"""Störungen je Stellplatz für die öffentliche Status-Seite: Gateway offline, Sensorfehler, Wartung.

Gateway offline/online kommt aus den Ereignissen von notify.check_devices, Sensorfehler aus `sensor.fault` (beendet,
sobald wieder eine gültige Messung eintrifft – geprüft in der Minutenschleife), Wartung aus den Stellplatz-Einstellungen.
"""

from __future__ import annotations

import sqlite3

from .billing import iso
from .security import new_id

WINDOW_DAYS = 30
STATES = ("ok", "fault", "maintenance", "no_data")


class Incidents:
    def __init__(self, core, monitoring):
        self.core = core
        self.db = core.db
        self.monitoring = monitoring
        core.listeners.append(self.on_event)

    def on_event(self, kind: str, tenant_id: str, data: dict) -> None:
        sid = data.get("station_id")
        if not sid:
            return
        source = "simulated" if data.get("simulated") else "live"
        if kind == "gateway.offline":
            self.open(tenant_id, sid, "gateway_offline", data.get("device_id"), source)
        elif kind == "gateway.online":
            self.close(sid, "gateway_offline", data.get("device_id"))
        elif kind == "sensor.fault":
            self.open(tenant_id, sid, "sensor_fault", None, source)

    def open(self, tenant_id: str, station_id: str, kind: str, device_id: str | None = None, source: str = "live") -> None:
        q = "SELECT 1 FROM incident WHERE station_id = ? AND kind = ? AND ended_at IS NULL"
        args: tuple = (station_id, kind)
        if device_id:
            q += " AND device_id = ?"
            args += (device_id,)
        if self.db.one(q, args):
            return
        self.db.execute("INSERT INTO incident (id, tenant_id, station_id, device_id, kind, started_at, source) VALUES (?,?,?,?,?,?,?)",
                        (new_id("inc"), tenant_id, station_id, device_id, kind, self.core.clock(), source))

    def close(self, station_id: str, kind: str, device_id: str | None = None, at: float | None = None) -> None:
        q = "UPDATE incident SET ended_at = ? WHERE station_id = ? AND kind = ? AND ended_at IS NULL"
        args: tuple = (at or self.core.clock(), station_id, kind)
        if device_id:
            q += " AND device_id = ?"
            args += (device_id,)
        self.db.execute(q, args)

    def tick(self) -> int:
        """Sensorfehler beenden, sobald wieder eine gültige Messung vorliegt (Zeitpunkt = erste gültige Messung)."""
        n = 0
        for inc in self.db.all("SELECT * FROM incident WHERE kind = 'sensor_fault' AND ended_at IS NULL"):
            ok = self.db.one("SELECT MIN(server_time) AS t FROM measurement WHERE station_id = ? AND server_time > ? "
                             "AND sensor_state = 'ok' AND occupied IS NOT NULL", (inc["station_id"], inc["started_at"]))
            if ok and ok["t"]:
                self.db.execute("UPDATE incident SET ended_at = ? WHERE id = ?", (ok["t"], inc["id"]))
                n += 1
        return n

    # ------------------------------------------------------------------ Ausgabe
    def state(self, station: sqlite3.Row, open_kinds: set[str]) -> str:
        if station["maintenance"]:
            return "maintenance"
        if open_kinds & {"gateway_offline", "sensor_fault"}:
            return "fault"
        s = self.monitoring.status(station, public=True)
        if s["state"] == "unknown":
            return "fault" if s["unknown_reason"] == "sensor_error" else "no_data"
        return "ok"

    def out(self, r: sqlite3.Row, names: dict, public: bool) -> dict:
        now = self.core.clock()
        body = {"station_name": names.get(r["station_id"], ""), "kind": r["kind"], "started_at": iso(r["started_at"]),
                "ended_at": iso(r["ended_at"]), "duration_s": round((r["ended_at"] or now) - r["started_at"]),
                "note": r["note"], "simulated": r["source"] == "simulated"}
        if not public:
            body.update(id=r["id"], station_id=r["station_id"], device_id=r["device_id"])
        return body

    def report(self, tenant: sqlite3.Row, public: bool = True) -> dict:
        now = self.core.clock()
        since = now - WINDOW_DAYS * 86400
        stations = self.db.all("SELECT * FROM station WHERE tenant_id = ? ORDER BY name", (tenant["id"],))
        names = {s["id"]: s["name"] for s in stations}
        rows = self.db.all("SELECT * FROM incident WHERE tenant_id = ? AND (ended_at IS NULL OR ended_at >= ?) "
                           "ORDER BY started_at DESC", (tenant["id"], since))
        stalls = []
        for st in stations:
            mine = [r for r in rows if r["station_id"] == st["id"]]
            open_kinds = {r["kind"] for r in mine if r["ended_at"] is None}
            period = now - max(since, st["created_at"])
            # Überlappende Störungen nur einmal zählen (Wartung zählt nicht als Ausfall)
            spans = sorted((max(r["started_at"], since), r["ended_at"] or now) for r in mine if r["kind"] != "maintenance")
            down, cur_a, cur_b = 0.0, None, None
            for a, b in spans:
                if cur_b is None or a > cur_b:
                    if cur_b is not None:
                        down += cur_b - cur_a
                    cur_a, cur_b = a, b
                else:
                    cur_b = max(cur_b, b)
            if cur_b is not None:
                down += cur_b - cur_a
            stalls.append({"name": st["name"], "state": self.state(st, open_kinds),
                           "availability": round(max(0.0, 1 - down / period), 4) if period > 0 else None,
                           **({"station_id": st["id"]} if not public else {})})
        return {"org": tenant["name"], "stalls": stalls, "window_days": WINDOW_DAYS, "server_time": iso(now),
                "open": [self.out(r, names, public) for r in rows if r["ended_at"] is None],
                "history": [self.out(r, names, public) for r in rows if r["ended_at"] is not None][:100],
                "simulated": any(r["source"] == "simulated" for r in rows)}
