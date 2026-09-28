"""Reservierung eines Stellplatzes für X Minuten.

Eine Reservierung ist entweder an eine Karte gebunden (nur diese Karte kann einchecken und erfüllt damit
die Reservierung) oder hält den Platz frei (niemand kann per Karte einchecken, z. B. für Besuch oder
eine Veranstaltung). Sie endet automatisch nach Ablauf.

Anzeige: RESERVIERT nur, wenn der Platz laut aktueller Messung frei ist. Ist der Zustand unbekannt oder
belegt, gewinnt dieser Zustand – eine Reservierung macht nie aus "unbekannt" einen bekannten Zustand.
"""

from __future__ import annotations

import sqlite3

from .billing import iso
from .core import Core
from .hours import closed_info
from .security import new_id

MIN_MINUTES, MAX_MINUTES = 5, 240


class ReservationError(ValueError):
    pass


class Reservations:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db

    def expire(self) -> int:
        """Abgelaufene Reservierungen beenden (wird bei jedem Zugriff und im Hintergrund aufgerufen)."""
        now = self.core.clock()
        rows = self.db.all("SELECT * FROM reservation WHERE status = 'active' AND expires_at <= ?", (now,))
        for r in rows:
            self.db.execute("UPDATE reservation SET status = 'expired', ended_at = expires_at WHERE id = ? AND status = 'active'",
                            (r["id"],))
            self.core.emit("reservation.ended", r["tenant_id"], {"reservation_id": r["id"], "station_id": r["station_id"],
                                                                 "reason": "expired"})
        return len(rows)

    def active(self, station_id: str) -> sqlite3.Row | None:
        now = self.core.clock()
        return self.db.one("SELECT * FROM reservation WHERE station_id = ? AND status = 'active' AND expires_at > ? "
                           "ORDER BY created_at DESC LIMIT 1", (station_id, now))

    def create(self, station: sqlite3.Row, minutes: int, *, card_id: str | None, label: str, actor: str, via: str,
               presence: str, source: str = "live") -> sqlite3.Row:
        if not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise ReservationError("invalid_minutes")
        self.expire()
        if station["maintenance"]:
            raise ReservationError("maintenance")
        if closed_info(self.db, station, self.core.clock()):
            raise ReservationError("closed")
        if self.active(station["id"]):
            raise ReservationError("already_reserved")
        if presence == "occupied" or self.db.one("SELECT 1 FROM parking_session WHERE station_id = ? AND status = 'open'",
                                                  (station["id"],)):
            raise ReservationError("occupied")
        if card_id:
            card = self.db.one("SELECT status FROM card WHERE id = ? AND tenant_id = ?", (card_id, station["tenant_id"]))
            if card is None:
                raise ReservationError("card_not_found")
            if card["status"] != "active":
                raise ReservationError("card_not_active")
        now = self.core.clock()
        rid = new_id("res")
        self.db.execute(
            "INSERT INTO reservation (id, tenant_id, station_id, card_id, label, created_at, expires_at, created_by, via, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, station["tenant_id"], station["id"], card_id, label, now, now + minutes * 60, actor, via, source))
        row = self.db.one("SELECT * FROM reservation WHERE id = ?", (rid,))
        self.core.emit("reservation.created", station["tenant_id"], {"reservation": self.out(row, public=True),
                                                                     "station_id": station["id"]})
        return row

    def end(self, row: sqlite3.Row, status: str) -> None:
        cur = self.db.execute("UPDATE reservation SET status = ?, ended_at = ? WHERE id = ? AND status = 'active'",
                              (status, self.core.clock(), row["id"]))
        if cur.rowcount:
            self.core.emit("reservation.ended", row["tenant_id"], {"reservation_id": row["id"], "station_id": row["station_id"],
                                                                   "reason": status})

    def out(self, r: sqlite3.Row, *, public: bool = False) -> dict:
        now = self.core.clock()
        body = {"id": r["id"], "station_id": r["station_id"], "created_at": iso(r["created_at"]), "until": iso(r["expires_at"]),
                "remaining_s": max(0, round(r["expires_at"] - now)) if r["status"] == "active" else 0,
                "status": r["status"], "for_card": r["card_id"] is not None, "simulated": r["source"] == "simulated"}
        if not public:
            card = self.db.one("SELECT label FROM card WHERE id = ?", (r["card_id"],)) if r["card_id"] else None
            body.update({"label": r["label"], "card_id": r["card_id"], "card_label": card["label"] if card else None,
                         "created_by": r["created_by"], "via": r["via"], "ended_at": iso(r["ended_at"])})
        return body
