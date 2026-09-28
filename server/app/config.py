"""Configuration: non-secret values from config.toml, secrets only from environment variables."""

from __future__ import annotations

import base64
import logging
import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

SERVER_DIR = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    environment: str
    base_url: str
    allowed_hosts: tuple[str, ...]
    signup_enabled: bool
    product_name: str
    # auth
    session_idle_s: float
    session_absolute_s: float
    lockout_threshold: int
    lockout_base_s: float
    password_min_length: int
    scrypt_n: int
    platform_admin_mfa_required: bool
    verify_token_s: float
    reset_token_s: float
    invite_token_s: float
    # mail
    mail_backend: str
    mail_from: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_starttls: bool
    smtp_password: str = field(repr=False)
    # timing / anomaly
    stale_after_s: float = 30
    ui_poll_interval_s: float = 2
    window_s: float = 10
    peak_threshold: int = 300
    min_peaks: int = 3
    grace_period_s: float = 15
    cooldown_s: float = 30
    model_path: Path | None = None
    audit_retention_s: float = 365 * 86400
    # security
    read_rate_per_s: float = 20
    read_burst: int = 60
    device_rate_per_s: float = 20
    device_burst: int = 60
    auth_per_minute: int = 10
    trust_proxy: bool = False
    # paths / secrets
    db_path: Path = SERVER_DIR / "bike_station.db"
    data_key: bytes = field(default=b"", repr=False)

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def origin(self) -> str:
        u = urlparse(self.base_url)
        return f"{u.scheme}://{u.netloc}"


def _data_key(environment: str, db_path: Path) -> bytes:
    """32-byte key for encrypting stored secrets (e.g. 2FA)."""
    raw = os.environ.get("BIKE_DATA_KEY", "")
    if raw:
        try:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except Exception as exc:
            raise ConfigError("BIKE_DATA_KEY is not valid base64") from exc
        if len(key) != 32:
            raise ConfigError("BIKE_DATA_KEY must be 32 bytes (base64)")
        return key
    if environment == "production":
        raise ConfigError("BIKE_DATA_KEY must be set in production")
    # Development: keep the key next to the database (gitignored) so 2FA survives restarts.
    key_file = db_path.with_suffix(".datakey")
    if key_file.exists():
        return base64.urlsafe_b64decode(key_file.read_text().strip())
    key = secrets.token_bytes(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(base64.urlsafe_b64encode(key).decode())
        os.chmod(key_file, 0o600)
        log.warning("Development data key created: %s (not for production!)", key_file)
    except OSError:
        log.warning("Volatile development data key - 2FA will not survive a restart")
    return key


def load_settings(config_path: str | os.PathLike | None = None) -> Settings:
    path = Path(config_path or os.environ.get("BIKE_CONFIG", SERVER_DIR / "config.toml"))
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    app = cfg.get("app", {})
    auth = cfg.get("auth", {})
    mail = cfg.get("mail", {})
    timing = cfg.get("timing", {})
    an = cfg.get("anomaly", {})
    ret = cfg.get("retention", {})
    sec = cfg.get("security", {})

    environment = os.environ.get("BIKE_ENV", app.get("environment", "development"))
    if environment not in ("development", "production"):
        raise ConfigError("app.environment must be development or production")
    base_url = os.environ.get("BIKE_BASE_URL", app.get("base_url", "http://127.0.0.1:8000")).rstrip("/")
    hosts_env = os.environ.get("BIKE_ALLOWED_HOSTS")
    allowed_hosts = tuple(h.strip() for h in hosts_env.split(",")) if hosts_env else tuple(app.get("allowed_hosts", ["*"]))

    model_path = an.get("model_path")
    if model_path:
        mp = Path(model_path)
        model_path = mp if mp.is_absolute() else (path.parent / mp).resolve()

    db_path = Path(os.environ.get("BIKE_DB_PATH", SERVER_DIR / "bike_station.db"))
    mail_backend = os.environ.get("BIKE_MAIL_BACKEND", mail.get("backend", "console"))
    scrypt_n = int(os.environ.get("BIKE_SCRYPT_N", auth.get("scrypt_n", 32768)))

    s = Settings(
        environment=environment,
        base_url=base_url,
        allowed_hosts=allowed_hosts,
        signup_enabled=bool(app.get("signup_enabled", True)),
        product_name=str(app.get("product_name", "Smart Bike Station")),
        session_idle_s=float(auth.get("session_idle_minutes", 60)) * 60,
        session_absolute_s=float(auth.get("session_absolute_hours", 12)) * 3600,
        lockout_threshold=int(auth.get("lockout_threshold", 5)),
        lockout_base_s=float(auth.get("lockout_base_minutes", 5)) * 60,
        password_min_length=max(8, int(auth.get("password_min_length", 12))),
        scrypt_n=scrypt_n,
        platform_admin_mfa_required=bool(auth.get("platform_admin_mfa_required", True)),
        verify_token_s=float(auth.get("verify_token_hours", 48)) * 3600,
        reset_token_s=float(auth.get("reset_token_hours", 1)) * 3600,
        invite_token_s=float(auth.get("invite_token_hours", 72)) * 3600,
        mail_backend=mail_backend,
        mail_from=str(mail.get("from", "no-reply@example.org")),
        smtp_host=os.environ.get("BIKE_SMTP_HOST", mail.get("smtp_host", "")),
        smtp_port=int(os.environ.get("BIKE_SMTP_PORT", mail.get("smtp_port", 587))),
        smtp_user=os.environ.get("BIKE_SMTP_USER", mail.get("smtp_user", "")),
        smtp_starttls=bool(mail.get("smtp_starttls", True)),
        smtp_password=os.environ.get("BIKE_SMTP_PASSWORD", ""),
        stale_after_s=float(timing.get("stale_after_s", 30)),
        ui_poll_interval_s=float(timing.get("ui_poll_interval_s", 2)),
        window_s=float(an.get("window_s", 10)),
        peak_threshold=int(an.get("peak_threshold", 300)),
        min_peaks=int(an.get("min_peaks", 3)),
        grace_period_s=float(an.get("grace_period_s", 15)),
        cooldown_s=float(an.get("cooldown_s", 30)),
        model_path=model_path or None,
        audit_retention_s=float(ret.get("audit_days", 365)) * 86400,
        read_rate_per_s=float(sec.get("read_rate_per_s", 20)),
        read_burst=int(sec.get("read_burst", 60)),
        device_rate_per_s=float(sec.get("device_rate_per_s", 20)),
        device_burst=int(sec.get("device_burst", 60)),
        auth_per_minute=int(sec.get("auth_per_minute", 10)),
        trust_proxy=os.environ.get("BIKE_TRUST_PROXY", str(sec.get("trust_proxy", False))).lower() in ("1", "true"),
        db_path=db_path,
        data_key=_data_key(environment, db_path),
    )
    validate(s)
    return s


def validate(s: Settings) -> None:
    """Reject insecure combinations in production (secure by default)."""
    if not s.production:
        return
    problems = []
    if not s.base_url.startswith("https://"):
        problems.append("base_url must be https://")
    if "*" in s.allowed_hosts:
        problems.append("allowed_hosts must not contain '*' in production")
    if s.mail_backend != "smtp":
        problems.append("mail.backend must be 'smtp' in production")
    if s.scrypt_n < 2**14:
        problems.append("scrypt_n too small")
    if s.password_min_length < 12:
        problems.append("password_min_length must be at least 12")
    if problems:
        raise ConfigError("Insecure configuration for production: " + "; ".join(problems))
