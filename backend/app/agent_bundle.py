"""Agent-Paket für Raspberry Pis: reproduzierbar gebaut, per SHA-256 prüfbar, mit Installationsskript."""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2] / "pi-gateway"
TEMPLATE = Path(__file__).resolve().parent / "templates" / "install-agent.sh"
FILES = ("VERSION", "agent.py", "gateway.py", "simulator.py", "camera.py", "sim-camera.jpg")


def version_tuple(v: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in (v or "").strip().split("."))
    except ValueError:
        return ()


@dataclass(frozen=True)
class AgentBundle:
    version: str
    data: bytes
    sha256: str
    script: bytes
    script_sha256: str

    @classmethod
    def build(cls, base_url: str, agent_dir: Path = AGENT_DIR, pin: str = "") -> "AgentBundle":
        version = (agent_dir / "VERSION").read_text().strip()
        raw = io.BytesIO()
        # Reproduzierbar: feste Reihenfolge, Zeitstempel 0, keine Besitzer -> gleiche Prüfsumme bei gleichem Inhalt.
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name in FILES:
                content = (agent_dir / name).read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o755 if name.endswith(".py") else 0o644
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tar.addfile(info, io.BytesIO(content))
        data = gzip.compress(raw.getvalue(), mtime=0)
        sha = hashlib.sha256(data).hexdigest()
        script = (
            TEMPLATE.read_text()
            .replace("__BASE_URL__", base_url)
            .replace("__VERSION__", version)
            .replace("__SHA256__", sha)
            .replace("__PIN__", pin)
            .encode()
        )
        return cls(version, data, sha, script, hashlib.sha256(script).hexdigest())
