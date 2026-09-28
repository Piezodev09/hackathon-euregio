"""Fachlogik je Station: Messungen annehmen, Zustände ableiten, Warnungen, Auslastung, Aufbewahrung."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .anomaly import DetectorParams, features_at, rule_decision
from .core import Core
from .plans import get_plan
from .schemas import MeasurementIn
from .security import new_id

log = logging.getLogger(__name__)

# Wie lange eine nicht quittierte Warnung angezeigt wird.
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


class Monitoring:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db
        s = core.s
        self.params = DetectorParams(
            window_s=s.window_s, peak_threshold=s.peak_threshold, min_peaks=s.min_peaks, grace_period_s=s.grace_period_s
        )

    def slots(self, station_id: str) -> list[sqlite3.Row]:
        return self.db.all("SELECT * FROM slot WHERE station_id = ? ORDER BY position, key", (station_id,))

    def _ml_allowed(self, station: sqlite3.Row) -> bool:
        tenant = self.db.one("SELECT plan FROM tenant WHERE id = ?", (station["tenant_id"],))
        return bool(tenant and get_plan(tenant["plan"]).ml_enabled)

    # ------------------------------------------------------------------ ingest
    def ingest(self, station: sqlite3.Row, m: MeasurementIn) -> IngestResult:
        slot = self.db.one("SELECT * FROM slot WHERE station_id = ? AND key = ?", (station["id"], m.slot_id))
        if slot is None:
            raise UnknownSlotError("unknown_slot")
        now = self.core.clock()
        age_s = m.age_ms / 1000.0
        t = now - age_s  # Server-Zeitstempel; gepufferte Nachrichten melden ihr Alter
        prev = self._latest(slot["id"])
        cur = self.db.execute(
            "INSERT OR IGNORE INTO measurement "
            "(station_id, slot_id, sequence, server_time, occupied, vibration_score, sensor_state, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (station["id"], slot["id"], m.sequence, t, None if m.occupied is None else int(m.occupied),
             m.vibration_score, m.sensor_state, m.source),
        )
        if cur.rowcount == 0:
            return IngestResult(stored=False, duplicate=True)
        result = IngestResult(stored=True)
        if m.sensor_state == "error" and (prev is None or prev["sensor_state"] != "error"):
            self._create_event(station, slot, "sensor_fault", "info", None, t, m.source, None)
        # Alte, nachträglich übertragene Daten dürfen keinen Live-Alarm auslösen.
        if m.sensor_state == "ok" and m.occupied and m.vibration_score > 0 and age_s <= self.core.s.window_s:
            result.alert_created = self._evaluate_movement(station, slot, t, m.source)
        return result

    def _evaluate_movement(self, station, slot, t: float, source: str) -> bool:
        rows = self.db.all(
            "SELECT server_time, occupied, vibration_score FROM measurement "
            "WHERE slot_id = ? AND sensor_state = 'ok' AND server_time <= ? ORDER BY server_time DESC, id DESC LIMIT 2000",
            (slot["id"], t),
        )
        history = [(r[0], bool(r[1]), r[2]) for r in reversed(rows)]
        features = features_at(history, len(history) - 1, self.params)
        ml_allowed = self._ml_allowed(station)
        decisions = {
            "rule": rule_decision(features, self.params),
            "ml": self.core.ml.decision(features, self.params) if ml_allowed else None,
        }
        visible = station["alert_source"] if ml_allowed else "rule"
        shadow = "ml" if visible == "rule" else "rule"
        detail = json.dumps({"rule": decisions["rule"], "ml": decisions["ml"], "features": features})
        created = False
        if decisions[visible] and not self._in_cooldown(slot["id"], t, "warning"):
            self._create_event(station, slot, "unusual_movement", "warning", visible, t, source, detail)
            created = True
        # Das andere Verfahren läuft "im Schatten" mit – für den Vergleich, nicht öffentlich.
        if decisions[shadow] and not self._in_cooldown(slot["id"], t, "shadow"):
            self._create_event(station, slot, "unusual_movement", "shadow", shadow, t, source, detail)
        return created

    def _in_cooldown(self, slot_id: str, t: float, severity: str) -> bool:
        last = self.db.scalar(
            "SELECT MAX(occurred_at) FROM event WHERE slot_id = ? AND kind = 'unusual_movement' AND severity = ?",
            (slot_id, severity),
        )
        return last is not None and t - last < self.core.s.cooldown_s

    def _create_event(self, station, slot, kind, severity, detector, t, source, detail) -> None:
        self.db.execute(
            "INSERT INTO event (id, tenant_id, station_id, slot_id, kind, severity, detector, detail, occurred_at, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id("evt"), station["tenant_id"], station["id"], slot["id"], kind, severity, detector, detail, t, source),
        )

    def _latest(self, slot_id: str):
        return self.db.one(
            "SELECT * FROM measurement WHERE slot_id = ? ORDER BY server_time DESC, id DESC LIMIT 1", (slot_id,)
        )

    # ------------------------------------------------------------------ status
    def status(self, station: sqlite3.Row, public: bool = False) -> dict:
        now = self.core.clock()
        stale = self.core.s.stale_after_s
        out_slots = []
        any_simulated = False
        for sl in self.slots(station["id"]):
            m = self._latest(sl["id"])
            if m is None:
                state, reason, last = "unknown", "no_data", None
            elif now - m["server_time"] > stale:
                state, reason, last = "unknown", "stale", m["server_time"]
            elif m["sensor_state"] != "ok" or m["occupied"] is None:
                state, reason, last = "unknown", "sensor_error", m["server_time"]
            else:
                state = "occupied" if m["occupied"] else "free"
                reason, last = None, m["server_time"]
            if m is not None and m["source"] == "simulated" and state != "unknown":
                any_simulated = True
            alert = self.db.one(
                "SELECT id, occurred_at, detector, source FROM event WHERE slot_id = ? AND kind = 'unusual_movement' "
                "AND severity = 'warning' AND acknowledged_at IS NULL AND occurred_at >= ? ORDER BY occurred_at DESC LIMIT 1",
                (sl["id"], now - ALERT_DISPLAY_S),
            )
            entry = {
                "slot_id": sl["key"],
                "label": sl["label"],
                "position": sl["position"],
                "state": state,
                "unknown_reason": reason,
                "last_update": iso(last),
                "age_s": None if last is None else round(now - last, 1),
                "alert": None
                if not alert
                else {"kind": "unusual_movement", "occurred_at": iso(alert["occurred_at"]), "simulated": alert["source"] == "simulated"},
            }
            if alert and not public:
                entry["alert"].update({"id": alert["id"], "detector": alert["detector"]})
            out_slots.append(entry)

        free = [s for s in out_slots if s["state"] == "free"]
        body = {
            "station_id": station["id"],
            "display_name": station["name"],
            "location": station["location"],
            "server_time": iso(now),
            "stale_after_s": stale,
            "poll_interval_s": self.core.s.ui_poll_interval_s,
            "free_count": len(free),
            "known_count": sum(1 for s in out_slots if s["state"] != "unknown"),
            "total": len(out_slots),
            # Transparente Regel: erster freier Platz in Positionsreihenfolge (keine KI).
            "recommendation": free[0]["slot_id"] if free else None,
            "recommendation_rule": "first_free_by_position",
            "slots": out_slots,
            "simulated_data": any_simulated,
        }
        if not public:
            ml_allowed = self._ml_allowed(station)
            body["ai"] = {
                "visible_detector": station["alert_source"] if ml_allowed else "rule",
                "plan_allows_ml": ml_allowed,
                "model_available": self.core.ml.available,
                "model_trained_on_simulated_data": bool(self.core.ml.meta.get("trained_on_simulated_data"))
                if self.core.ml.available
                else None,
                "model_error": self.core.ml.error,
            }
        return body

    # ------------------------------------------------------------------ events
    def events(self, tenant_id: str, station_id: str | None = None, limit: int = 100,
               include_shadow: bool = False, open_only: bool = False) -> list[dict]:
        sql = ("SELECT e.*, s.key AS slot_key, st.name AS station_name FROM event e "
               "JOIN slot s ON s.id = e.slot_id JOIN station st ON st.id = e.station_id WHERE e.tenant_id = ?")
        params: list = [tenant_id]
        if station_id:
            sql += " AND e.station_id = ?"
            params.append(station_id)
        if not include_shadow:
            sql += " AND e.severity != 'shadow'"
        if open_only:
            sql += " AND e.acknowledged_at IS NULL AND e.severity = 'warning'"
        sql += " ORDER BY e.occurred_at DESC LIMIT ?"
        params.append(limit)
        return [
            {
                "id": r["id"],
                "station_id": r["station_id"],
                "station_name": r["station_name"],
                "slot_id": r["slot_key"],
                "kind": r["kind"],
                "severity": r["severity"],
                "detector": r["detector"],
                "detail": json.loads(r["detail"]) if r["detail"] else None,
                "occurred_at": iso(r["occurred_at"]),
                "acknowledged_at": iso(r["acknowledged_at"]),
                "acknowledged_by": r["acknowledged_by"],
                "simulated": r["source"] == "simulated",
            }
            for r in self.db.all(sql, tuple(params))
        ]

    def acknowledge(self, tenant_id: str, event_id: str, actor: str) -> bool | None:
        row = self.db.one("SELECT acknowledged_at FROM event WHERE id = ? AND tenant_id = ?", (event_id, tenant_id))
        if row is None:
            return None
        if row[0] is not None:
            return False
        self.db.execute(
            "UPDATE event SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ? AND tenant_id = ?",
            (self.core.clock(), actor, event_id, tenant_id),
        )
        return True

    # ------------------------------------------------------------------ Auslastung
    def occupancy_summary(self, station: sqlite3.Row, hours: int = 24) -> dict:
        """Zeitgewichtete Belegung je Stunde und Platz. Lücken zählen nicht als "frei"."""
        now = self.core.clock()
        stale = self.core.s.stale_after_s
        start = (int(now // 3600) - hours + 1) * 3600
        slots = self.slots(station["id"])
        buckets = {start + i * 3600: {sl["key"]: [0.0, 0.0] for sl in slots} for i in range(hours)}
        simulated = live = False
        n = 0
        for sl in slots:
            rows = self.db.all(
                "SELECT server_time, occupied, sensor_state, source FROM measurement "
                "WHERE slot_id = ? AND server_time >= ? ORDER BY server_time",
                (sl["id"], start - stale),
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
                seg_end = min(nxt, r["server_time"] + stale, now)
                while seg_start < seg_end:
                    b = int(seg_start // 3600) * 3600
                    part_end = min(seg_end, b + 3600)
                    if b in buckets:
                        acc = buckets[b][sl["key"]]
                        acc[1] += part_end - seg_start
                        if r["occupied"]:
                            acc[0] += part_end - seg_start
                    seg_start = part_end
        out = []
        for b, per_slot in buckets.items():
            vals = {k: (round(o / t, 3) if t > 0 else None) for k, (o, t) in per_slot.items()}
            known = [v for v in vals.values() if v is not None]
            out.append({"hour_start": iso(b), "occupancy": vals, "avg_occupied_slots": round(sum(known), 2) if known else None})
        return {"station_id": station["id"], "hours": hours, "buckets": out, "measurement_count": n,
                "contains_simulated": simulated, "contains_live": live}

    # ------------------------------------------------------------------ Aufbewahrung
    def purge(self) -> dict:
        """Löscht Daten nach Tarif-Aufbewahrung sowie abgelaufene Sessions/Tokens (Datensparsamkeit)."""
        now = self.core.clock()
        deleted = {"measurements": 0, "events": 0}
        for t in self.db.all("SELECT id, plan FROM tenant"):
            cutoff = now - get_plan(t["plan"]).retention_days * 86400
            deleted["measurements"] += self.db.execute(
                "DELETE FROM measurement WHERE server_time < ? AND station_id IN (SELECT id FROM station WHERE tenant_id = ?)",
                (cutoff, t["id"]),
            ).rowcount
            deleted["events"] += self.db.execute(
                "DELETE FROM event WHERE tenant_id = ? AND occurred_at < ?", (t["id"], cutoff)
            ).rowcount
        self.db.execute("DELETE FROM session WHERE expires_at < ? OR last_seen_at < ?", (now, now - self.core.s.session_idle_s))
        self.db.execute("DELETE FROM auth_token WHERE expires_at < ?", (now - 86400,))
        self.db.execute("DELETE FROM audit_log WHERE at < ?", (now - self.core.s.audit_retention_s,))
        return deleted
