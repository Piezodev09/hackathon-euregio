"""Kryptografische Bausteine: Passwort-Hashing, Richtlinie, Zufallstokens, TOTP (RFC 6238), Verschlüsselung.

Bewusst nur geprüfte Primitive: hashlib.scrypt, hmac, secrets, AES-GCM (cryptography).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

# ---------------------------------------------------------------------- IDs und Tokens


def new_id(prefix: str) -> str:
    """Nicht erratbare Ressourcen-ID (keine fortlaufenden Nummern -> keine Aufzählung)."""
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def new_token(prefix: str = "") -> str:
    """Hochentropisches Geheimnis (256 Bit). Wird nur gehasht gespeichert."""
    return prefix + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Für hochentropische Tokens genügt SHA-256 (kein Passwort-Hash nötig)."""
    return hashlib.sha256(token.encode()).hexdigest()


def safe_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ---------------------------------------------------------------------- Passwörter

SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str, n: int = 2**15) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=SCRYPT_R, p=SCRYPT_P, maxmem=128 * 1024 * 1024, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        n, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), maxmem=128 * 1024 * 1024, dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def needs_rehash(stored: str, n: int) -> bool:
    try:
        return int(stored.split("$")[1]) < n
    except (IndexError, ValueError):
        return True


_DUMMY_HASH_CACHE: dict[int, str] = {}


def dummy_verify(password: str, n: int) -> None:
    """Gleicher Zeitaufwand für unbekannte Konten (verhindert Konto-Aufzählung über Antwortzeit)."""
    if n not in _DUMMY_HASH_CACHE:
        _DUMMY_HASH_CACHE[n] = hash_password(secrets.token_hex(16), n)
    verify_password(password, _DUMMY_HASH_CACHE[n])


# Auszug der häufigsten Passwörter; ergänzt durch strukturelle Prüfungen.
COMMON_PASSWORDS = frozenset(
    """
    123456789012 password1234 passwort1234 qwertzuiop12 qwertyuiop12 1234567890ab iloveyou1234
    welcome12345 willkommen12 admin1234567 letmein12345 fahrrad12345 radstation12 sommer202412
    hallo1234567 password123! passwort123! 000000000000 111111111111 abc123456789 changeme1234
    """.split()
)


def password_problems(password: str, min_length: int, email: str = "", name: str = "") -> list[str]:
    """Liefert Fehlercodes; leer = in Ordnung. Orientiert an NIST SP 800-63B (Länge statt Zeichenklassen)."""
    problems = []
    if len(password) < min_length:
        problems.append("too_short")
    if len(password) > 128:
        problems.append("too_long")
    low = password.lower()
    if low in COMMON_PASSWORDS:
        problems.append("too_common")
    if len(set(password)) < 5:
        problems.append("too_simple")
    local = email.split("@")[0].lower()
    if len(local) >= 4 and local in low:
        problems.append("contains_email")
    for part in name.lower().split():
        if len(part) >= 4 and part in low:
            problems.append("contains_name")
            break
    return problems


# ---------------------------------------------------------------------- TOTP (RFC 6238)

TOTP_STEP = 30
TOTP_DIGITS = 6


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**TOTP_DIGITS
    return str(code).zfill(TOTP_DIGITS)


def totp_now(secret_b32: str, t: float | None = None) -> str:
    return _hotp(secret_b32, int((t if t is not None else time.time()) // TOTP_STEP))


def totp_verify(secret_b32: str, code: str, last_step: int | None, t: float | None = None) -> int | None:
    """Prüft Code mit ±1 Zeitschritt Toleranz. Gibt den genutzten Schritt zurück (Replay-Schutz) oder None."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != TOTP_DIGITS:
        return None
    step = int((t if t is not None else time.time()) // TOTP_STEP)
    for s in (step - 1, step, step + 1):
        if last_step is not None and s <= last_step:
            continue  # bereits benutzter Code darf nicht erneut gelten
        if hmac.compare_digest(_hotp(secret_b32, s), code):
            return s
    return None


def totp_uri(secret_b32: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def new_recovery_codes(n: int = 10) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(2)) for _ in range(n)]


def normalize_recovery_code(code: str) -> str:
    return code.strip().lower().replace(" ", "")


# ---------------------------------------------------------------------- Verschlüsselung ruhender Geheimnisse


class SecretBox:
    """AES-256-GCM mit zufälliger Nonce; Associated Data bindet den Geheimtext an seinen Zweck/Datensatz."""

    VERSION = b"\x01"

    def __init__(self, key: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(key) != 32:
            raise ValueError("Schlüssel muss 32 Byte lang sein")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, aad: str) -> bytes:
        nonce = os.urandom(12)
        return self.VERSION + nonce + self._aead.encrypt(nonce, plaintext.encode(), aad.encode())

    def decrypt(self, blob: bytes, aad: str) -> str:
        if not blob or blob[:1] != self.VERSION:
            raise ValueError("unbekanntes Format")
        return self._aead.decrypt(blob[1:13], blob[13:], aad.encode()).decode()
