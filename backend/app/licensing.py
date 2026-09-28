"""Lizenzabrechnung Plattform-Betreiber -> Organisation: Stellplatz-Tage x Tagespreis + Grundgebühr."""

from __future__ import annotations

import json
import sqlite3

from . import billing
from .core import Core
from .plans import get_plan
from .security import new_id
from .billing import iso as _iso


class Licensing:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db

    def record_usage(self) -> int:
        """Stellplatz-Anzahl je Organisation für heute festhalten (Maximum des Tages)."""
        day = billing.day_str(self.core.clock())
        n = 0
        for t in self.db.all("SELECT t.id, COUNT(s.id) AS stalls FROM tenant t LEFT JOIN station s ON s.tenant_id = t.id GROUP BY t.id"):
            self.db.execute(
                "INSERT INTO usage_day (tenant_id, day, stalls) VALUES (?,?,?) "
                "ON CONFLICT (tenant_id, day) DO UPDATE SET stalls = MAX(stalls, excluded.stalls)", (t["id"], day, t["stalls"]))
            n += 1
        return n

    def license(self, tenant: sqlite3.Row) -> dict:
        plan = get_plan(tenant["plan"])
        lic = self.db.one("SELECT * FROM license WHERE tenant_id = ?", (tenant["id"],))
        valid_until = lic["valid_until"] if lic else (tenant["created_at"] + plan.trial_days * 86400 if plan.trial_days else None)
        now = self.core.clock()
        return {
            "plan": plan.id,
            "valid_from": _iso(lic["valid_from"] if lic else tenant["created_at"]),
            "valid_until": _iso(valid_until),
            "trial": lic is None and bool(plan.trial_days),
            "days_left": None if valid_until is None else max(0, int((valid_until - now) // 86400)),
            "expired": valid_until is not None and now > valid_until,
            "price_per_stall_day_cents": lic["price_per_stall_day_cents"] if lic and lic["price_per_stall_day_cents"] is not None
            else plan.price_per_stall_day_cents,
            "base_month_cents": lic["base_month_cents"] if lic and lic["base_month_cents"] is not None else plan.base_month_cents,
            "custom": lic is not None and (lic["price_per_stall_day_cents"] is not None or lic["base_month_cents"] is not None),
            "notes": lic["notes"] if lic else "",
        }

    def billable_from(self, tenant: sqlite3.Row) -> str | None:
        """Erster abrechenbarer Tag (YYYY-MM-DD); None ohne Testphase. Die Testphase endet nach trial_days
        oder früher, sobald ein Vertrag beginnt."""
        plan = get_plan(tenant["plan"])
        if not plan.trial_days:
            return None
        end = tenant["created_at"] + plan.trial_days * 86400
        lic = self.db.one("SELECT valid_from FROM license WHERE tenant_id = ?", (tenant["id"],))
        if lic:
            end = min(end, lic["valid_from"])
        return billing.day_str(end)

    def stall_days(self, tenant: sqlite3.Row, month: str) -> tuple[int, int]:
        """(abrechenbare Stellplatz-Tage, Tage) im Monat bis heute. Tage in der kostenlosen Testphase zählen nicht.
        Ohne Aufzeichnung zählt die heutige Anzahl ab Anlagedatum."""
        now = self.core.clock()
        first = self.billable_from(tenant)
        recorded = {r["day"]: r["stalls"] for r in self.db.all("SELECT day, stalls FROM usage_day WHERE tenant_id = ? AND day LIKE ?",
                                                              (tenant["id"], f"{month}-%"))}
        stations = [s["created_at"] for s in self.db.all("SELECT created_at FROM station WHERE tenant_id = ?", (tenant["id"],))]
        days = billing.days_of_month(month, until=now)
        total = 0
        for d in days:
            if first and d < first:
                continue
            if d in recorded:
                total += recorded[d]
            else:
                total += sum(1 for c in stations if billing.day_str(c) <= d)
        return total, len(days)

    def preview(self, tenant: sqlite3.Row, month: str) -> dict:
        lic = self.license(tenant)
        plan = get_plan(tenant["plan"])
        stall_days, days = self.stall_days(tenant, month)
        lines = []
        if lic["base_month_cents"] and stall_days:
            lines.append({"text": f"Grundgebühr {plan.name} {month}", "qty": 1, "unit_cents": lic["base_month_cents"],
                          "total_cents": lic["base_month_cents"]})
        if lic["price_per_stall_day_cents"] and stall_days:
            lines.append({"text": f"Stellplatz-Tage {month}", "qty": stall_days, "unit_cents": lic["price_per_stall_day_cents"],
                          "total_cents": stall_days * lic["price_per_stall_day_cents"]})
        return {"tenant_id": tenant["id"], "tenant_name": tenant["name"], "month": month, "plan": plan.id, "days": days,
                "trial_until": lic["valid_until"] if lic["trial"] else None,
                "stall_days": stall_days, "lines": lines, "total_cents": sum(x["total_cents"] for x in lines), "license": lic}

    def issue(self, tenant: sqlite3.Row, month: str) -> dict:
        existing = self.db.one("SELECT * FROM invoice WHERE tenant_id = ? AND month = ?", (tenant["id"], month))
        if existing:
            return self.invoice_out(existing)
        p = self.preview(tenant, month)
        seq = (self.db.scalar("SELECT COUNT(*) FROM invoice WHERE month = ?", (month,)) or 0) + 1
        number = f"SBB-{month.replace('-', '')}-{seq:04d}"
        iid = new_id("inv")
        self.db.execute("INSERT INTO invoice (id, tenant_id, number, month, created_at, lines, total_cents) VALUES (?,?,?,?,?,?,?)",
                        (iid, tenant["id"], number, month, self.core.clock(), json.dumps(p["lines"]), p["total_cents"]))
        return self.invoice_out(self.db.one("SELECT * FROM invoice WHERE id = ?", (iid,)))

    def invoice_out(self, r: sqlite3.Row) -> dict:
        name = self.db.scalar("SELECT name FROM tenant WHERE id = ?", (r["tenant_id"],))
        return {"id": r["id"], "tenant_id": r["tenant_id"], "tenant_name": name, "number": r["number"], "month": r["month"],
                "created_at": _iso(r["created_at"]), "lines": json.loads(r["lines"]), "total_cents": r["total_cents"],
                "status": r["status"], "paid_at": _iso(r["paid_at"])}
