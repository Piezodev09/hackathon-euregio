"""Web-Push ohne Zusatzpaket: VAPID (RFC 8292, ES256) und Verschlüsselung der Nachricht (RFC 8291, aes128gcm).

Nur mit `cryptography`. Die Nachricht wird Ende-zu-Ende für den Browser verschlüsselt; der Push-Dienst
(Google/Mozilla/Apple) sieht nur Ziel-Endpunkt und Größe. Endpunkte werden wie Webhook-Ziele geprüft (kein
internes Netz). Abgelaufene Abos (404/410) werden gelöscht.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .security import new_id

log = logging.getLogger("webpush")
RECORD_SIZE = 4096
MAX_FAILURES = 3


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hkdf(salt: bytes, info: bytes, length: int, ikm: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """RFC 8291: Nachricht für genau diesen Browser verschlüsseln (ein Datensatz, aes128gcm)."""
    ua_bytes = b64u_dec(p256dh)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_bytes)
    auth_secret = b64u_dec(auth)
    salt = salt or os.urandom(16)
    as_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_bytes = public_bytes(as_key.public_key())
    shared = as_key.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(auth_secret, b"WebPush: info\x00" + ua_bytes + as_bytes, 32, shared)
    cek = _hkdf(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = _hkdf(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    if len(payload) + 17 > RECORD_SIZE:
        raise ValueError("payload_too_large")
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 = letzter Datensatz, ohne Auffüllung
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_bytes)]) + as_bytes + ct


class Vapid:
    """Schlüsselpaar der Plattform; der öffentliche Schlüssel geht an den Browser (applicationServerKey)."""

    def __init__(self, key: ec.EllipticCurvePrivateKey, subject: str):
        self.key = key
        self.subject = subject
        self.public_key = b64u(public_bytes(key.public_key()))

    def jwt(self, endpoint: str, now: float) -> str:
        u = urlparse(endpoint)
        claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(now) + 12 * 3600, "sub": self.subject}
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        body = b64u(json.dumps(claims, separators=(",", ":")).encode())
        r, s = decode_dss_signature(self.key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
        return f"{head}.{body}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"

    def headers(self, endpoint: str, now: float) -> dict:
        return {"Authorization": f"vapid t={self.jwt(endpoint, now)}, k={self.public_key}"}


class Push:
    def __init__(self, core):
        self.core = core
        self.db = core.db
        self.vapid = Vapid(self._load_key(), core.s.push_subject)
        # Für Tests austauschbar: transport(url, headers, body) -> HTTP-Status; sync=True sendet sofort.
        from .integrations import _OPENER
        self._opener = _OPENER
        self.transport = self._http
        self.sync = False

    def _load_key(self) -> ec.EllipticCurvePrivateKey:
        row = self.db.one("SELECT value FROM kv WHERE key = 'vapid'")
        if row is not None:
            pem = self.core.box.decrypt(row["value"], "vapid").encode()
            return serialization.load_pem_private_key(pem, password=None)
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        self.db.execute("INSERT OR IGNORE INTO kv (key, value) VALUES ('vapid', ?)", (self.core.box.encrypt(pem, "vapid"),))
        return self._load_key()

    def check_endpoint(self, endpoint: str) -> None:
        from .integrations import check_url
        check_url(endpoint, allow_private=self.core.s.webhooks_allow_private, allow_http=False)

    def subscribe(self, tenant_id: str, card_id: str, endpoint: str, p256dh: str, auth: str) -> None:
        self.check_endpoint(endpoint)
        ua = b64u_dec(p256dh)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua)  # wirft bei ungültigem Schlüssel
        if len(b64u_dec(auth)) != 16:
            raise ValueError("invalid_auth")
        # je Karte höchstens 5 Geräte
        n = self.db.scalar("SELECT COUNT(*) FROM push_sub WHERE card_id = ? AND endpoint != ?", (card_id, endpoint)) or 0
        if n >= 5:
            self.db.execute("DELETE FROM push_sub WHERE id = (SELECT id FROM push_sub WHERE card_id = ? ORDER BY created_at LIMIT 1)",
                            (card_id,))
        self.db.execute(
            "INSERT INTO push_sub (id, tenant_id, card_id, endpoint, p256dh, auth, created_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (card_id, endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, failures = 0",
            (new_id("ps"), tenant_id, card_id, endpoint, p256dh, auth, self.core.clock()))

    def unsubscribe(self, card_id: str, endpoint: str | None = None) -> None:
        if endpoint:
            self.db.execute("DELETE FROM push_sub WHERE card_id = ? AND endpoint = ?", (card_id, endpoint))
        else:
            self.db.execute("DELETE FROM push_sub WHERE card_id = ?", (card_id,))

    def send_card(self, card_id: str, message: dict, ttl: int = 600) -> int:
        """An alle Geräte der Karte senden. Rückgabe: Zahl der Abos, an die gesendet wurde."""
        subs = self.db.all("SELECT * FROM push_sub WHERE card_id = ?", (card_id,))
        payload = json.dumps(message, ensure_ascii=False).encode()
        for sub in subs:
            if self.sync:
                self._deliver(sub, payload, ttl)
            else:
                threading.Thread(target=self._deliver, args=(sub, payload, ttl), daemon=True).start()
        return len(subs)

    def _deliver(self, sub, payload: bytes, ttl: int) -> None:
        try:
            self.check_endpoint(sub["endpoint"])  # DNS kann sich seit dem Abo geändert haben
            body = encrypt(payload, sub["p256dh"], sub["auth"])
            headers = {**self.vapid.headers(sub["endpoint"], time.time()), "Content-Encoding": "aes128gcm",
                       "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": "high"}
            status = self.transport(sub["endpoint"], headers, body)
        except Exception as exc:  # Netzwerk, ungültiges Ziel
            log.warning("Push fehlgeschlagen: %s", exc)
            status = 0
        if status in (404, 410):
            self.db.execute("DELETE FROM push_sub WHERE id = ?", (sub["id"],))
        elif 200 <= status < 300:
            self.db.execute("UPDATE push_sub SET last_ok_at = ?, failures = 0 WHERE id = ?", (self.core.clock(), sub["id"]))
        else:
            self.db.execute("UPDATE push_sub SET failures = failures + 1 WHERE id = ?", (sub["id"],))
            self.db.execute("DELETE FROM push_sub WHERE id = ? AND failures >= ?", (sub["id"], MAX_FAILURES))

    def _http(self, url: str, headers: dict, body: bytes) -> int:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(req, timeout=8) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
