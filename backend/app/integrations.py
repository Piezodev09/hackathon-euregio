"""Anbindung an die Schul-IT: API-Schlüssel (lesen, reservieren) und Webhooks (signierte Ereignis-Meldungen).

Webhook-Format:
    POST <url>
    Content-Type: application/json
    X-SBB-Event: alert.created
    X-SBB-Delivery: <id>
    X-SBB-Timestamp: 1727517600
    X-SBB-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>
    {"event": "alert.created", "created_at": "...", "tenant_id": "...", "data": {...}}

Zustellung im Hintergrund mit bis zu 3 Versuchen (sofort, nach 10 s, nach 60 s). Ziele im privaten Netz nur,
wenn in der Konfiguration erlaubt; Loopback/Link-Local (z. B. Cloud-Metadaten) sind immer gesperrt.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .billing import iso
from .core import Core
from .plans import get_plan
from .security import hash_token, new_id, new_token

log = logging.getLogger("integrations")

EVENTS = ("alert.created", "problem.reported", "sensor.fault", "stall.changed", "parking.checked_in", "parking.checked_out",
          "reservation.created", "reservation.ended", "gateway.offline", "gateway.online")
SCOPES = ("read", "reservations")
RETRY_DELAYS = (0, 10, 60)
MAX_WEBHOOKS = 5


class UrlRejected(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Weiterleitungen nicht folgen – sonst könnte ein Ziel auf interne Adressen umlenken."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def check_url(url: str, *, allow_private: bool, allow_http: bool) -> None:
    u = urlparse(url)
    if u.scheme not in ("https", "http") or (u.scheme == "http" and not allow_http):
        raise UrlRejected("webhook_https_required")
    if not u.hostname or u.username or u.password:
        raise UrlRejected("webhook_invalid_url")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UrlRejected("webhook_host_not_found") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise UrlRejected("webhook_target_forbidden")
        if ip.is_private and not allow_private:
            raise UrlRejected("webhook_private_network")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()


class Integrations:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db
        # Für Tests austauschbar: transport(url, headers, body) -> HTTP-Status; sync=True stellt sofort zu.
        self.transport = self._http
        self.sync = False
        core.listeners.append(self.on_event)

    # ------------------------------------------------------------------ API-Schlüssel
    def create_key(self, tenant_id: str, name: str, scopes: list[str], actor: str) -> dict:
        key = new_token("sbk_")
        kid = new_id("key")
        self.db.execute("INSERT INTO api_key (id, tenant_id, name, prefix, key_hash, scopes, created_at, created_by) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (kid, tenant_id, name, key[:12], hash_token(key), json.dumps(sorted(set(scopes))), self.core.clock(), actor))
        return {**self.key_out(self.db.one("SELECT * FROM api_key WHERE id = ?", (kid,))), "key": key}

    def key_out(self, r) -> dict:
        return {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "scopes": json.loads(r["scopes"]),
                "created_at": iso(r["created_at"]), "created_by": r["created_by"], "last_used_at": iso(r["last_used_at"]),
                "revoked_at": iso(r["revoked_at"])}

    def authenticate(self, token: str):
        if not token or len(token) > 200 or not token.startswith("sbk_"):
            return None
        row = self.db.one(
            "SELECT k.*, t.status AS tenant_status, t.plan AS tenant_plan FROM api_key k JOIN tenant t ON t.id = k.tenant_id "
            "WHERE k.key_hash = ? AND k.revoked_at IS NULL", (hash_token(token),))
        if row is not None and (row["last_used_at"] is None or self.core.clock() - row["last_used_at"] > 60):
            self.db.execute("UPDATE api_key SET last_used_at = ? WHERE id = ?", (self.core.clock(), row["id"]))
        return row

    # ------------------------------------------------------------------ Webhooks
    def webhook_out(self, r) -> dict:
        last = self.db.one("SELECT * FROM webhook_delivery WHERE webhook_id = ? ORDER BY id DESC LIMIT 1", (r["id"],))
        return {"id": r["id"], "url": r["url"], "events": json.loads(r["events"]), "active": bool(r["active"]),
                "created_at": iso(r["created_at"]),
                "last_delivery": None if last is None else {"at": iso(last["at"]), "event": last["event"], "ok": bool(last["ok"]),
                                                            "status_code": last["status_code"], "error": last["error"]}}

    def deliveries(self, webhook_id: str, limit: int = 20) -> list[dict]:
        return [{"at": iso(r["at"]), "event": r["event"], "attempt": r["attempt"], "ok": bool(r["ok"]),
                 "status_code": r["status_code"], "error": r["error"]}
                for r in self.db.all("SELECT * FROM webhook_delivery WHERE webhook_id = ? ORDER BY id DESC LIMIT ?", (webhook_id, limit))]

    def check(self, url: str) -> None:
        check_url(url, allow_private=self.core.s.webhooks_allow_private, allow_http=not self.core.s.production)

    def create_webhook(self, tenant_id: str, url: str, events: list[str], actor: str) -> dict:
        self.check(url)
        secret = new_token("whsec_")
        wid = new_id("wh")
        self.db.execute("INSERT INTO webhook (id, tenant_id, url, secret_enc, events, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
                        (wid, tenant_id, url, self.core.box.encrypt(secret, f"webhook:{wid}"), json.dumps(sorted(set(events))),
                         self.core.clock(), actor))
        return {**self.webhook_out(self.db.one("SELECT * FROM webhook WHERE id = ?", (wid,))), "secret": secret}

    def rotate_secret(self, row) -> str:
        secret = new_token("whsec_")
        self.db.execute("UPDATE webhook SET secret_enc = ? WHERE id = ?", (self.core.box.encrypt(secret, f"webhook:{row['id']}"), row["id"]))
        return secret

    def on_event(self, kind: str, tenant_id: str, data: dict) -> None:
        if kind not in EVENTS:
            return
        plan = self.db.scalar("SELECT plan FROM tenant WHERE id = ?", (tenant_id,))
        if not plan or not get_plan(plan).integrations:
            return
        for w in self.db.all("SELECT * FROM webhook WHERE tenant_id = ? AND active = 1", (tenant_id,)):
            if kind in json.loads(w["events"]):
                self.dispatch(w, kind, data)

    def dispatch(self, w, kind: str, data: dict) -> None:
        payload = {"id": new_id("dlv"), "event": kind, "created_at": iso(self.core.clock()), "tenant_id": w["tenant_id"], "data": data}
        if self.sync:
            self._deliver(w, payload)
        else:
            threading.Thread(target=self._deliver, args=(w, payload), daemon=True).start()

    def _deliver(self, w, payload: dict) -> bool:
        body = json.dumps(payload, separators=(",", ":")).encode()
        secret = self.core.box.decrypt(w["secret_enc"], f"webhook:{w['id']}")
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            if delay and not self.sync:
                time.sleep(delay)
            ts = int(self.core.clock())
            headers = {"Content-Type": "application/json", "User-Agent": f"{self.core.s.product_name} Webhooks",
                       "X-SBB-Event": payload["event"], "X-SBB-Delivery": payload["id"], "X-SBB-Timestamp": str(ts),
                       "X-SBB-Signature": sign(secret, ts, body)}
            status, error = None, None
            try:
                self.check(w["url"])  # erneut prüfen (DNS kann sich geändert haben)
                status = self.transport(w["url"], headers, body)
            except Exception as exc:  # Netzwerkfehler, abgelehnte Adresse …
                error = str(exc)[:200] or type(exc).__name__
            ok = status is not None and 200 <= status < 300
            self.db.execute("INSERT INTO webhook_delivery (webhook_id, tenant_id, event, at, attempt, status_code, ok, error) "
                            "VALUES (?,?,?,?,?,?,?,?)", (w["id"], w["tenant_id"], payload["event"], self.core.clock(), attempt,
                                                         status, int(ok), error))
            self.db.execute("DELETE FROM webhook_delivery WHERE webhook_id = ? AND id NOT IN "
                            "(SELECT id FROM webhook_delivery WHERE webhook_id = ? ORDER BY id DESC LIMIT 100)", (w["id"], w["id"]))
            if ok or self.sync or (status is not None and 400 <= status < 500 and status != 429):
                return ok
        return False

    @staticmethod
    def _http(url: str, headers: dict, body: bytes) -> int:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with _OPENER.open(req, timeout=5) as r:  # Ziel wurde mit check_url geprüft
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
