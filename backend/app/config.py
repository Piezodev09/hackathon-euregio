"""Konfiguration: nicht-geheime Werte aus config.toml, Geheimnisse aus Umgebungsvariablen."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SlotConfig:
    id: str
    label: str
    position: int


@dataclass(frozen=True)
class Settings:
    station_id: str
    station_name: str
    slots: tuple[SlotConfig, ...]
    stale_after_s: float
    ui_poll_interval_s: float
    alert_source: str
    window_s: float
    peak_threshold: int
    min_peaks: int
    grace_period_s: float
    cooldown_s: float
    model_path: Path | None
    measurements_max_age_h: float
    write_rate_per_s: float
    write_burst: int
    read_rate_per_s: float
    read_burst: int
    db_path: Path
    device_tokens: frozenset[str] = field(repr=False)
    admin_tokens: frozenset[str] = field(repr=False)
    trust_proxy: bool = False

    def slot_ids(self) -> set[str]:
        return {s.id for s in self.slots}


def _tokens(name: str) -> frozenset[str]:
    raw = os.environ.get(name, "")
    tokens = {t.strip() for t in raw.split(",") if t.strip()}
    # Zu kurze Tokens ablehnen – keine Standardpasswörter wie "admin".
    weak = [t for t in tokens if len(t) < 16]
    if weak:
        raise ValueError(f"{name}: Tokens müssen mindestens 16 Zeichen lang sein")
    return frozenset(tokens)


def load_settings(config_path: str | os.PathLike | None = None) -> Settings:
    path = Path(config_path or os.environ.get("BIKE_CONFIG", BACKEND_DIR / "config.toml"))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    st = cfg["station"]
    slots = tuple(
        sorted(
            (SlotConfig(id=str(s["id"]), label=str(s["label"]), position=int(s["position"])) for s in st["slots"]),
            key=lambda s: s.position,
        )
    )
    timing = cfg.get("timing", {})
    an = cfg.get("anomaly", {})
    ret = cfg.get("retention", {})
    sec = cfg.get("security", {})

    alert_source = an.get("alert_source", "rule")
    if alert_source not in ("rule", "ml"):
        raise ValueError("anomaly.alert_source muss 'rule' oder 'ml' sein")

    model_path = an.get("model_path")
    if model_path:
        mp = Path(model_path)
        model_path = mp if mp.is_absolute() else (path.parent / mp).resolve()

    db_path = Path(os.environ.get("BIKE_DB_PATH", BACKEND_DIR / "bike_station.db"))

    return Settings(
        station_id=str(st["id"]),
        station_name=str(st.get("display_name", st["id"])),
        slots=slots,
        stale_after_s=float(timing.get("stale_after_s", 30)),
        ui_poll_interval_s=float(timing.get("ui_poll_interval_s", 2)),
        alert_source=alert_source,
        window_s=float(an.get("window_s", 10)),
        peak_threshold=int(an.get("peak_threshold", 300)),
        min_peaks=int(an.get("min_peaks", 3)),
        grace_period_s=float(an.get("grace_period_s", 15)),
        cooldown_s=float(an.get("cooldown_s", 30)),
        model_path=model_path or None,
        measurements_max_age_h=float(ret.get("measurements_max_age_h", 72)),
        write_rate_per_s=float(sec.get("write_rate_per_s", 20)),
        write_burst=int(sec.get("write_burst", 60)),
        read_rate_per_s=float(sec.get("read_rate_per_s", 10)),
        read_burst=int(sec.get("read_burst", 40)),
        db_path=db_path,
        device_tokens=_tokens("BIKE_DEVICE_TOKENS"),
        admin_tokens=_tokens("BIKE_ADMIN_TOKENS"),
        trust_proxy=os.environ.get("BIKE_TRUST_PROXY", "0") == "1",
    )
