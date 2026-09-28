"""Tarife der Plattform. Limits werden serverseitig durchgesetzt.

Hinweis: Eine Zahlungsanbindung (z. B. Stripe, Rechnung) ist NICHT enthalten. Tarifwechsel werden
protokolliert; die Abrechnung erfolgt bis zur Anbindung manuell.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_eur_month: float
    max_stations: int  # eine Station = ein Stellplatz
    max_users: int
    retention_days: int
    ml_enabled: bool
    public_display: bool
    audit_log: bool

    def to_dict(self) -> dict:
        return asdict(self)


PLANS: dict[str, Plan] = {
    p.id: p
    for p in (
        Plan("free", "Free", 0, 1, 2, 7, False, True, False),
        Plan("school", "Schule", 19, 5, 10, 30, True, True, True),
        Plan("pro", "Pro", 79, 50, 50, 90, True, True, True),
    )
}
DEFAULT_PLAN = "free"


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN])
