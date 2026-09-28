"""Tages-, Wochen- und Monatsbericht je Organisation: Auslastung, Check-ins, Gebühren, Warnungen, Meldungen.

Ausgabe als JSON (Portal), CSV (Tabellenkalkulation) und PDF (E-Mail an die Schule). Enthält der Zeitraum
simulierte Daten, steht das deutlich im Bericht.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import date, datetime, timedelta

from .billing import TZ, iso
from .core import Core
from .pdf import W, Pdf
from .plans import get_plan

PERIODS = ("day", "week", "month")
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def period_bounds(period: str, day: str) -> tuple[float, float, str]:
    d = date.fromisoformat(day)
    if period == "day":
        a, b = d, d + timedelta(days=1)
        label = f"{WEEKDAYS[d.weekday()]}, {d:%d.%m.%Y}"
    elif period == "week":
        a = d - timedelta(days=d.weekday())
        b = a + timedelta(days=7)
        label = f"KW {a.isocalendar()[1]}/{a.isocalendar()[0]} ({a:%d.%m.}–{b - timedelta(days=1):%d.%m.%Y})"
    elif period == "month":
        a = d.replace(day=1)
        b = (a + timedelta(days=32)).replace(day=1)
        label = f"{a:%m/%Y}"
    else:
        raise ValueError("invalid_period")
    ts = lambda x: datetime(x.year, x.month, x.day, tzinfo=TZ).timestamp()  # noqa: E731
    return ts(a), ts(b), label


def _eur(cents: int) -> str:
    return f"{cents / 100:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{round(v * 100)} %"


class Reports:
    def __init__(self, core: Core, monitoring):
        self.core = core
        self.db = core.db
        self.monitoring = monitoring

    def allowed(self, tenant: sqlite3.Row) -> bool:
        return get_plan(tenant["plan"]).reports

    def build(self, tenant: sqlite3.Row, period: str, day: str) -> dict:
        start, end, label = period_bounds(period, day)
        q = lambda sql, *p: self.db.scalar(sql, p) or 0  # noqa: E731
        stations, sim_any = [], False
        for st in self.db.all("SELECT * FROM station WHERE tenant_id = ? ORDER BY created_at", (tenant["id"],)):
            occ = self.monitoring.occupancy_between(st, start, end)
            row = {
                "station_id": st["id"], "name": st["name"],
                "occupancy": round(occ["occupied_s"] / occ["known_s"], 3) if occ["known_s"] else None,
                "data_coverage": round(occ["known_s"] / occ["period_s"], 3) if occ["period_s"] else None,
                "checkins": q("SELECT COUNT(*) FROM parking_session WHERE station_id = ? AND started_at >= ? AND started_at < ?",
                              st["id"], start, end),
                "fees_cents": q("SELECT SUM(amount_cents) FROM parking_session WHERE station_id = ? AND status = 'closed' "
                                "AND ended_at >= ? AND ended_at < ?", st["id"], start, end),
                "warnings": q("SELECT COUNT(*) FROM event WHERE station_id = ? AND kind = 'unusual_movement' AND severity = 'warning' "
                              "AND occurred_at >= ? AND occurred_at < ?", st["id"], start, end),
                "problems": q("SELECT COUNT(*) FROM event WHERE station_id = ? AND kind = 'user_report' "
                              "AND occurred_at >= ? AND occurred_at < ?", st["id"], start, end),
                "sensor_faults": q("SELECT COUNT(*) FROM event WHERE station_id = ? AND kind = 'sensor_fault' "
                                   "AND occurred_at >= ? AND occurred_at < ?", st["id"], start, end),
                "reservations": q("SELECT COUNT(*) FROM reservation WHERE station_id = ? AND created_at >= ? AND created_at < ?",
                                  st["id"], start, end),
            }
            row["simulated"] = occ["simulated"] or bool(q(
                "SELECT COUNT(*) FROM parking_session WHERE station_id = ? AND source = 'simulated' AND started_at >= ? AND started_at < ?",
                st["id"], start, end))
            sim_any = sim_any or row["simulated"]
            stations.append(row)
        known = [r["occupancy"] for r in stations if r["occupancy"] is not None]
        totals = {k: sum(r[k] for r in stations) for k in ("checkins", "fees_cents", "warnings", "problems", "sensor_faults",
                                                             "reservations")}
        totals["occupancy"] = round(sum(known) / len(known), 3) if known else None
        kind = {"day": "Tagesbericht", "week": "Wochenbericht", "month": "Monatsbericht"}[period]
        return {"tenant_id": tenant["id"], "tenant_name": tenant["name"], "period": period, "day": day,
                "from": iso(start), "to": iso(end), "label": label, "title": f"{kind} {label}",
                "stations": stations, "totals": totals, "contains_simulated": sim_any,
                "generated_at": iso(self.core.clock())}

    # ------------------------------------------------------------------ Ausgabe
    @staticmethod
    def filename(rep: dict, ext: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", rep["tenant_name"].lower()).strip("-")[:40] or "organisation"
        return f"bericht-{rep['period']}-{rep['day']}-{slug}.{ext}"

    @staticmethod
    def csv(rep: dict) -> str:
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow([rep["title"], rep["tenant_name"]])
        if rep["contains_simulated"]:
            w.writerow(["Hinweis: enthält SIMULATION / DEMODATEN"])
        w.writerow(["Stellplatz", "Auslastung %", "Datenabdeckung %", "Check-ins", "Gebühren EUR", "Warnungen",
                    "Gemeldete Probleme", "Sensorfehler", "Reservierungen", "Simulation"])
        pct = lambda v: "" if v is None else str(round(v * 100))  # noqa: E731
        name = lambda v: "'" + v if v and v[0] in "=+-@\t\r" else v  # Schutz vor Formel-Injektion  # noqa: E731
        for r in rep["stations"]:
            w.writerow([name(r["name"]), pct(r["occupancy"]), pct(r["data_coverage"]), r["checkins"],
                        f"{r['fees_cents'] / 100:.2f}".replace(".", ","), r["warnings"], r["problems"], r["sensor_faults"],
                        r["reservations"], "ja" if r["simulated"] else "nein"])
        t = rep["totals"]
        w.writerow(["Summe", pct(t["occupancy"]), "", t["checkins"], f"{t['fees_cents'] / 100:.2f}".replace(".", ","),
                    t["warnings"], t["problems"], t["sensor_faults"], t["reservations"], ""])
        return "﻿" + buf.getvalue()

    def pdf(self, rep: dict) -> bytes:
        p = Pdf(rep["title"])
        teal, grey, dark = (0.05, 0.37, 0.4), (0.4, 0.45, 0.5), (0.1, 0.13, 0.16)
        p.rect(0, 0, W, 70, teal)
        p.text(40, 32, self.core.s.product_name, 11, True, (1, 1, 1))
        p.text(40, 54, rep["title"], 16, True, (1, 1, 1))
        y = 100
        p.text(40, y, rep["tenant_name"], 12, True, dark)
        p.text(W - 40, y, f"Erstellt: {datetime.fromisoformat(rep['generated_at']).astimezone(TZ):%d.%m.%Y %H:%M}", 9,
               color=grey, align="right")
        y += 18
        if rep["contains_simulated"]:
            p.rect(40, y, W - 80, 22, (1.0, 0.93, 0.7))
            p.text(48, y + 15, "Enthält SIMULATION / DEMODATEN – keine echten Messwerte.", 10, True, (0.45, 0.3, 0.0))
            y += 32
        t = rep["totals"]
        tiles = [("Auslastung", _pct(t["occupancy"])), ("Check-ins", str(t["checkins"])), ("Gebühren", _eur(t["fees_cents"])),
                 ("Warnungen", str(t["warnings"])), ("Gemeldete Probleme", str(t["problems"]))]
        tw = (W - 80 - 4 * 8) / 5
        for i, (k, v) in enumerate(tiles):
            x = 40 + i * (tw + 8)
            p.rect(x, y, tw, 52, (0.93, 0.96, 0.96))
            p.text(x + 8, y + 18, k, 8, color=grey)
            p.text(x + 8, y + 40, v, 15, True, dark)
        y += 76
        cols = [("Stellplatz", 40, "left"), ("Auslastung", 250, "left"), ("Check-ins", 395, "right"), ("Gebühren", 465, "right"),
                ("Warn.", 510, "right"), ("Meld.", 555, "right")]
        for label, x, align in cols:
            p.text(x, y, label, 9, True, grey, align)
        y += 6
        p.line(40, y, W - 40, y, 0.8)
        for r in rep["stations"]:
            y += 20
            if y > 780:
                p.page()
                y = 60
            name = r["name"] if len(r["name"]) <= 32 else r["name"][:31] + "…"
            p.text(40, y, name + (" (Sim.)" if r["simulated"] else ""), 10, color=dark)
            if r["occupancy"] is not None:
                p.rect(250, y - 9, 90, 10, (0.9, 0.92, 0.93))
                p.rect(250, y - 9, 90 * r["occupancy"], 10, teal)
            p.text(346, y, _pct(r["occupancy"]), 9, color=dark)
            p.text(395, y, str(r["checkins"]), 10, color=dark, align="right")
            p.text(465, y, _eur(r["fees_cents"]), 10, color=dark, align="right")
            p.text(510, y, str(r["warnings"]), 10, color=dark, align="right")
            p.text(555, y, str(r["problems"]), 10, color=dark, align="right")
            p.line(40, y + 7, W - 40, y + 7, 0.3)
        y += 36
        notes = [
            "Auslastung = Anteil der Zeit mit gültiger Messung, in der der Stellplatz belegt war. Zeiten ohne verlässliche",
            "Daten (STATUS UNBEKANNT) zählen weder als frei noch als belegt.",
            "Warnungen sind Hinweise auf ungewöhnliche Bewegung – kein Nachweis für einen Diebstahl.",
            "Gebühren = abgeschlossene Parkvorgänge im Zeitraum (ohne Zahlungsanbieter; Abrechnung durch die Organisation).",
        ]
        for line in notes:
            p.text(40, y, line, 8, color=grey)
            y += 12
        return p.render()

    def mail_text(self, rep: dict) -> str:
        t = rep["totals"]
        lines = [f"{rep['title']} – {rep['tenant_name']}", ""]
        if rep["contains_simulated"]:
            lines += ["Hinweis: Der Bericht enthält SIMULATION / DEMODATEN.", ""]
        lines += [f"Auslastung (Durchschnitt): {_pct(t['occupancy'])}", f"Check-ins: {t['checkins']}",
                  f"Gebühren: {_eur(t['fees_cents'])}", f"Warnungen: {t['warnings']}", f"Gemeldete Probleme: {t['problems']}", ""]
        for r in rep["stations"]:
            lines.append(f"- {r['name']}: Auslastung {_pct(r['occupancy'])}, {r['checkins']} Check-ins, {_eur(r['fees_cents'])}, "
                         f"{r['warnings']} Warnungen")
        lines += ["", "Der vollständige Bericht liegt als PDF bei.", f"Portal: {self.core.s.base_url}/app#/reports",
                  "E-Mail-Einstellungen: Portal → Mein Konto → Benachrichtigungen"]
        return "\n".join(lines)
