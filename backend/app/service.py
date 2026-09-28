"""Fachlogik: Messungen annehmen, Zustände ableiten, Warnungen erzeugen, Auslastung aggregieren."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .anomaly import DetectorParams, MlDetector, features_at, rule_decision
from .config import Settings
from .db import Database
from .schemas import MeasurementIn

log = logging.getLogger(__name__)

# Wie lange eine nicht quittierte Warnung öffentlich angezeigt wird.
ALERT_DISPLAY_S = 300


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


class UnknownSlotError(ValueError):
    pass


@dataclass
class IngestResult:
    stored: bool
    duplicate: bool = False
    alert_created: bool = False


class StationService:
    def __init__(self, settings: Settings, db: Database, clock: Callable[[], float] = time.time):
        self.s = settings
        self.db = db
        self.clock = clock
        self.params = DetectorParams(
            window_s=settings.window_s,
            peak_threshold=settings.peak_threshold,
            min_peaks=settings.min_peaks,
            grace_period_s=settings.grace_period_s,
        )
        self.ml = MlDetector(settings.model_path)

    # ------------------------------------------------------------------ ingest
    def ingest(self, m: MeasurementIn) -> IngestResult:
        if m.station_id != self.s.station_id or m.slot_id not in self.s.slot_ids():
            raise UnknownSlotError("unbekannte Station oder unbekannter Platz")

        now = self.clock()
        age_s = m.age_ms / 1000.0
        # Server-Zeitstempel (Plan 5.1). Gepufferte Nachrichten melden ihr Alter.
        t = now - age_s
        prev = self._latest(m.slot_id)

        cur = self.db.execute(
            "INSERT OR IGNORE INTO measurement "
            "(station_id, slot_id, sequence, server_time, occupied, vibration_score, sensor_state, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                m.station_id,
                m.slot_id,
                m.sequence,
                t,
                None if m.occupied is None else int(m.occupied),
                m.vibration_score,
                m.sensor_state,
                m.source,
            ),
        )
        if cur.rowcount == 0:
            return IngestResult(stored=False, duplicate=True)

        result = IngestResult(stored=True)

        # Sensorfehler als Ereignis melden (nur beim Übergang, nicht bei jeder Wiederholung).
        if m.sensor_state == "error" and (prev is None or prev["sensor_state"] != "error"):
            self._create_event(m.slot_id, "sensor_fault", "info", None, t, m.source, None)

        # Auffällige Bewegung nur an belegten Plätzen und nur für frische Daten bewerten:
        # alte, nachträglich übertragene Daten dürfen keinen Live-Alarm auslösen.
        if m.sensor_state == "ok" and m.occupied and m.vibration_score > 0 and age_s <= self.s.window_s:
            result.alert_created = self._evaluate_movement(m.slot_id, t, m.source)
        return result

    def _evaluate_movement(self, slot_id: str, t: float, source: str) -> bool:
        rows = self.db.query(
            "SELECT server_time, occupied, vibration_score FROM measurement "
            "WHERE station_id = ? AND slot_id = ? AND sensor_state = 'ok' AND server_time <= ? "
            "ORDER BY server_time DESC, id DESC LIMIT 2000",
            (self.s.station_id, slot_id, t),
        )
        history = [(r[0], bool(r[1]), r[2]) for r in reversed(rows)]
        features = features_at(history, len(history) - 1, self.params)

        decisions = {"rule": rule_decision(features, self.params), "ml": self.ml.decision(features, self.params)}
        visible = self.s.alert_source
        shadow = "ml" if visible == "rule" else "rule"
        detail = json.dumps({"rule": decisions["rule"], "ml": decisions["ml"], "features": features})

        created = False
        if decisions[visible] and not self._in_cooldown(slot_id, t, "warning"):
            self._create_event(slot_id, "unusual_movement", "warning", visible, t, source, detail)
            created = True
        # Das jeweils andere Verfahren läuft "im Schatten" mit – für den Vergleich (Test T15),
        # erscheint aber nicht als öffentliche Warnung.
        if decisions[shadow] and not self._in_cooldown(slot_id, t, "shadow"):
            self._create_event(slot_id, "unusual_movement", "shadow", shadow, t, source, detail)
        return created

    def _in_cooldown(self, slot_id: str, t: float, severity: str) -> bool:
        row = self.db.query(
            "SELECT MAX(occurred_at) FROM event WHERE station_id = ? AND slot_id = ? "
            "AND kind = 'unusual_movement' AND severity = ?",
            (self.s.station_id, slot_id, severity),
        )[0][0]
        return row is not None and t - row < self.s.cooldown_s

    def _create_event(self, slot_id, kind, severity, detector, t, source, detail) -> None:
        self.db.execute(
            "INSERT INTO event (station_id, slot_id, kind, severity, detector, detail, occurred_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.s.station_id, slot_id, kind, severity, detector, detail, t, source),
        )
        log.info("Ereignis %s/%s an Platz %s (%s)", kind, severity, slot_id, detector or "-")

    def _latest(self, slot_id: str):
        rows = self.db.query(
            "SELECT * FROM measurement WHERE station_id = ? AND slot_id = ? ORDER BY server_time DESC, id DESC LIMIT 1",
            (self.s.station_id, slot_id),
        )
        return rows[0] if rows else None

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        now = self.clock()
        slots = []
        any_simulated = False
        for sc in self.s.slots:
            m = self._latest(sc.id)
            if m is None:
                state, reason, last = "unknown", "no_data", None
            elif now - m["server_time"] > self.s.stale_after_s:
                state, reason, last = "unknown", "stale", m["server_time"]
            elif m["sensor_state"] != "ok" or m["occupied"] is None:
                state, reason, last = "unknown", "sensor_error", m["server_time"]
            else:
                state = "occupied" if m["occupied"] else "free"
                reason, last = None, m["server_time"]
            if m is not None and m["source"] == "simulated" and state != "unknown":
                any_simulated = True

            alert = self.db.query(
                "SELECT id, occurred_at, detector, source FROM event WHERE station_id = ? AND slot_id = ? "
                "AND kind = 'unusual_movement' AND severity = 'warning' AND acknowledged_at IS NULL "
                "AND occurred_at >= ? ORDER BY occurred_at DESC LIMIT 1",
                (self.s.station_id, sc.id, now - ALERT_DISPLAY_S),
            )
            slots.append(
                {
                    "slot_id": sc.id,
                    "label": sc.label,
                    "position": sc.position,
                    "state": state,
                    "unknown_reason": reason,
                    "last_update": iso(last),
                    "age_s": None if last is None else round(now - last, 1),
                    "alert": None
                    if not alert
                    else {
                        "kind": "unusual_movement",
                        "occurred_at": iso(alert[0]["occurred_at"]),
                        "detector": alert[0]["detector"],
                        "simulated": alert[0]["source"] == "simulated",
                    },
                }
            )

        free = [s for s in slots if s["state"] == "free"]
        # Transparente Regel: erster freier Platz vom Eingang aus (kleinste Position).
        recommendation = free[0]["slot_id"] if free else None
        return {
            "station_id": self.s.station_id,
            "display_name": self.s.station_name,
            "server_time": iso(now),
            "stale_after_s": self.s.stale_after_s,
            "poll_interval_s": self.s.ui_poll_interval_s,
            "free_count": len(free),
            "known_count": sum(1 for s in slots if s["state"] != "unknown"),
            "total": len(slots),
            "recommendation": recommendation,
            "recommendation_rule": "first_free_by_position",
            "slots": slots,
            "simulated_data": any_simulated,
            "ai": self.ai_status(),
        }

    def ai_status(self) -> dict:
        return {
            "visible_detector": self.s.alert_source,
            "model_available": self.ml.available,
            "model_info": self.ml.meta if self.ml.available else None,
            "model_error": self.ml.error,
        }

    # ------------------------------------------------------------------ events
    def events(self, limit: int = 100, include_shadow: bool = False) -> list[dict]:
        sql = "SELECT * FROM event WHERE station_id = ?"
        if not include_shadow:
            sql += " AND severity != 'shadow'"
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        rows = self.db.query(sql, (self.s.station_id, limit))
        return [
            {
                "id": r["id"],
                "slot_id": r["slot_id"],
                "kind": r["kind"],
                "severity": r["severity"],
                "detector": r["detector"],
                "detail": json.loads(r["detail"]) if r["detail"] else None,
                "occurred_at": iso(r["occurred_at"]),
                "acknowledged_at": iso(r["acknowledged_at"]),
                "simulated": r["source"] == "simulated",
            }
            for r in rows
        ]

    def acknowledge(self, event_id: int, client: str) -> bool | None:
        """True = quittiert, False = war schon quittiert, None = nicht gefunden."""
        rows = self.db.query(
            "SELECT acknowledged_at FROM event WHERE id = ? AND station_id = ?", (event_id, self.s.station_id)
        )
        if not rows:
            return None
        if rows[0][0] is not None:
            return False
        now = self.clock()
        self.db.execute("UPDATE event SET acknowledged_at = ? WHERE id = ?", (now, event_id))
        self.db.audit(now, "event_ack", client, f"event={event_id}")
        return True

    # ------------------------------------------------------------------ summary
    def occupancy_summary(self, hours: int = 24) -> dict:
        """Zeitgewichtete Belegung je Stunde und Platz (Plan 9.3).

        Jedes Intervall zwischen zwei Messungen zählt mit dem Zustand der ersten Messung,
        höchstens aber stale_after_s lang – Lücken werden nicht als "frei" gezählt.
        """
        now = self.clock()
        start = (int(now // 3600) - hours + 1) * 3600
        buckets = {start + i * 3600: {sc.id: [0.0, 0.0] for sc in self.s.slots} for i in range(hours)}
        simulated = live = False
        n = 0
        for sc in self.s.slots:
            rows = self.db.query(
                "SELECT server_time, occupied, sensor_state, source FROM measurement "
                "WHERE station_id = ? AND slot_id = ? AND server_time >= ? ORDER BY server_time",
                (self.s.station_id, sc.id, start - self.s.stale_after_s),
            )
            n += len(rows)
            for i, r in enumerate(rows):
                if r["sensor_state"] != "ok" or r["occupied"] is None:
                    continue
                if r["source"] == "simulated":
                    simulated = True
                else:
                    live = True
                seg_start = max(r["server_time"], start)
                nxt = rows[i + 1]["server_time"] if i + 1 < len(rows) else now
                seg_end = min(nxt, r["server_time"] + self.s.stale_after_s, now)
                while seg_start < seg_end:
                    b = int(seg_start // 3600) * 3600
                    part_end = min(seg_end, b + 3600)
                    if b in buckets:
                        acc = buckets[b][sc.id]
                        acc[1] += part_end - seg_start
                        if r["occupied"]:
                            acc[0] += part_end - seg_start
                    seg_start = part_end

        out = []
        for b, per_slot in buckets.items():
            slot_vals = {sid: (round(o / k, 3) if k > 0 else None) for sid, (o, k) in per_slot.items()}
            known = [v for v in slot_vals.values() if v is not None]
            out.append(
                {
                    "hour_start": iso(b),
                    "occupancy": slot_vals,
                    "avg_occupied_slots": round(sum(known), 2) if known else None,
                }
            )
        return {
            "station_id": self.s.station_id,
            "hours": hours,
            "buckets": out,
            "measurement_count": n,
            "contains_simulated": simulated,
            "contains_live": live,
        }
