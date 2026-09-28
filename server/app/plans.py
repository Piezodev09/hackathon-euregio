"""Subscription plans. All limits are enforced on the server.

Note: there is NO payment integration (e.g. Stripe). Plan changes are audited; billing is done
manually (invoice) until a payment provider is connected.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_eur_month: float
    max_stations: int
    max_slots_per_station: int
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
        Plan("free", "Free", 0, 1, 4, 2, 7, False, True, False),
        Plan("school", "School", 19, 5, 30, 10, 30, True, True, True),
        Plan("pro", "Pro", 79, 50, 200, 50, 90, True, True, True),
    )
}
DEFAULT_PLAN = "free"
# The operator's own organisation of a self-hosted installation gets every feature.
SELF_HOSTED_PLAN = "pro"


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN])
