"""SQLite storage, multi-tenant. Every business table carries tenant_id or belongs to a station.

Schema changes: bump SCHEMA_VERSION and add a ``_migrate_<n>_to_<n+1>`` step (idempotent ALTER TABLEs).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 4

EVENT_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id               TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    station_id       TEXT NOT NULL REFERENCES station(id) ON DELETE CASCADE,
    slot_id          TEXT REFERENCES slot(id) ON DELETE CASCADE,        -- NULL for gateway events
    device_id        TEXT REFERENCES device(id) ON DELETE CASCADE,      -- set for gateway events
    kind             TEXT NOT NULL,          -- unusual_movement | sensor_fault | gateway_offline | gateway_online
    severity         TEXT NOT NULL,          -- warning | info | shadow
    detector         TEXT,
    detail           TEXT,
    occurred_at      REAL NOT NULL,
    acknowledged_at  REAL,
    acknowledged_by  TEXT,
    source           TEXT NOT NULL DEFAULT 'live'
);
"""
EVENT_COLUMNS = ("id", "tenant_id", "station_id", "slot_id", "kind", "severity", "detector", "detail", "occurred_at",
                 "acknowledged_at", "acknowledged_by", "source")


SCHEMA = """
CREATE TABLE IF NOT EXISTS tenant (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT 'free',
    status          TEXT NOT NULL DEFAULT 'active',      -- active | pending (awaiting approval) | suspended
    mfa_required    INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS user (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT REFERENCES tenant(id) ON DELETE CASCADE,   -- NULL = platform admin
    email               TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name                TEXT NOT NULL,
    role                TEXT NOT NULL,          -- owner | admin | operator | viewer | platform
    password_hash       TEXT NOT NULL,
    email_verified_at   REAL,
    is_platform_admin   INTEGER NOT NULL DEFAULT 0,
    failed_logins       INTEGER NOT NULL DEFAULT 0,
    locked_until        REAL,
    totp_secret_enc     BLOB,
    totp_pending_enc    BLOB,
    totp_enabled        INTEGER NOT NULL DEFAULT 0,
    totp_last_step      INTEGER,
    locale              TEXT NOT NULL DEFAULT 'de',
    created_at          REAL NOT NULL,
    password_changed_at REAL NOT NULL,
    last_login_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_user_tenant ON user (tenant_id);

CREATE TABLE IF NOT EXISTS session (
    token_hash    TEXT PRIMARY KEY,
    public_id     TEXT NOT NULL UNIQUE,
    user_id       TEXT NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    csrf_token    TEXT NOT NULL,
    created_at    REAL NOT NULL,
    last_seen_at  REAL NOT NULL,
    expires_at    REAL NOT NULL,
    ip            TEXT,
    user_agent    TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_user ON session (user_id);

-- One-time tokens for e-mail verification, password reset, invitation, 2FA intermediate step.
CREATE TABLE IF NOT EXISTS auth_token (
    token_hash  TEXT PRIMARY KEY,
    public_id   TEXT NOT NULL UNIQUE,
    purpose     TEXT NOT NULL,          -- verify | reset | invite | mfa
    user_id     TEXT REFERENCES user(id) ON DELETE CASCADE,
    tenant_id   TEXT REFERENCES tenant(id) ON DELETE CASCADE,
    email       TEXT,
    role        TEXT,
    created_by  TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL
);

CREATE TABLE IF NOT EXISTS recovery_code (
    user_id    TEXT NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    used_at    REAL,
    PRIMARY KEY (user_id, code_hash)
);

CREATE TABLE IF NOT EXISTS station (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    location            TEXT NOT NULL DEFAULT '',
    alert_source        TEXT NOT NULL DEFAULT 'rule',   -- rule | ml
    display_token_hash  TEXT UNIQUE,
    display_enabled     INTEGER NOT NULL DEFAULT 0,
    config_version      INTEGER NOT NULL DEFAULT 1,     -- increases on changes that affect gateways
    auto_update         INTEGER NOT NULL DEFAULT 1,     -- install agent updates automatically
    created_at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_station_tenant ON station (tenant_id);

CREATE TABLE IF NOT EXISTS slot (
    id          TEXT PRIMARY KEY,
    station_id  TEXT NOT NULL REFERENCES station(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,          -- key used by the Arduino/gateway, e.g. "A"
    label       TEXT NOT NULL,
    position    INTEGER NOT NULL,
    UNIQUE (station_id, key)
);

CREATE TABLE IF NOT EXISTS device (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    station_id    TEXT NOT NULL REFERENCES station(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    token_prefix  TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    created_at    REAL NOT NULL,
    created_by    TEXT,
    last_seen_at  REAL,
    last_ip       TEXT,
    revoked_at    REAL,
    -- Agent (Raspberry Pi)
    enrolled_at             REAL,
    hostname                TEXT,
    agent_version           TEXT,
    os_info                 TEXT,
    source                  TEXT,
    last_heartbeat_at       REAL,
    health                  TEXT,
    pending_command         TEXT,       -- restart | rotate_token (fixed commands only, never code)
    update_requested        INTEGER NOT NULL DEFAULT 0,
    prev_token_hash         TEXT,       -- old token stays valid briefly (rotation without lock-out)
    prev_token_valid_until  REAL,
    token_rotated_at        REAL,
    offline_notified        INTEGER NOT NULL DEFAULT 0   -- a gateway_offline event was raised
);
CREATE INDEX IF NOT EXISTS idx_device_prev_token ON device (prev_token_hash);

-- Pairing codes for the agent installation (one-time, short-lived, stored only as hash).
CREATE TABLE IF NOT EXISTS enrollment (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    station_id  TEXT NOT NULL REFERENCES station(id) ON DELETE CASCADE,
    code_hash   TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_by  TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL,
    device_id   TEXT
);

CREATE TABLE IF NOT EXISTS measurement (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id       TEXT NOT NULL REFERENCES station(id) ON DELETE CASCADE,
    slot_id          TEXT NOT NULL REFERENCES slot(id) ON DELETE CASCADE,
    sequence         INTEGER NOT NULL,
    server_time      REAL NOT NULL,
    occupied         INTEGER,
    vibration_score  INTEGER NOT NULL DEFAULT 0,
    sensor_state     TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'live',
    UNIQUE (slot_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_measurement_slot_time ON measurement (slot_id, server_time);
CREATE INDEX IF NOT EXISTS idx_measurement_station_time ON measurement (station_id, server_time);

""" + EVENT_TABLE.format(name="event") + """CREATE INDEX IF NOT EXISTS idx_event_tenant_time ON event (tenant_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_event_slot ON event (slot_id, kind, severity, occurred_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id  TEXT,
    user_id    TEXT,
    actor      TEXT,
    action     TEXT NOT NULL,
    target     TEXT,
    ip         TEXT,
    at         REAL NOT NULL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_log (tenant_id, at);

-- Platform-wide settings set at runtime (e.g. the demo station shown on the landing page).
CREATE TABLE IF NOT EXISTS setting (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

-- Demo requests from the landing page (no IP address stored; deleted after 180 days).
CREATE TABLE IF NOT EXISTS lead (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    organisation  TEXT NOT NULL,
    email         TEXT NOT NULL,
    message       TEXT NOT NULL DEFAULT '',
    locale        TEXT,
    created_at    REAL NOT NULL,
    handled_at    REAL,
    handled_by    TEXT
);

-- Read-only API keys for integrations (only the hash is stored).
CREATE TABLE IF NOT EXISTS api_key (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    prefix        TEXT NOT NULL,
    key_hash      TEXT NOT NULL UNIQUE,
    created_at    REAL NOT NULL,
    created_by    TEXT,
    last_used_at  REAL,
    last_ip       TEXT,
    revoked_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_api_key_tenant ON api_key (tenant_id);

-- Outgoing webhooks (HMAC secret encrypted with the data key).
CREATE TABLE IF NOT EXISTS webhook (
    id               TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    url              TEXT NOT NULL,
    kind             TEXT NOT NULL,          -- generic | slack | teams | discord
    events           TEXT NOT NULL,          -- JSON list of event kinds
    secret_enc       BLOB NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1,
    created_at       REAL NOT NULL,
    created_by       TEXT,
    last_status      TEXT,
    last_attempt_at  REAL,
    last_error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_webhook_tenant ON webhook (tenant_id);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA secure_delete=ON")  # overwrite deleted data
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 2:
                self._migrate_2_to_3()
                version = 3
            if version == 3:
                self._migrate_3_to_4()
                version = 4
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(
                    f"Database has schema version {version}, expected {SCHEMA_VERSION}. "
                    "Back up and remove the old database."
                )
            self._conn.executescript(SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if self.path != ":memory:":
            try:
                Path(self.path).chmod(0o600)
            except OSError:
                pass

    def _migrate_2_to_3(self) -> None:
        """Agent management: new columns for station and device."""
        cols = {
            "station": ["config_version INTEGER NOT NULL DEFAULT 1", "auto_update INTEGER NOT NULL DEFAULT 1"],
            "device": ["enrolled_at REAL", "hostname TEXT", "agent_version TEXT", "os_info TEXT", "source TEXT",
                       "last_heartbeat_at REAL", "health TEXT", "pending_command TEXT",
                       "update_requested INTEGER NOT NULL DEFAULT 0", "prev_token_hash TEXT",
                       "prev_token_valid_until REAL", "token_rotated_at REAL"],
        }
        for table, defs in cols.items():
            existing = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for d in defs:
                if d.split()[0] not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {d}")

    def _migrate_3_to_4(self) -> None:
        """Integrations: gateway events without a slot, offline flag per device (new tables come from SCHEMA)."""
        dev_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(device)")}
        if dev_cols and "offline_notified" not in dev_cols:
            self._conn.execute("ALTER TABLE device ADD COLUMN offline_notified INTEGER NOT NULL DEFAULT 0")
        ev = self._conn.execute("PRAGMA table_info(event)").fetchall()
        if not ev or any(r[1] == "device_id" for r in ev):
            return
        # SQLite cannot drop NOT NULL from slot_id: rebuild the table and copy the rows.
        cols = ", ".join(c for c in EVENT_COLUMNS if c in {r[1] for r in ev})
        self._conn.execute("PRAGMA foreign_keys=OFF")
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(EVENT_TABLE.format(name="event_v4"))
            self._conn.execute(f"INSERT INTO event_v4 ({cols}) SELECT {cols} FROM event")
            self._conn.execute("DROP TABLE event")
            self._conn.execute("ALTER TABLE event_v4 RENAME TO event")
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._conn.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.one(sql, params)
        return None if row is None else row[0]

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def ping(self) -> bool:
        try:
            self.one("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()
