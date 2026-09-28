"""Informationen zum TLS-Zertifikat der Plattform für die Gateway-Einrichtung (Zertifikats-Pinning).

Ein selbst signiertes Zertifikat kann ein Raspberry Pi nicht prüfen. Das Portal zeigt deshalb den
SHA-256-Pin des öffentlichen Schlüssels an; `curl --pinnedpubkey` lädt damit das Zertifikat sicher,
und der Agent vertraut danach genau diesem Zertifikat.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtensionOID

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TlsInfo:
    pem: bytes
    pin: str  # "sha256//<base64>" (SPKI, wie curl --pinnedpubkey)
    fingerprint: str  # SHA-256 des Zertifikats, "AA:BB:…" wie openssl -fingerprint
    names: tuple[str, ...]
    not_after: str
    self_signed: bool

    @classmethod
    def load(cls, path: Path | None) -> "TlsInfo | None":
        if not path or not Path(path).is_file():
            return None
        try:
            pem = Path(path).read_bytes()
            cert = x509.load_pem_x509_certificate(pem)
        except (OSError, ValueError) as exc:
            log.warning("TLS-Zertifikat %s nicht lesbar: %s", path, exc)
            return None
        spki = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        pin = "sha256//" + base64.b64encode(hashlib.sha256(spki).digest()).decode()
        fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
        names: list[str] = []
        try:
            san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
            names = [str(v) for v in san.get_values_for_type(x509.DNSName)] + [str(v) for v in san.get_values_for_type(x509.IPAddress)]
        except x509.ExtensionNotFound:
            pass
        return cls(pem=pem, pin=pin, fingerprint=":".join(fp[i:i + 2] for i in range(0, len(fp), 2)), names=tuple(names),
                   not_after=_not_after(cert), self_signed=cert.issuer == cert.subject)


def _not_after(cert: x509.Certificate) -> str:
    # cryptography < 42 kennt nur das naive not_valid_after (UTC).
    t = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")
