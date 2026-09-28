"""SQLite-Datenhaltung (Plan 7.1). Bewusst einfach: eine Datei, keine ORM-Abhängigkeit."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS station (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slot (
    id          TEXT NOT NULL,
    station_id  TEXT NOT NULL REFERENCES station(id),
    label       TEXT NOT NULL,
    position    INTEGER NOT NULL,
    PRIMARY KEY (station_id, id)
);

CREATE TABLE IF NOT EXISTS measurement (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id       TEXT NOT NULL,
    slot_id          TEXT NOT NULL,
    sequence         INTEGER NOT NULL,
    server_time      REAL NOT NULL,          -- Unix-Zeit (UTC), vom Server gesetzt
    occupied         INTEGER,                -- 1/0, NULL = unbekannt
    vibration_score  INTEGER NOT NULL DEFAULT 0,
    sensor_state     TEXT NOT NULL,          -- ok | error
    source           TEXT NOT NULL DEFAULT 'live',  -- live | simulated
    UNIQUE (station_id, slot_id, sequence),
    FOREIGN KEY (station_id, slot_id) REFERENCES slot(station_id, id)
);
CREATE INDEX IF NOT EXISTS idx_measurement_slot_time
    ON measurement (station_id, slot_id, server_time);

CREATE TABLE IF NOT EXISTS event (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id       TEXT NOT NULL,
    slot_id          TEXT NOT NULL,
    kind             TEXT NOT NULL,          -- unusual_movement | sensor_fault
    severity         TEXT NOT NULL,          -- warning | info
    detector         TEXT,                   -- rule | ml (nur bei unusual_movement)
    detail           TEXT,
    occurred_at      REAL NOT NULL,
    acknowledged_at  REAL,
    source           TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS idx_event_time ON event (occurred_at);

-- Protokoll für Admin-Aktionen und abgewiesene Schreibversuche (ohne Geheimnisse).
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    action    TEXT NOT NULL,
    client    TEXT,
    detail    TEXT
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        # Eine Verbindung, durch Lock geschützt – ausreichend für wenige Demo-Plätze.
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def ping(self) -> bool:
        try:
            self.query("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def seed(self, settings: Settings) -> None:
        """Station und Plätze aus der Konfiguration anlegen/aktualisieren."""
        with self.tx() as c:
            c.execute(
                "INSERT INTO station (id, display_name) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name",
                (settings.station_id, settings.station_name),
            )
            for s in settings.slots:
                c.execute(
                    "INSERT INTO slot (id, station_id, label, position) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(station_id, id) DO UPDATE SET label = excluded.label, position = excluded.position",
                    (s.id, settings.station_id, s.label, s.position),
                )

    def audit(self, at: float, action: str, client: str | None, detail: str | None = None) -> None:
        self.execute(
            "INSERT INTO audit_log (at, action, client, detail) VALUES (?, ?, ?, ?)",
            (at, action, client, detail),
        )

    def purge_old(self, older_than: float) -> int:
        """Löscht Rohmesswerte vor older_than (Datensparsamkeit)."""
        cur = self.execute("DELETE FROM measurement WHERE server_time < ?", (older_than,))
        return cur.rowcount
