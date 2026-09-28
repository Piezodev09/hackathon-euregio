"""Abrechnung ohne Zahlungsanbieter.

1. Parkgebühren (Organisation -> Radfahrende): Tarif je Stellplatz oder Organisation, Gebühr je Parkvorgang.
2. Lizenz (Plattform-Betreiber -> Organisation): Stellplatz-Tage x Tagespreis + Grundgebühr je Monat.

Alle Beträge in Cent (int). Kalendertage in der Zeitzone der Schule (Europe/Berlin).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
MODES = ("free", "flat", "per_hour", "per_day")
DEFAULT_TARIFF = {"mode": "per_day", "price_cents": 50, "free_minutes": 15, "daily_cap_cents": None, "currency": "EUR"}


def normalize_tariff(t: dict | None) -> dict:
    t = {**DEFAULT_TARIFF, **(t or {})}
    if t["mode"] not in MODES:
        raise ValueError("unknown_mode")
    t["price_cents"] = max(0, int(t["price_cents"]))
    t["free_minutes"] = max(0, int(t["free_minutes"]))
    t["daily_cap_cents"] = None if t.get("daily_cap_cents") in (None, "", 0) else max(0, int(t["daily_cap_cents"]))
    t["currency"] = "EUR"
    return t


def load_tariff(raw: str | None) -> dict:
    return normalize_tariff(json.loads(raw) if raw else None)


def local_days(start: float, end: float, tz=TZ) -> int:
    """Anzahl der (auch angebrochenen) Kalendertage zwischen start und end."""
    a = datetime.fromtimestamp(start, tz).date()
    b = datetime.fromtimestamp(max(start, end), tz).date()
    return (b - a).days + 1


def compute_fee(start: float, end: float, tariff: dict, tz=TZ) -> int:
    t = normalize_tariff(tariff)
    duration = max(0.0, end - start)
    if t["mode"] == "free" or duration <= t["free_minutes"] * 60:
        return 0
    days = local_days(start, end, tz)
    if t["mode"] == "flat":
        amount = t["price_cents"]
    elif t["mode"] == "per_day":
        amount = days * t["price_cents"]
    else:  # per_hour: je angefangene Stunde nach den Freiminuten
        hours = math.ceil((duration - t["free_minutes"] * 60) / 3600)
        amount = hours * t["price_cents"]
    if t["daily_cap_cents"] is not None:
        amount = min(amount, days * t["daily_cap_cents"])
    return int(amount)


def month_bounds(month: str, tz=TZ) -> tuple[float, float]:
    """'2026-09' -> (Start, Ende) als Unix-Zeit in lokaler Zeitzone."""
    y, m = (int(x) for x in month.split("-"))
    if not (2000 <= y <= 2100 and 1 <= m <= 12):
        raise ValueError("invalid_month")
    start = datetime(y, m, 1, tzinfo=tz)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=tz)
    return start.timestamp(), end.timestamp()


def current_month(now: float, tz=TZ) -> str:
    return datetime.fromtimestamp(now, tz).strftime("%Y-%m")


def day_str(ts: float, tz=TZ) -> str:
    return datetime.fromtimestamp(ts, tz).date().isoformat()


def days_of_month(month: str, until: float | None = None, tz=TZ) -> list[str]:
    start, end = month_bounds(month, tz)
    d = datetime.fromtimestamp(start, tz).date()
    last = datetime.fromtimestamp(end - 1, tz).date()
    if until is not None:
        last = min(last, datetime.fromtimestamp(until, tz).date())
    out = []
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
