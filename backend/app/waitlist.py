"""Warteliste je Anlage.

Sind alle Stellplätze einer Anlage belegt, trägt man sich in der Karten-App ein. Wird ein Platz frei, bekommt der
erste Eintrag eine an seine Karte gebundene Reservierung (Haltezeit der Anlage, Standard 10 min) und eine
Push-Nachricht. Checkt die Karte ein, ist der Eintrag erledigt; läuft die Reservierung ab, ist der Nächste dran.
Frei heißt: gültige, aktuelle Messung „frei“, keine Wartung, geöffnet, nicht reserviert – nie „unbekannt“.
"""

from __future__ import annotations

import logging
import sqlite3

from .reservations import ReservationError
from .security import new_id

log = logging.getLogger("waitlist")
ACTIVE = ("waiting", "offered")


class WaitlistError(ValueError):
    pass


class Waitlist:
    def __init__(self, core, monitoring, sites, reservations, push):
        self.core = core
        self.db = core.db
        self.monitoring = monitoring
        self.sites = sites
        self.reservations = reservations
        self.push = push
        self._busy = False
        core.listeners.append(self.on_event)

    # ------------------------------------------------------------------ Ereignisse
    def on_event(self, kind: str, tenant_id: str, data: dict) -> None:
        if kind == "reservation.ended":
            entry = self.db.one("SELECT * FROM waitlist WHERE reservation_id = ? AND status = 'offered'", (data.get("reservation_id"),))
            if entry is not None:
                status = {"fulfilled": "done", "expired": "expired"}.get(data.get("reason"), "cancelled")
                self._finish(entry, status)
        if kind in ("stall.changed", "reservation.ended", "parking.checked_out") and data.get("station_id"):
            site = self.sites.site_of(data["station_id"])
            if site is not None and site["waitlist_enabled"]:
                self.offer(site)

    def tick(self) -> int:
        """Minutenschleife: auch Änderungen ohne Ereignis (z. B. Simulation, Ende einer Sperrzeit) berücksichtigen."""
        n = 0
        for site in self.db.all("SELECT s.* FROM site s WHERE s.waitlist_enabled = 1 AND EXISTS "
                                "(SELECT 1 FROM waitlist w WHERE w.site_id = s.id AND w.status = 'waiting')"):
            n += self.offer(site)
        return n

    # ------------------------------------------------------------------ Einträge
    def active_entry(self, card_id: str, site_id: str | None = None) -> sqlite3.Row | None:
        q = "SELECT * FROM waitlist WHERE card_id = ? AND status IN ('waiting','offered')"
        args: tuple = (card_id,)
        if site_id:
            q += " AND site_id = ?"
            args += (site_id,)
        return self.db.one(q + " ORDER BY created_at LIMIT 1", args)

    def position(self, entry: sqlite3.Row) -> int:
        if entry["status"] != "waiting":
            return 0
        return (self.db.scalar("SELECT COUNT(*) FROM waitlist WHERE site_id = ? AND status = 'waiting' AND created_at <= ?",
                               (entry["site_id"], entry["created_at"])) or 0)

    def join(self, site: sqlite3.Row, card: sqlite3.Row) -> sqlite3.Row:
        if not site["waitlist_enabled"]:
            raise WaitlistError("waitlist_disabled")
        if card["status"] != "active":
            raise WaitlistError("card_not_active")
        if self.active_entry(card["id"]):
            raise WaitlistError("already_waiting")
        if self.db.one("SELECT 1 FROM parking_session WHERE card_id = ? AND status = 'open'", (card["id"],)):
            raise WaitlistError("already_parked")
        summary = self.sites.summary(site)
        if summary["total"] == 0:
            raise WaitlistError("no_stalls")
        if summary["free"] > 0:
            raise WaitlistError("stall_available")  # lieber direkt einchecken oder reservieren
        eid = new_id("wl")
        self.db.execute("INSERT INTO waitlist (id, tenant_id, site_id, card_id, created_at) VALUES (?,?,?,?,?)",
                        (eid, site["tenant_id"], site["id"], card["id"], self.core.clock()))
        return self.db.one("SELECT * FROM waitlist WHERE id = ?", (eid,))

    def leave(self, entry: sqlite3.Row) -> None:
        self._finish(entry, "cancelled")
        if entry["status"] == "offered" and entry["reservation_id"]:
            res = self.db.one("SELECT * FROM reservation WHERE id = ?", (entry["reservation_id"],))
            if res is not None:
                self.reservations.end(res, "cancelled")  # gibt den Platz frei -> nächster Eintrag

    def close_site(self, site_id: str, status: str) -> None:
        for e in self.db.all("SELECT * FROM waitlist WHERE site_id = ? AND status IN ('waiting','offered')", (site_id,)):
            self._finish(e, status)
            if e["status"] == "offered" and e["reservation_id"]:
                res = self.db.one("SELECT * FROM reservation WHERE id = ? AND status = 'active'", (e["reservation_id"],))
                if res is not None:
                    self.db.execute("UPDATE reservation SET status = 'cancelled', ended_at = ? WHERE id = ?", (self.core.clock(), res["id"]))

    def _finish(self, entry: sqlite3.Row, status: str) -> None:
        self.db.execute("UPDATE waitlist SET status = ?, ended_at = ? WHERE id = ? AND status IN ('waiting','offered')",
                        (status, self.core.clock(), entry["id"]))

    # ------------------------------------------------------------------ Angebot
    def offer(self, site: sqlite3.Row) -> int:
        """Freie Stellplätze der Anlage an die Wartenden vergeben (in Reihenfolge). Rückgabe: Anzahl Angebote."""
        if self._busy:  # Reservierungen lösen selbst Ereignisse aus
            return 0
        self._busy = True
        try:
            made = 0
            while True:
                entry = self.db.one("SELECT * FROM waitlist WHERE site_id = ? AND status = 'waiting' ORDER BY created_at LIMIT 1",
                                    (site["id"],))
                if entry is None:
                    return made
                card = self.db.one("SELECT * FROM card WHERE id = ?", (entry["card_id"],))
                if card is None or card["status"] != "active":
                    self._finish(entry, "cancelled")
                    continue
                station = self._free_station(site)
                if station is None:
                    return made
                try:
                    res = self.reservations.create(station, int(site["hold_minutes"]), card_id=card["id"], label="Warteliste",
                                                   actor="waitlist", via="waitlist", presence="free")
                except ReservationError as exc:
                    log.info("Warteliste: Platz %s nicht vergeben (%s)", station["id"], exc)
                    return made
                self.db.execute("UPDATE waitlist SET status = 'offered', station_id = ?, reservation_id = ?, offered_at = ? "
                                "WHERE id = ?", (station["id"], res["id"], self.core.clock(), entry["id"]))
                made += 1
                self.push.send_card(card["id"], {
                    "type": "waitlist.offered", "title": "Platz frei!",
                    "body": f"{station['name']} ist {site['hold_minutes']} Minuten für dich reserviert.",
                    "station_name": station["name"], "minutes": site["hold_minutes"], "url": "/k"},
                    ttl=int(site["hold_minutes"]) * 60)
        finally:
            self._busy = False

    def _free_station(self, site: sqlite3.Row) -> sqlite3.Row | None:
        for st in self.sites.stations(site["id"]):
            s = self.monitoring.status(st, public=True)
            if self.sites.category(s) == "available" and not s["reservation"] and not s["session"]:
                return st
        return None
