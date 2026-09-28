"""Fachlogik je Station (= ein Stellplatz): Messungen annehmen, Zustand ableiten, Warnungen, Auslastung, Aufbewahrung."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .anomaly import DetectorParams, features_at, rule_decision
from .core import Core
from .hours import closed_info, load_hours
from .plans import get_plan
from .schemas import MeasurementIn
from .security import new_id

log = logging.getLogger(__name__)

# Wie lange eine nicht quittierte Warnung angezeigt wird.
ALERT_DISPLAY_S = 300
# Interne Kennung des einen Stellplatzes einer Station.
STALL_KEY = "A"


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
    event_id: str | None = None


class Monitoring:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db
        s = core.s
        self.params = DetectorParams(
            window_s=s.window_s, peak_threshold=s.peak_threshold, min_peaks=s.min_peaks, grace_period_s=s.grace_period_s
        )

    def stall(self, station_id: str) -> sqlite3.Row:
        """Der eine Stellplatz der Station (wird beim Anlegen der Station erzeugt)."""
        row = self.db.one("SELECT * FROM slot WHERE station_id = ? ORDER BY position LIMIT 1", (station_id,))
        if row is None:
            row_id = new_id("sl")
            self.db.execute("INSERT INTO slot (id, station_id, key, label, position) VALUES (?,?,?,?,1)",
                            (row_id, station_id, STALL_KEY, "Stellplatz"))
            row = self.db.one("SELECT * FROM slot WHERE id = ?", (row_id,))
        return row

    def _ml_allowed(self, station: sqlite3.Row) -> bool:
        tenant = self.db.one("SELECT plan FROM tenant WHERE id = ?", (station["tenant_id"],))
        return bool(tenant and get_plan(tenant["plan"]).ml_enabled)

    # ------------------------------------------------------------------ ingest
    def ingest(self, station: sqlite3.Row, m: MeasurementIn) -> IngestResult:
        if m.slot_id is not None and m.slot_id != STALL_KEY:
            raise UnknownSlotError("unknown_slot")
        slot = self.stall(station["id"])
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
        live = age_s <= self.core.s.window_s
        if live and m.sensor_state == "ok" and prev is not None and prev["sensor_state"] == "ok" \
                and prev["occupied"] is not None and bool(prev["occupied"]) != bool(m.occupied):
            self.core.emit("stall.changed", station["tenant_id"], {
                "station_id": station["id"], "station_name": station["name"],
                "state": "occupied" if m.occupied else "free", "simulated": m.source == "simulated"})
        # Wartungsmodus: keine Warnungen und keine Sensorfehler-Meldungen (Technik arbeitet am Stellplatz).
        if station["maintenance"]:
            return result
        if m.sensor_state == "error" and (prev is None or prev["sensor_state"] != "error"):
            self._create_event(station, slot, "sensor_fault", "info", None, t, m.source, None)
        # Alte, nachträglich übertragene Daten dürfen keinen Live-Alarm auslösen.
        if m.sensor_state == "ok" and m.occupied and m.vibration_score > 0 and live:
            result.alert_created = self._evaluate_movement(station, slot, t, m.source)
            if result.alert_created:
                result.event_id = self.db.scalar(
                    "SELECT id FROM event WHERE slot_id = ? AND kind = 'unusual_movement' AND severity = 'warning' "
                    "ORDER BY occurred_at DESC LIMIT 1", (slot["id"],))
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

    def _create_event(self, station, slot, kind, severity, detector, t, source, detail) -> str:
        eid = new_id("evt")
        self.db.execute(
            "INSERT INTO event (id, tenant_id, station_id, slot_id, kind, severity, detector, detail, occurred_at, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, station["tenant_id"], station["id"], slot["id"], kind, severity, detector, detail, t, source),
        )
        if severity != "shadow":
            name = {"unusual_movement": "alert.created", "user_report": "problem.reported", "sensor_fault": "sensor.fault"}.get(kind)
            if name:
                info = json.loads(detail) if detail and kind == "user_report" else {}
                self.core.emit(name, station["tenant_id"], {
                    "event_id": eid, "station_id": station["id"], "station_name": station["name"], "kind": kind,
                    "occurred_at": iso(t), "simulated": source == "simulated",
                    **({"category": info.get("category"), "text": info.get("text", "")} if info else {})})
        return eid

    def _latest(self, slot_id: str):
        return self.db.one(
            "SELECT * FROM measurement WHERE slot_id = ? ORDER BY server_time DESC, id DESC LIMIT 1", (slot_id,)
        )

    # ------------------------------------------------------------------ status
    def status(self, station: sqlite3.Row, public: bool = False) -> dict:
        """Zustand des Stellplatzes. "free"/"occupied" nur aus einer gültigen, aktuellen Messung – sonst "unknown"."""
        now = self.core.clock()
        stale = self.core.s.stale_after_s
        sl = self.stall(station["id"])
        m = self._latest(sl["id"])
        if m is None:
            state, reason, last = "unknown", "no_data", None
        elif now - m["server_time"] > stale or m["server_time"] > now + 5:
            state, reason, last = "unknown", "stale", m["server_time"]
        elif m["sensor_state"] != "ok" or m["occupied"] is None:
            state, reason, last = "unknown", "sensor_error", m["server_time"]
        else:
            state = "occupied" if m["occupied"] else "free"
            reason, last = None, m["server_time"]
        alert = self.db.one(
            "SELECT id, occurred_at, detector, source FROM event WHERE slot_id = ? AND kind = 'unusual_movement' "
            "AND severity = 'warning' AND acknowledged_at IS NULL AND occurred_at >= ? ORDER BY occurred_at DESC LIMIT 1",
            (sl["id"], now - ALERT_DISPLAY_S),
        )
        alert_out = None
        if alert:
            alert_out = {"kind": "unusual_movement", "occurred_at": iso(alert["occurred_at"]), "simulated": alert["source"] == "simulated"}
            if not public:
                alert_out.update({"id": alert["id"], "detector": alert["detector"]})
        body = {
            "station_id": station["id"],
            "display_name": station["name"],
            "location": station["location"],
            "server_time": iso(now),
            "stale_after_s": stale,
            "poll_interval_s": self.core.s.ui_poll_interval_s,
            "state": state,
            "unknown_reason": reason,
            "last_update": iso(last),
            "age_s": None if last is None else round(now - last, 1),
            "alert": alert_out,
            # Kennzeichnung für die Oberfläche: die letzte Messung stammt aus dem Simulator.
            "simulated_data": m is not None and m["source"] == "simulated",
            "presence": state,
            "reservation": None,
            "closed": closed_info(self.db, station, now),
            "hours": load_hours(station["hours"]),
            "maintenance": bool(station["maintenance"]),
            "camera_active": bool(station["camera_enabled"]),
            "session": None,
            "last_tap": None,
        }
        reservations = getattr(self, "reservations", None)
        if reservations is not None:
            res = reservations.active(station["id"])
            if res is not None:
                body["reservation"] = reservations.out(res, public=public)
                # RESERVIERT nur bei sicher freiem Platz – unbekannt/belegt haben Vorrang.
                if state == "free":
                    body["state"] = "reserved"
        parking = getattr(self, "parking", None)
        if parking is not None:
            s = parking.open_session(station["id"])
            body["session"] = parking.session_out(s, public=public) if s else None
            body["last_tap"] = parking.last_tap(station["id"])
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
        sql = ("SELECT e.*, st.name AS station_name FROM event e "
               "JOIN station st ON st.id = e.station_id WHERE e.tenant_id = ?")
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
        """Zeitgewichteter Anteil "belegt" je Stunde. Lücken und unbekannte Zeiten zählen nicht als "frei"."""
        now = self.core.clock()
        stale = self.core.s.stale_after_s
        start = (int(now // 3600) - hours + 1) * 3600
        buckets = {start + i * 3600: [0.0, 0.0] for i in range(hours)}  # [belegt, bekannt] in Sekunden
        simulated = live = False
        rows = self.db.all(
            "SELECT server_time, occupied, sensor_state, source FROM measurement "
            "WHERE slot_id = ? AND server_time >= ? ORDER BY server_time",
            (self.stall(station["id"])["id"], start - stale),
        )
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
                    buckets[b][1] += part_end - seg_start
                    if r["occupied"]:
                        buckets[b][0] += part_end - seg_start
                seg_start = part_end
        out = [{"hour_start": iso(b), "occupancy": round(o / t, 3) if t > 0 else None, "known_s": round(t)}
               for b, (o, t) in buckets.items()]
        return {"station_id": station["id"], "hours": hours, "buckets": out, "measurement_count": len(rows),
                "contains_simulated": simulated, "contains_live": live}

    def occupancy_between(self, station: sqlite3.Row, start: float, end: float) -> dict:
        """Belegt-/bekannt-Sekunden im Zeitraum. Unbekannte Zeiten (keine/veraltete/fehlerhafte Daten) zählen nicht."""
        stale = self.core.s.stale_after_s
        end = min(end, self.core.clock())
        occ = known = 0.0
        simulated = False
        rows = self.db.all(
            "SELECT server_time, occupied, sensor_state, source FROM measurement "
            "WHERE slot_id = ? AND server_time >= ? AND server_time < ? ORDER BY server_time",
            (self.stall(station["id"])["id"], start - stale, end))
        for i, r in enumerate(rows):
            if r["sensor_state"] != "ok" or r["occupied"] is None:
                continue
            simulated = simulated or r["source"] == "simulated"
            nxt = rows[i + 1]["server_time"] if i + 1 < len(rows) else end
            a, b = max(r["server_time"], start), min(nxt, r["server_time"] + stale, end)
            if b > a:
                known += b - a
                if r["occupied"]:
                    occ += b - a
        return {"occupied_s": occ, "known_s": known, "period_s": max(0.0, end - start), "simulated": simulated}

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
