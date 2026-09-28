"""Öffnungszeiten je Stellplatz und Sperrzeiten (Ferien, Veranstaltungen).

Öffnungszeiten: {"mon": [["07:00", "18:00"]], "tue": [...], ...} in Ortszeit Europe/Berlin.
Ein fehlender Wochentag bzw. eine leere Liste heißt "geschlossen", `None` heißt "immer geöffnet".
Geschlossen bedeutet: keine neuen Check-ins und Reservierungen. Anzeige und Warnungen laufen weiter.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from .billing import TZ, iso

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def normalize_hours(hours: dict | None) -> dict | None:
    """Prüft und vereinheitlicht einen Wochenplan. ValueError bei ungültigen Angaben."""
    if hours is None:
        return None
    if not isinstance(hours, dict) or set(hours) - set(DAYS):
        raise ValueError("invalid_hours")
    out: dict[str, list[list[str]]] = {}
    for day in DAYS:
        ranges = hours.get(day) or []
        if not isinstance(ranges, list) or len(ranges) > 4:
            raise ValueError("invalid_hours")
        clean = []
        for r in ranges:
            if not (isinstance(r, (list, tuple)) and len(r) == 2 and all(isinstance(x, str) and _TIME.match(x) for x in r)):
                raise ValueError("invalid_hours")
            if _minutes(r[0]) >= _minutes(r[1]):
                raise ValueError("invalid_hours")
            clean.append([r[0], r[1]])
        out[day] = sorted(clean)
    return out


def load_hours(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return normalize_hours(json.loads(raw))
    except (ValueError, TypeError):
        return None


def _open_by_hours(hours: dict | None, t: float) -> bool:
    if hours is None:
        return True
    local = datetime.fromtimestamp(t, TZ)
    minute = local.hour * 60 + local.minute
    return any(_minutes(a) <= minute < _minutes(b) for a, b in hours.get(DAYS[local.weekday()], []))


def next_opening(hours: dict | None, t: float) -> float | None:
    """Nächster Öffnungsbeginn nach t (innerhalb von 8 Tagen), sonst None."""
    if hours is None:
        return t
    local = datetime.fromtimestamp(t, TZ)
    for add in range(0, 8):
        day = (local + timedelta(days=add)).date()
        for a, _b in hours.get(DAYS[day.weekday()], []):
            h, m = divmod(_minutes(a), 60)
            start = datetime(day.year, day.month, day.day, h, m, tzinfo=TZ).timestamp()
            if start > t:
                return start
    return None


def closures(db, tenant_id: str, station_id: str, t: float) -> list[sqlite3.Row]:
    return db.all("SELECT * FROM closure WHERE tenant_id = ? AND (station_id IS NULL OR station_id = ?) AND ends_at > ? "
                  "ORDER BY starts_at", (tenant_id, station_id, t))


def closed_info(db, station: sqlite3.Row, t: float) -> dict | None:
    """None = geöffnet. Sonst {"reason": "closure"|"hours", "note", "opens_at"}."""
    hours = load_hours(station["hours"])
    for c in closures(db, station["tenant_id"], station["id"], t):
        if c["starts_at"] <= t < c["ends_at"]:
            reopen = c["ends_at"] if _open_by_hours(hours, c["ends_at"]) else next_opening(hours, c["ends_at"])
            return {"reason": "closure", "note": c["note"], "until": iso(c["ends_at"]), "opens_at": iso(reopen)}
    if not _open_by_hours(hours, t):
        return {"reason": "hours", "note": "", "until": None, "opens_at": iso(next_opening(hours, t))}
    return None
