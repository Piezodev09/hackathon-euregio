"""Anlagen: mehrere Stellplätze nebeneinander mit gemeinsamer Übersicht („3 von 10 frei“) und Warteliste.

Frei zählt nur ein Stellplatz mit gültiger, aktueller Messung „frei“, ohne Wartung, geöffnet und nicht reserviert.
STATUS UNBEKANNT zählt nie als frei.
"""

from __future__ import annotations

import sqlite3

from .billing import iso


class Sites:
    def __init__(self, core, monitoring):
        self.core = core
        self.db = core.db
        self.monitoring = monitoring

    def stations(self, site_id: str) -> list[sqlite3.Row]:
        return self.db.all("SELECT * FROM station WHERE site_id = ? ORDER BY name, created_at", (site_id,))

    @staticmethod
    def category(st: dict) -> str:
        """Eine Kategorie je Stellplatz: available | occupied | reserved | closed | unknown."""
        if st["maintenance"] or st["closed"]:
            return "closed"
        if st["state"] == "free":
            return "available"
        return st["state"] if st["state"] in ("occupied", "reserved") else "unknown"

    def summary(self, site: sqlite3.Row, public: bool = True) -> dict:
        stalls, counts, simulated = [], {"available": 0, "occupied": 0, "reserved": 0, "closed": 0, "unknown": 0}, False
        for st in self.stations(site["id"]):
            s = self.monitoring.status(st, public=True)
            cat = self.category(s)
            counts[cat] += 1
            simulated = simulated or s["simulated_data"]
            stalls.append({"station_id": st["id"], "name": st["name"], "state": s["state"], "category": cat,
                           "unknown_reason": s["unknown_reason"], "maintenance": s["maintenance"], "closed": bool(s["closed"]),
                           "reservation_remaining_s": (s["reservation"] or {}).get("remaining_s"),
                           "simulated": s["simulated_data"]})
        waiting = self.db.scalar("SELECT COUNT(*) FROM waitlist WHERE site_id = ? AND status = 'waiting'", (site["id"],)) or 0
        out = {"id": site["id"], "name": site["name"], "location": site["location"], "total": len(stalls),
               "free": counts["available"], "counts": counts, "stalls": stalls, "simulated": simulated,
               "waitlist_enabled": bool(site["waitlist_enabled"]), "waiting": waiting,
               "server_time": iso(self.core.clock()), "stale_after_s": self.core.s.stale_after_s,
               "poll_interval_s": self.core.s.ui_poll_interval_s}
        if not public:
            out.update(display_enabled=bool(site["display_enabled"]), hold_minutes=site["hold_minutes"],
                       created_at=iso(site["created_at"]))
        return out

    def site_of(self, station_id: str) -> sqlite3.Row | None:
        return self.db.one("SELECT s.* FROM site s JOIN station st ON st.site_id = s.id WHERE st.id = ?", (station_id,))
