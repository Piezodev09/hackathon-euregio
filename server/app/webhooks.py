"""Outgoing webhooks: event → JSON (generic, signed) or chat message (Slack, Microsoft Teams, Discord).

Security:
  * SSRF protection: the host is resolved before every delivery and the connection goes to exactly the
    checked address (no DNS rebinding). Loopback, private, link-local, multicast and reserved addresses are
    refused - loopback/private only become allowed with ``webhook_allow_private`` (LAN self-hosting, e.g. a
    local Home Assistant). Link-local (cloud metadata) and multicast are never allowed.
  * HTTPS only, except to private addresses when ``webhook_allow_private`` is set.
  * No redirects, 5 s timeout, response body ignored (at most 1 KB read).
  * ``X-BikeStation-Signature: t=<unix time>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>`` so receivers can
    verify origin and freshness. The secret is stored encrypted with the data key.
Delivery runs in a background thread with 3 attempts and exponential backoff.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import queue
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

log = logging.getLogger("webhooks")

KINDS = ("generic", "slack", "teams", "discord")
EVENT_TYPES = ("alert", "sensor_fault", "gateway_offline", "gateway_online")
TIMEOUT_S = 5.0
ATTEMPTS = 3
USER_AGENT = "SmartBikeStation-Webhook/1.0"


class WebhookError(ValueError):
    """Invalid target (code in ``args[0]``: invalid_url, https_required, private_address, unresolvable)."""


# ---------------------------------------------------------------------- target validation
def _blocked(ip: ipaddress._BaseAddress, allow_private: bool) -> bool:
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or (ip.version == 4 and ip in ipaddress.ip_network("0.0.0.0/8")):
        return True
    if ip.is_loopback or ip.is_private or ip.is_reserved or (ip.version == 6 and ip.is_site_local):
        return not allow_private
    return False


def check_url(url: str, allow_private: bool) -> tuple[str, int, bool, str]:
    """Syntax check. Returns (host, port, tls, path_with_query)."""
    try:
        u = urlsplit(url.strip())
    except ValueError as exc:
        raise WebhookError("invalid_url") from exc
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or len(url) > 1000:
        raise WebhookError("invalid_url")
    tls = u.scheme == "https"
    if not tls and not allow_private:
        raise WebhookError("https_required")
    try:
        port = u.port or (443 if tls else 80)
    except ValueError as exc:
        raise WebhookError("invalid_url") from exc
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return u.hostname, port, tls, path


def resolve(host: str, port: int, allow_private: bool, resolver: Callable = socket.getaddrinfo) -> str:
    """Resolve and check every address; return the one to connect to."""
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise WebhookError("unresolvable") from exc
    addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    if not addrs:
        raise WebhookError("unresolvable")
    if any(_blocked(a, allow_private) for a in addrs):
        raise WebhookError("private_address")
    # http without TLS only to private addresses (LAN), never to the internet.
    return str(addrs[0])


def validate_target(url: str, allow_private: bool, resolver: Callable = socket.getaddrinfo) -> None:
    host, port, tls, _ = check_url(url, allow_private)
    ip = resolve(host, port, allow_private, resolver)
    if not tls and not ipaddress.ip_address(ip).is_private and not ipaddress.ip_address(ip).is_loopback:
        raise WebhookError("https_required")


# ---------------------------------------------------------------------- payloads
def signature(secret: str, body: bytes, t: int) -> str:
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify_signature(secret: str, body: bytes, header: str, now: float, tolerance_s: int = 300) -> bool:
    """Reference implementation for receivers (also used by the tests)."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    return abs(now - t) <= tolerance_s and hmac.compare_digest(signature(secret, body, t), header)


def event_text(ev: dict) -> str:
    station = ev["station"]["name"]
    slot = f" – space {ev['slot']}" if ev.get("slot") else ""
    sim = " [simulated]" if ev.get("simulated") else ""
    return {
        "alert": f"⚠ Unusual movement at {station}{slot}. This is a suspicion, not proof – please check on site.{sim}",
        "sensor_fault": f"Sensor fault at {station}{slot}: the space is shown as unknown.{sim}",
        "gateway_offline": f"Gateway offline at {station}: no contact for more than 3 minutes"
                           + (f" ({ev['device']})" if ev.get("device") else "") + f". Spaces show as unknown.{sim}",
        "gateway_online": f"Gateway back online at {station}" + (f" ({ev['device']})" if ev.get("device") else "") + f".{sim}",
        "test": f"Test message from Smart Bike Station ({station}). Your webhook works.",
    }.get(ev["type"], f"{ev['type']} at {station}{slot}")


def build_body(kind: str, ev: dict) -> bytes:
    text = event_text(ev)
    if kind == "slack":
        payload: dict = {"text": text}
    elif kind == "teams":
        payload = {"text": text}
    elif kind == "discord":
        payload = {"content": text[:1900]}
    else:
        payload = {**ev, "text": text}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


# ---------------------------------------------------------------------- HTTP
class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip
        self._ctx = context

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self.host)


def post(url: str, body: bytes, headers: dict, allow_private: bool, resolver: Callable = socket.getaddrinfo,
         timeout: float = TIMEOUT_S) -> int:
    host, port, tls, path = check_url(url, allow_private)
    ip = resolve(host, port, allow_private, resolver)
    if not tls and not (ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback):
        raise WebhookError("https_required")
    conn = (_PinnedHTTPSConnection(host, ip, port, timeout, ssl.create_default_context()) if tls
            else _PinnedHTTPConnection(host, ip, port, timeout))
    try:
        conn.request("POST", path, body=body, headers={"Host": host if port in (80, 443) else f"{host}:{port}", **headers})
        resp = conn.getresponse()
        resp.read(1024)
        return resp.status
    finally:
        conn.close()


# ---------------------------------------------------------------------- dispatcher
@dataclass
class Job:
    webhook_id: str
    url: str
    kind: str
    secret: str
    event: dict
    attempt: int = 0


class Dispatcher:
    """Background delivery with retries. ``deliver_now`` is used for the test button."""

    def __init__(self, core, backoff_s: tuple[float, ...] = (2.0, 10.0), sender: Callable | None = None):
        self.core = core
        self.backoff_s = backoff_s
        self.sender = sender or post
        self.q: queue.Queue[Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._pending = 0
        self._cond = threading.Condition()

    # -- public API
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="webhooks")
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self.q.put(None)
            self._thread.join(timeout=2)
            self._thread = None

    def wait_idle(self, timeout: float = 10.0) -> bool:
        end = time.monotonic() + timeout
        with self._cond:
            while self._pending:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
        return True

    def publish(self, event: dict) -> int:
        """Queue an event for every enabled webhook of the tenant that subscribed to its type."""
        rows = self.core.db.all("SELECT * FROM webhook WHERE tenant_id = ? AND enabled = 1", (event["tenant_id"],))
        n = 0
        for w in rows:
            if event["type"] not in json.loads(w["events"]):
                continue
            job = Job(w["id"], w["url"], w["kind"], self._secret(w), _public(event))
            with self._cond:
                self._pending += 1
            self.q.put(job)
            n += 1
        if n:
            self.start()
        return n

    def deliver_now(self, w, event: dict) -> tuple[bool, str]:
        job = Job(w["id"], w["url"], w["kind"], self._secret(w), _public(event))
        return self._attempt(job)

    # -- internals
    def _secret(self, w) -> str:
        return self.core.box.decrypt(w["secret_enc"], f"webhook:{w['id']}")

    def _attempt(self, job: Job) -> tuple[bool, str]:
        body = build_body(job.kind, job.event)
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "X-BikeStation-Event": job.event["type"],
                   "X-BikeStation-Delivery": job.event.get("id", ""),
                   "X-BikeStation-Signature": signature(job.secret, body, int(self.core.clock()))}
        try:
            status = self.sender(job.url, body, headers, self.core.s.webhook_allow_private)
            ok = 200 <= status < 300
            result = f"HTTP {status}"
        except WebhookError as exc:
            ok, result = False, exc.args[0]
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            ok, result = False, type(exc).__name__
        self.core.db.execute("UPDATE webhook SET last_status = ?, last_attempt_at = ?, last_error = ? WHERE id = ?",
                             ("ok" if ok else "error", self.core.clock(), None if ok else result[:200], job.webhook_id))
        return ok, result

    def _run(self) -> None:
        while True:
            job = self.q.get()
            if job is None:
                return
            try:
                ok, result = self._attempt(job)
            except Exception:  # never let the worker die
                log.exception("Webhook delivery crashed")
                ok, result = False, "internal_error"
            if not ok and job.attempt + 1 < ATTEMPTS and result not in ("invalid_url", "https_required", "private_address"):
                # Retry later without blocking other deliveries.
                job.attempt += 1
                timer = threading.Timer(self.backoff_s[min(job.attempt - 1, len(self.backoff_s) - 1)], self.q.put, args=(job,))
                timer.daemon = True
                timer.start()
                continue
            if not ok:
                log.warning("Webhook %s failed after %d attempt(s): %s", job.webhook_id, job.attempt + 1, result)
            with self._cond:
                self._pending -= 1
                self._cond.notify_all()


def _public(event: dict) -> dict:
    """The event as sent to receivers (no internal tenant ID)."""
    return {k: v for k, v in event.items() if k != "tenant_id"}
