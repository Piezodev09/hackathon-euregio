"""Lizenzmodell der Plattform. Limits und Funktionen werden serverseitig durchgesetzt.

Abgerechnet wird pro Stellplatz und Tag plus Grundgebühr je Monat (siehe billing.py). Ein Lizenzvertrag
je Organisation (Tabelle `license`) kann Laufzeit und abweichende Preise festlegen.
Eine Zahlungsanbindung ist NICHT enthalten; Rechnungen werden erzeugt und manuell als bezahlt markiert.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

FEATURES = ("ml", "public_display", "audit_log", "nfc", "parking_billing", "camera", "stall_view",
            "reservations", "reports", "integrations")


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
    parking_billing: bool  # Parkgebühren, Monatsaufstellung und Guthaben
    camera: bool
    stall_view: bool
    reservations: bool
    reports: bool  # Tages-/Wochenbericht (PDF/CSV, per E-Mail)
    integrations: bool  # API-Schlüssel und Webhooks

    @property
    def price_eur_month(self) -> float:
        """Richtwert für einen Stellplatz und 30 Tage (Anzeige, Kennzahlen)."""
        return round((self.base_month_cents + 30 * self.price_per_stall_day_cents) / 100, 2)

    def has(self, feature: str) -> bool:
        return bool(getattr(self, "ml_enabled" if feature == "ml" else feature))

    def to_dict(self) -> dict:
        return {**asdict(self), "price_eur_month": self.price_eur_month}


_ALL = dict(ml_enabled=True, public_display=True, audit_log=True, nfc=True, parking_billing=True, camera=True,
            stall_view=True, reservations=True, reports=True, integrations=True)

PLANS: dict[str, Plan] = {
    p.id: p
    for p in (
        # Free: ein Stellplatz mit Status, Anzeige, NFC-Check-in, Stellplatz-Ansicht, E-Mail-Warnungen, Öffnungszeiten.
        Plan("free", "Free", base_month_cents=0, price_per_stall_day_cents=0, max_stations=1, max_users=2, retention_days=7,
             trial_days=0, **{**{k: False for k in _ALL}, "public_display": True, "nfc": True, "stall_view": True}),
        Plan("school", "Schule", base_month_cents=900, price_per_stall_day_cents=20, max_stations=5, max_users=10,
             retention_days=30, trial_days=30, **_ALL),
        Plan("pro", "Pro", base_month_cents=2900, price_per_stall_day_cents=15, max_stations=50, max_users=50,
             retention_days=90, trial_days=30, **_ALL),
    )
}
DEFAULT_PLAN = "free"
# Neue Organisationen starten mit der kostenlosen Testphase des Schul-Tarifs (alle Funktionen, 30 Tage).
SIGNUP_PLAN = "school"


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id, PLANS[DEFAULT_PLAN])
