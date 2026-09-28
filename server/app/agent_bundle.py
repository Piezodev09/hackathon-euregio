"""Agent package for Raspberry Pis: built reproducibly from ``agent/``, verifiable via SHA-256, with install script."""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
TEMPLATE = Path(__file__).resolve().parent / "templates" / "install-agent.sh"
PACKAGE = "bikeagent"


def version_tuple(v: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in (v or "").strip().split("."))
    except ValueError:
        return ()


def package_files(agent_dir: Path) -> list[str]:
    """Files shipped in the tarball: ``VERSION`` plus every module of the ``bikeagent`` package."""
    return ["VERSION", *sorted(f"{PACKAGE}/{p.name}" for p in (agent_dir / PACKAGE).glob("*.py"))]


@dataclass(frozen=True)
class AgentBundle:
    version: str
    data: bytes
    sha256: str
    script: bytes
    script_sha256: str

    @classmethod
    def build(cls, base_url: str, agent_dir: Path = AGENT_DIR, ca_pem: str = "") -> "AgentBundle":
        version = (agent_dir / "VERSION").read_text().strip()
        raw = io.BytesIO()
        # Reproducible: fixed order, timestamp 0, no owners -> same checksum for the same content.
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name in package_files(agent_dir):
                content = (agent_dir / name).read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o644
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
            .replace("__CA_PEM__", ca_pem.strip())
            .encode()
        )
        return cls(version, data, sha, script, hashlib.sha256(script).hexdigest())
