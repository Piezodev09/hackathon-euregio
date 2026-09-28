"""Input schemas. Strict: unknown fields forbidden, lengths limited, control characters rejected."""

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


# ---------------------------------------------------------------------- telemetry (gateway)
class MeasurementIn(Strict):
    station_id: str = Field(pattern=ID_PATTERN)
    slot_id: str = Field(pattern=SLOT_KEY_PATTERN)  # slot key ("A")
    # Assigned by the gateway, monotonically increasing; prevents duplicates on retries.
    sequence: int = Field(ge=0, le=2**62)
    # None only together with sensor_state="error".
    occupied: bool | None
    vibration_score: int = Field(default=0, ge=0, le=1023)
    sensor_state: Literal["ok", "error"]
    # Simulated data must be labelled as such (plan 9.3, 15).
    source: Literal["live", "simulated"] = "live"
    # How long the message waited in the gateway buffer (milliseconds). The Pi often has no
    # real-time clock, so it reports the age instead of its own timestamp.
    age_ms: int = Field(default=0, ge=0, le=86_400_000)

    @model_validator(mode="after")
    def _consistent(self) -> "MeasurementIn":
        if self.sensor_state == "ok" and self.occupied is None:
            raise ValueError("sensor_state 'ok' requires occupied true/false")
        return self


class MeasurementBatchIn(Strict):
    measurements: list[MeasurementIn] = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------- authentication
class RegisterIn(Strict):
    org_name: Name
    name: Name
    email: Email
    password: Password
    accept_terms: Literal[True]
    locale: Locale = "en"


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


# ---------------------------------------------------------------------- organisation
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


# ---------------------------------------------------------------------- stations
class SlotIn(Strict):
    key: str = Field(pattern=SLOT_KEY_PATTERN)
    label: Name


class SlotPatch(Strict):
    label: Name | None = None
    position: int | None = Field(default=None, ge=1, le=10_000)


class StationIn(Strict):
    name: Name
    location: ShortText = ""
    slots: list[SlotIn] = Field(default_factory=list, max_length=200)


class StationPatch(Strict):
    name: Name | None = None
    location: ShortText | None = None
    alert_source: Literal["rule", "ml"] | None = None
    display_enabled: bool | None = None
    auto_update: bool | None = None


class DeviceIn(Strict):
    name: Name


# ---------------------------------------------------------------------- platform
class TenantPatch(Strict):
    status: Literal["active", "suspended"] | None = None
    plan: Annotated[str, Field(pattern=r"^[a-z]{1,20}$")] | None = None
