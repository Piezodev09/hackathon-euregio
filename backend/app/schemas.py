"""Nachrichtenformat Pi -> API (Plan 5.1). Strikte Validierung, unbekannte Felder werden abgelehnt."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"


class MeasurementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: str = Field(pattern=ID_PATTERN)
    slot_id: str = Field(pattern=ID_PATTERN)
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


class MeasurementBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurements: list[MeasurementIn] = Field(min_length=1, max_length=200)
