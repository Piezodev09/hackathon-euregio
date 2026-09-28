"""Lizenzmodell der Plattform. Limits und Funktionen werden serverseitig durchgesetzt.

Abgerechnet wird pro Stellplatz und Tag plus Grundgebühr je Monat (siehe billing.py). Ein Lizenzvertrag
je Organisation (Tabelle `license`) kann Laufzeit und abweichende Preise festlegen.
Eine Zahlungsanbindung ist NICHT enthalten; Rechnungen werden erzeugt und manuell als bezahlt markiert.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

FEATURES = ("ml", "public_display", "audit_log", "nfc", "parking_billing", "camera", "stall_view")


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    base_month_cents: int
    price_per_stall_day_cents: int
    max_stations: int  # eine Station = ein Stellplatz
    max_users: int
    retention_days: int
    trial_days: int
    ml_enabled: bool
    public_display: bool
    audit_log: bool
    nfc: bool
    parking_billing: bool
    camera: bool
    stall_view: bool

    @property
    def price_eur_month(self) -> float:
        """Richtwert für einen Stellplatz und 30 Tage (Anzeige, Kennzahlen)."""
        return round((self.base_month_cents + 30 * self.price_per_stall_day_cents) / 100, 2)

    def has(self, feature: str) -> bool:
        return bool(getattr(self, "ml_enabled" if feature == "ml" else feature))

    def to_dict(self) -> dict:
        return {**asdict(self), "price_eur_month": self.price_eur_month}


PLANS: dict[str, Plan] = {
    p.id: p
    for p in (
        #    id        name      Grund  /Tag  Plätze Nutzer Tage Test  KI    Anz.  Audit NFC   Park  Kam.  Ansicht
        Plan("free", "Free", 0, 0, 1, 2, 7, 0, False, True, False, True, False, False, True),
        Plan("school", "Schule", 900, 20, 5, 10, 30, 30, True, True, True, True, True, True, True),
        Plan("pro", "Pro", 2900, 15, 50, 50, 90, 30, True, True, True, True, True, True, True),
    )
}
DEFAULT_PLAN = "free"


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN])
