"""Eingabeschemata. Strikt: unbekannte Felder verboten, Längen begrenzt, Steuerzeichen abgelehnt."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

ID_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"
SLOT_KEY_PATTERN = r"^[A-Za-z0-9_-]{1,16}$"
_EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _email(v: str) -> str:
    v = v.strip()
    if len(v) > 254 or not _EMAIL_RE.match(v):
        raise ValueError("invalid_email")
    return v.lower()


def _text(v: str) -> str:
    v = v.strip()
    if _CONTROL.search(v):
        raise ValueError("control_characters")
    return v


Email = Annotated[str, AfterValidator(_email)]
Name = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(_text)]
ShortText = Annotated[str, Field(max_length=200), AfterValidator(_text)]
Password = Annotated[str, Field(min_length=1, max_length=128)]
Token = Annotated[str, Field(min_length=10, max_length=200)]
Locale = Literal["de", "nl", "en"]
TenantRole = Literal["owner", "admin", "operator", "viewer"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------- Telemetrie (Gateway)
class MeasurementIn(Strict):
    station_id: str = Field(pattern=ID_PATTERN)
    # Veraltet: eine Station hat genau einen Stellplatz. Wird nur noch für alte Gateways akzeptiert ("A").
    slot_id: str | None = Field(default=None, pattern=SLOT_KEY_PATTERN)
    # Vom Gateway vergeben, monoton steigend; verhindert Doppelungen bei Wiederholung.
    sequence: int = Field(ge=0, le=2**62)
    # None nur zusammen mit sensor_state="error".
    occupied: bool | None
    vibration_score: int = Field(default=0, ge=0, le=1023)
    sensor_state: Literal["ok", "error"]
    # Simulierte Daten müssen als solche gekennzeichnet sein (Plan 9.3, 15).
    source: Literal["live", "simulated"] = "live"
    # Wie lange die Nachricht im Puffer des Gateways lag (Millisekunden). Der Pi hat
    # oft keine Echtzeituhr; daher meldet er das Alter statt eines eigenen Zeitstempels.
    age_ms: int = Field(default=0, ge=0, le=86_400_000)

    @model_validator(mode="after")
    def _consistent(self) -> "MeasurementIn":
        if self.sensor_state == "ok" and self.occupied is None:
            raise ValueError("sensor_state 'ok' erfordert occupied true/false")
        return self


class MeasurementBatchIn(Strict):
    measurements: list[MeasurementIn] = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------- Authentifizierung
class RegisterIn(Strict):
    org_name: Name
    name: Name
    email: Email
    password: Password
    accept_terms: Literal[True]
    locale: Locale = "de"


class LoginIn(Strict):
    email: Annotated[str, Field(max_length=254)]
    password: Password


class MfaLoginIn(Strict):
    mfa_token: Token
    code: Annotated[str, Field(min_length=6, max_length=20)]


class EmailIn(Strict):
    email: Annotated[str, Field(max_length=254)]


class TokenIn(Strict):
    token: Token


class ResetIn(Strict):
    token: Token
    password: Password


class PasswordChangeIn(Strict):
    current_password: Password
    new_password: Password


class CodeIn(Strict):
    code: Annotated[str, Field(min_length=6, max_length=20)]


class MfaDisableIn(Strict):
    password: Password
    code: Annotated[str, Field(min_length=6, max_length=20)]


class PasswordConfirmIn(Strict):
    password: Password


class InviteAcceptIn(Strict):
    token: Token
    name: Name
    password: Password


class ProfilePatch(Strict):
    name: Name | None = None
    locale: Locale | None = None


# ---------------------------------------------------------------------- Organisation
class OrgPatch(Strict):
    name: Name | None = None
    mfa_required: bool | None = None


class RoleIn(Strict):
    role: TenantRole


class InviteIn(Strict):
    email: Email
    role: Literal["admin", "operator", "viewer"]


class PlanIn(Strict):
    plan: Annotated[str, Field(pattern=r"^[a-z]{1,20}$")]


class DeleteOrgIn(Strict):
    password: Password
    confirm_name: Annotated[str, Field(max_length=100)]


# ---------------------------------------------------------------------- Stationen
class StationIn(Strict):
    name: Name
    location: ShortText = ""


class TariffIn(Strict):
    mode: Literal["free", "flat", "per_hour", "per_day"]
    price_cents: int = Field(ge=0, le=100_000)
    free_minutes: int = Field(default=0, ge=0, le=24 * 60)
    daily_cap_cents: int | None = Field(default=None, ge=0, le=100_000)


class StationPatch(Strict):
    name: Name | None = None
    location: ShortText | None = None
    alert_source: Literal["rule", "ml"] | None = None
    display_enabled: bool | None = None
    auto_update: bool | None = None
    stall_view_enabled: bool | None = None
    maintenance: bool | None = None


class DeviceIn(Strict):
    name: Name


# ---------------------------------------------------------------------- Plattform
class TenantPatch(Strict):
    status: Literal["active", "suspended"] | None = None
    plan: Annotated[str, Field(pattern=r"^[a-z]{1,20}$")] | None = None


# ---------------------------------------------------------------------- NFC / Parken / Abrechnung
NfcUid = Annotated[str, Field(min_length=8, max_length=40, pattern=r"^[0-9A-Fa-f:\- ]+$")]


class TapIn(Strict):
    station_id: str = Field(pattern=ID_PATTERN)
    sequence: int = Field(ge=0, le=2**62)
    uid: NfcUid
    age_ms: int = Field(default=0, ge=0, le=86_400_000)
    source: Literal["live", "simulated"] = "live"


class CardIn(Strict):
    uid: NfcUid
    label: Name


class CardPatch(Strict):
    label: Name | None = None
    status: Literal["active", "blocked"] | None = None


class StationTariffIn(Strict):
    tariff: TariffIn | None  # None = Tarif der Organisation


class PaidIn(Strict):
    paid: bool


class LicenseIn(Strict):
    valid_until: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    price_per_stall_day_cents: int | None = Field(default=None, ge=0, le=100_000)
    base_month_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    notes: ShortText = ""


class InvoiceIn(Strict):
    tenant_id: str = Field(pattern=ID_PATTERN)
    month: Annotated[str, Field(pattern=r"^\d{4}-\d{2}$")]


class InvoicePatch(Strict):
    status: Literal["open", "paid", "void"]


class ReportIn(Strict):
    category: Literal["damaged", "blocked", "wrong_status", "other"]
    text: Annotated[str, Field(max_length=300), AfterValidator(_text)] = ""


class CameraIn(Strict):
    enabled: bool
    retention_h: int = Field(default=24, ge=1, le=72)
    # Wer die Kamera genehmigt hat (z. B. "Schulleitung, 28.09.2026") – Pflicht beim Einschalten.
    approved_by: ShortText | None = None
