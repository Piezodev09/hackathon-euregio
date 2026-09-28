"""Statistiken je Organisation: Auslastung, Heatmap, Parkdauer, NFC, Datenabdeckung (Europe/Berlin).

Grundsatz wie überall: Zeiten ohne gültige, aktuelle Messung zählen weder als frei noch als belegt, sondern als
"keine Daten". Auslastung = belegte Sekunden / Sekunden mit Daten. Simulierte Daten werden gekennzeichnet.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from statistics import median

from .billing import TZ
from .plans import get_plan

# Parkdauer-Klassen in Minuten (untere Grenze), letzte Klasse offen
DURATION_BINS = [(0, "<15 min"), (15, "15–60 min"), (60, "1–3 h"), (180, "3–6 h"), (360, "6–12 h"), (720, "12–24 h"), (1440, "> 24 h")]
TAP_OK = ("checked_in", "checked_out")


def day_start(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp()


class Stats:
    def __init__(self, core, monitoring):
        self.core = core
        self.db = core.db
        self.monitoring = monitoring

    # ------------------------------------------------------------------ heute (Übersicht)
    def today(self, tenant: sqlite3.Row) -> dict:
        now = self.core.clock()
        start = day_start(datetime.fromtimestamp(now, TZ).date())
        tid = tenant["id"]
        q = lambda sql, *p: self.db.scalar(sql, (tid, *p)) or 0  # noqa: E731
        return {
            "day": datetime.fromtimestamp(now, TZ).date().isoformat(),
            "checkins": q("SELECT COUNT(*) FROM parking_session WHERE tenant_id = ? AND started_at >= ?", start),
            "checkouts": q("SELECT COUNT(*) FROM parking_session WHERE tenant_id = ? AND status = 'closed' AND ended_at >= ?", start),
            "fees_cents": q("SELECT SUM(amount_cents) FROM parking_session WHERE tenant_id = ? AND status = 'closed' AND ended_at >= ?",
                            start),
            "parked_now": q("SELECT COUNT(*) FROM parking_session WHERE tenant_id = ? AND status = 'open'"),
            "taps": q("SELECT COUNT(*) FROM nfc_tap WHERE tenant_id = ? AND at >= ? AND result != 'duplicate'", start),
            "unknown_cards": q("SELECT COUNT(DISTINCT card_id) FROM nfc_tap WHERE tenant_id = ? AND at >= ? AND result = 'unknown_card'",
                               start),
            "warnings": q("SELECT COUNT(*) FROM event WHERE tenant_id = ? AND occurred_at >= ? AND kind = 'unusual_movement' "
                          "AND severity = 'warning'", start),
        }

    # ------------------------------------------------------------------ Zeitraum
    def build(self, tenant: sqlite3.Row, days: int, station_id: str | None = None) -> dict:
        plan = get_plan(tenant["plan"])
        preview = not plan.reports
        if preview:
            days = 7  # Free: Vorschau auf die letzten 7 Tage
        now = self.core.clock()
        today = datetime.fromtimestamp(now, TZ).date()
        first = today - timedelta(days=days - 1)
        start, end = day_start(first), now
        day_keys = [(first + timedelta(days=i)).isoformat() for i in range(days)]

        stations = self.db.all("SELECT * FROM station WHERE tenant_id = ? ORDER BY created_at", (tenant["id"],))
        if station_id:
            stations = [s for s in stations if s["id"] == station_id]
        ids = [s["id"] for s in stations]

        daily = {d: {"day": d, "occupied_s": 0.0, "known_s": 0.0, "period_s": 0.0, "checkins": 0, "fees_cents": 0,
                     "warnings": 0, "reservations": 0, "taps": 0} for d in day_keys}
        heat = [[{"occupied_s": 0.0, "known_s": 0.0, "checkins": 0} for _ in range(24)] for _ in range(7)]
        per_station, simulated = [], False

        for st in stations:
            occ = self._occupancy(st, start, end, daily, heat)
            simulated = simulated or occ["simulated"]
            # Zeitraum je Stellplatz ab Anlage (vorher gab es keine Daten zu erwarten)
            created = max(start, st["created_at"])
            period = max(0.0, end - created)
            for d in day_keys:
                a = max(day_start(date.fromisoformat(d)), created)
                b = min(day_start(date.fromisoformat(d)) + 86400, end)  # Sommerzeit: 23/25 h nur Näherung am Umstelltag
                daily[d]["period_s"] += max(0.0, b - a)
            sessions = self.db.all("SELECT started_at, ended_at, amount_cents, status, source FROM parking_session "
                                   "WHERE station_id = ? AND started_at < ? AND (started_at >= ? OR ended_at >= ?)",
                                   (st["id"], end, start, start))
            durations = [(s["ended_at"] - s["started_at"]) / 60 for s in sessions
                         if s["status"] == "closed" and s["ended_at"] and s["ended_at"] >= start]
            simulated = simulated or any(s["source"] == "simulated" for s in sessions)
            per_station.append({
                "station_id": st["id"], "name": st["name"],
                "occupancy": round(occ["occupied_s"] / occ["known_s"], 3) if occ["known_s"] else None,
                "data_coverage": round(min(1.0, occ["known_s"] / period), 3) if period else None,
                "checkins": sum(1 for s in sessions if s["started_at"] >= start),
                "fees_cents": sum(s["amount_cents"] or 0 for s in sessions if s["status"] == "closed" and (s["ended_at"] or 0) >= start),
                "median_minutes": round(median(durations)) if durations else None,
                "simulated": occ["simulated"] or any(s["source"] == "simulated" for s in sessions),
            })

        ph = ",".join("?" * len(ids)) or "''"
        dkey = lambda ts: datetime.fromtimestamp(ts, TZ).date().isoformat()  # noqa: E731
        durations_all: list[float] = []
        for s in self.db.all(f"SELECT started_at, ended_at, amount_cents, status FROM parking_session WHERE station_id IN ({ph}) "
                             "AND (started_at >= ? OR ended_at >= ?)", (*ids, start, start)):
            if s["started_at"] >= start:
                daily[dkey(s["started_at"])]["checkins"] += 1
                lt = datetime.fromtimestamp(s["started_at"], TZ)
                heat[lt.weekday()][lt.hour]["checkins"] += 1
            if s["status"] == "closed" and s["ended_at"] and s["ended_at"] >= start:
                daily[dkey(s["ended_at"])]["fees_cents"] += s["amount_cents"] or 0
                durations_all.append((s["ended_at"] - s["started_at"]) / 60)
        for r in self.db.all(f"SELECT occurred_at FROM event WHERE station_id IN ({ph}) AND occurred_at >= ? AND kind = 'unusual_movement' "
                             "AND severity = 'warning'", (*ids, start)):
            daily[dkey(r["occurred_at"])]["warnings"] += 1
        for r in self.db.all(f"SELECT created_at FROM reservation WHERE station_id IN ({ph}) AND created_at >= ?", (*ids, start)):
            daily[dkey(r["created_at"])]["reservations"] += 1

        taps = self.db.all(f"SELECT t.at, t.result, t.device_id, t.card_id, t.source, d.name AS device_name FROM nfc_tap t "
                           f"LEFT JOIN device d ON d.id = t.device_id WHERE t.station_id IN ({ph}) AND t.at >= ? AND t.result != 'duplicate'",
                           (*ids, start))
        by_result: dict[str, int] = {}
        by_reader: dict[str, dict] = {}
        for tp in taps:
            daily[dkey(tp["at"])]["taps"] += 1
            by_result[tp["result"]] = by_result.get(tp["result"], 0) + 1
            rd = by_reader.setdefault(tp["device_id"], {"device_id": tp["device_id"], "name": tp["device_name"] or tp["device_id"],
                                                         "taps": 0, "ok": 0, "last_at": 0.0})
            rd["taps"] += 1
            rd["ok"] += tp["result"] in TAP_OK
            rd["last_at"] = max(rd["last_at"], tp["at"])
            simulated = simulated or tp["source"] == "simulated"

        series = []
        for d in day_keys:
            x = daily[d]
            series.append({"day": d, "occupancy": round(x["occupied_s"] / x["known_s"], 3) if x["known_s"] else None,
                           "data_coverage": round(min(1.0, x["known_s"] / x["period_s"]), 3) if x["period_s"] else None,
                           "checkins": x["checkins"], "fees_cents": x["fees_cents"], "warnings": x["warnings"],
                           "reservations": x["reservations"], "taps": x["taps"]})
        heatmap = [[{"occupancy": round(c["occupied_s"] / c["known_s"], 3) if c["known_s"] >= 60 else None, "checkins": c["checkins"]}
                    for c in row] for row in heat]
        occ_s = sum(daily[d]["occupied_s"] for d in day_keys)
        known_s = sum(daily[d]["known_s"] for d in day_keys)
        period_s = sum(daily[d]["period_s"] for d in day_keys)
        peak = max(((w, h, c["occupancy"]) for w, row in enumerate(heatmap) for h, c in enumerate(row) if c["occupancy"] is not None),
                   key=lambda x: x[2], default=None)
        bins = [{"from_min": lo, "label": label, "count": 0} for lo, label in DURATION_BINS]
        for m in durations_all:
            idx = max(i for i, (lo, _) in enumerate(DURATION_BINS) if m >= lo)
            bins[idx]["count"] += 1
        unknown_cards = len({tp["card_id"] for tp in taps if tp["result"] == "unknown_card"})
        return {
            "days": days, "from": first.isoformat(), "to": today.isoformat(), "station_id": station_id,
            "preview": preview, "retention_days": plan.retention_days, "simulated": simulated,
            "totals": {
                "occupancy": round(occ_s / known_s, 3) if known_s else None,
                "data_coverage": round(min(1.0, known_s / period_s), 3) if period_s else None,
                "checkins": sum(x["checkins"] for x in series), "fees_cents": sum(x["fees_cents"] for x in series),
                "warnings": sum(x["warnings"] for x in series), "reservations": sum(x["reservations"] for x in series),
                "median_minutes": round(median(durations_all)) if durations_all else None,
                "taps": len(taps), "tap_success": round(sum(by_result.get(r, 0) for r in TAP_OK) / len(taps), 3) if taps else None,
                "unknown_cards": unknown_cards,
                "peak": {"weekday": peak[0], "hour": peak[1], "occupancy": peak[2]} if peak else None,
            },
            "series": series, "heatmap": heatmap, "durations": bins,
            "stations": per_station,
            "nfc": {"by_result": by_result,
                    "readers": [{**r, "success": round(r["ok"] / r["taps"], 3), "last_at": datetime.fromtimestamp(r["last_at"], TZ).isoformat()}
                                for r in sorted(by_reader.values(), key=lambda r: -r["taps"])]},
        }

    def _occupancy(self, station: sqlite3.Row, start: float, end: float, daily: dict, heat: list) -> dict:
        """Wie Monitoring.occupancy_between, aber aufgeteilt nach Tag und Wochentag × Stunde (lokale Zeit)."""
        stale = self.core.s.stale_after_s
        rows = self.db.all("SELECT server_time, occupied, sensor_state, source FROM measurement "
                           "WHERE slot_id = ? AND server_time >= ? AND server_time < ? ORDER BY server_time",
                           (self.monitoring.stall(station["id"])["id"], start - stale, end))
        occ = known = 0.0
        simulated = False
        for i, r in enumerate(rows):
            if r["sensor_state"] != "ok" or r["occupied"] is None:
                continue
            simulated = simulated or r["source"] == "simulated"
            nxt = rows[i + 1]["server_time"] if i + 1 < len(rows) else end
            a, b = max(r["server_time"], start), min(nxt, r["server_time"] + stale, end)
            while a < b:  # an Stundengrenzen teilen (Berlin hat volle Stunden Versatz zu UTC)
                cut = min(b, (a // 3600 + 1) * 3600)
                lt = datetime.fromtimestamp(a, TZ)
                span = cut - a
                cell = heat[lt.weekday()][lt.hour]
                day = daily.get(lt.date().isoformat())
                cell["known_s"] += span
                known += span
                if day is not None:
                    day["known_s"] += span
                if r["occupied"]:
                    cell["occupied_s"] += span
                    occ += span
                    if day is not None:
                        day["occupied_s"] += span
                a = cut
        return {"occupied_s": occ, "known_s": known, "simulated": simulated}
