"""Ablage der Kamera-Einzelbilder: Dateien im Datenverzeichnis, Metadaten in der Datenbank."""

from __future__ import annotations

import os
from pathlib import Path

from .core import Core
from .security import new_id

JPEG_MAGIC = b"\xff\xd8\xff"
MAX_BYTES = 2 * 1024 * 1024


class Snapshots:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db
        base = Path(core.s.db_path).parent if str(core.s.db_path) != ":memory:" else Path(".")
        self.dir = base / "snapshots"

    def _path(self, file: str) -> Path:
        # Dateiname stammt nur aus new_id() -> keine Pfadtricks möglich; trotzdem prüfen.
        if not file or "/" in file or "\\" in file or file.startswith("."):
            raise ValueError("invalid_file")
        return self.dir / file

    def store(self, station, data: bytes, reason: str, event_id: str | None, source: str) -> dict:
        if not data.startswith(JPEG_MAGIC) or len(data) > MAX_BYTES:
            raise ValueError("invalid_image")
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        sid = new_id("snap")
        file = f"{sid}.jpg"
        path = self._path(file)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        now = self.core.clock()
        self.db.execute(
            "INSERT INTO snapshot (id, tenant_id, station_id, event_id, reason, taken_at, expires_at, size, file, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, station["tenant_id"], station["id"], event_id, reason, now, now + station["camera_retention_h"] * 3600, len(data), file, source))
        return {"id": sid, "size": len(data)}

    def read(self, row) -> bytes | None:
        try:
            return self._path(row["file"]).read_bytes()
        except (OSError, ValueError):
            return None

    def delete_rows(self, rows) -> int:
        n = 0
        for r in rows:
            try:
                self._path(r["file"]).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            self.db.execute("DELETE FROM snapshot WHERE id = ?", (r["id"],))
            n += 1
        return n

    def purge(self) -> int:
        """Abgelaufene Bilder löschen und verwaiste Dateien (z. B. nach Löschen einer Station) entfernen."""
        n = self.delete_rows(self.db.all("SELECT id, file FROM snapshot WHERE expires_at <= ?", (self.core.clock(),)))
        if self.dir.is_dir():
            known = {r["file"] for r in self.db.all("SELECT file FROM snapshot")}
            for p in self.dir.glob("*.jpg"):
                if p.name not in known:
                    p.unlink(missing_ok=True)
                    n += 1
        return n
