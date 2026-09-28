"""Shared application context: authentication (sessions), CSRF, roles, one-time tokens and audit log."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException, Request, Response

from .anomaly import MlDetector
from .config import Settings
from .db import Database
from .mailer import Mailer
from .ratelimit import RateLimiter
from .security import SecretBox, hash_token, new_id, new_token, safe_equals

log = logging.getLogger("bike_station")

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3, "owner": 4}
TENANT_ROLES = tuple(ROLE_RANK)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class Core:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time):
        self.s = settings
        self.clock = clock
        self.db = Database(settings.db_path)
        self.mailer = Mailer(settings)
        self.box = SecretBox(settings.data_key)
        self.ml = MlDetector(settings.model_path)
        self.read_limiter = RateLimiter(settings.read_rate_per_s, settings.read_burst)
        self.device_limiter = RateLimiter(settings.device_rate_per_s, settings.device_burst)
        self.auth_limiter = RateLimiter(settings.auth_per_minute / 60.0, settings.auth_per_minute)
        # sign-up, forgotten password etc.: at most 5 per hour and IP
        self.mail_limiter = RateLimiter(5 / 3600.0, 5)

    @property
    def cookie_name(self) -> str:
        # __Host- prefix: only with Secure, no Domain, Path=/ -> cannot be overwritten by subdomains
        return "__Host-bs_session" if self.s.secure_cookies else "bs_session"

    # ------------------------------------------------------------------ Audit
    def audit(self, action: str, *, tenant_id=None, user_id=None, actor=None, target=None, ip=None, detail=None):
        self.db.execute(
            "INSERT INTO audit_log (tenant_id, user_id, actor, action, target, ip, at, detail) VALUES (?,?,?,?,?,?,?,?)",
            (tenant_id, user_id, actor, action, target, ip, self.clock(), json.dumps(detail) if detail else None),
        )

    # ------------------------------------------------------------------ Sessions
    def create_session(self, response: Response, user: sqlite3.Row, ip: str, user_agent: str) -> str:
        now = self.clock()
        token = new_token()
        csrf = new_token()
        self.db.execute(
            "INSERT INTO session (token_hash, public_id, user_id, csrf_token, created_at, last_seen_at, expires_at, ip, user_agent) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (hash_token(token), new_id("ses"), user["id"], csrf, now, now, now + self.s.session_absolute_s, ip, user_agent[:200]),
        )
        response.set_cookie(
            self.cookie_name,
            token,
            max_age=int(self.s.session_absolute_s),
            path="/",
            secure=self.s.secure_cookies,
            httponly=True,
            samesite="strict",
        )
        return csrf

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(self.cookie_name, path="/", secure=self.s.secure_cookies, httponly=True, samesite="strict")

    def revoke_sessions(self, user_id: str, except_hash: str | None = None) -> int:
        if except_hash:
            cur = self.db.execute("DELETE FROM session WHERE user_id = ? AND token_hash != ?", (user_id, except_hash))
        else:
            cur = self.db.execute("DELETE FROM session WHERE user_id = ?", (user_id,))
        return cur.rowcount

    # ------------------------------------------------------------------ one-time tokens
    def issue_token(self, purpose: str, lifetime_s: float, **fields) -> str:
        token = new_token()
        now = self.clock()
        cols = ["token_hash", "public_id", "purpose", "created_at", "expires_at", *fields]
        vals = [hash_token(token), new_id("tok"), purpose, now, now + lifetime_s, *fields.values()]
        self.db.execute(f"INSERT INTO auth_token ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(vals))
        return token

    def consume_token(self, token: str, purpose: str) -> sqlite3.Row | None:
        """Single use: atomically marks the token as used."""
        if not token or len(token) > 200:
            return None
        h = hash_token(token)
        with self.db.tx() as c:
            row = c.execute(
                "SELECT * FROM auth_token WHERE token_hash = ? AND purpose = ? AND used_at IS NULL AND expires_at > ?",
                (h, purpose, self.clock()),
            ).fetchone()
            if row is None:
                return None
            c.execute("UPDATE auth_token SET used_at = ? WHERE token_hash = ?", (self.clock(), h))
        return row

    def peek_token(self, token: str, purpose: str) -> sqlite3.Row | None:
        if not token or len(token) > 200:
            return None
        return self.db.one(
            "SELECT * FROM auth_token WHERE token_hash = ? AND purpose = ? AND used_at IS NULL AND expires_at > ?",
            (hash_token(token), purpose, self.clock()),
        )

    # ------------------------------------------------------------------ tenants
    def tenant_usage(self, tenant_id: str) -> dict:
        return {
            "stations": self.db.scalar("SELECT COUNT(*) FROM station WHERE tenant_id = ?", (tenant_id,)),
            "users": self.db.scalar("SELECT COUNT(*) FROM user WHERE tenant_id = ?", (tenant_id,))
            + self.db.scalar(
                "SELECT COUNT(*) FROM auth_token WHERE tenant_id = ? AND purpose = 'invite' AND used_at IS NULL AND expires_at > ?",
                (tenant_id, self.clock()),
            ),
            "slots": self.db.scalar(
                "SELECT COUNT(*) FROM slot s JOIN station st ON st.id = s.station_id WHERE st.tenant_id = ?", (tenant_id,)
            ),
            "devices": self.db.scalar(
                "SELECT COUNT(*) FROM device WHERE tenant_id = ? AND revoked_at IS NULL", (tenant_id,)
            ),
        }


# ---------------------------------------------------------------------- request context


@dataclass
class Ctx:
    user: sqlite3.Row
    tenant: sqlite3.Row | None
    session_hash: str
    csrf: str
    ip: str
    user_agent: str

    @property
    def role(self) -> str:
        return self.user["role"]

    @property
    def tenant_id(self) -> str | None:
        return self.user["tenant_id"]

    @property
    def actor(self) -> str:
        return self.user["email"]


def core_of(request: Request) -> Core:
    return request.app.state.core


def client_ip(request: Request) -> str:
    core = core_of(request)
    if core.s.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def load_ctx(request: Request) -> Ctx | None:
    core = core_of(request)
    token = request.cookies.get(core.cookie_name)
    if not token or len(token) > 200:
        return None
    h = hash_token(token)
    now = core.clock()
    sess = core.db.one("SELECT * FROM session WHERE token_hash = ?", (h,))
    if sess is None:
        return None
    if sess["expires_at"] <= now or now - sess["last_seen_at"] > core.s.session_idle_s:
        core.db.execute("DELETE FROM session WHERE token_hash = ?", (h,))
        return None
    user = core.db.one("SELECT * FROM user WHERE id = ?", (sess["user_id"],))
    if user is None:
        return None
    # CSRF: every state-changing request with a session cookie must carry the matching header.
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not safe_equals(sent, sess["csrf_token"]):
            raise HTTPException(403, "csrf_failed")
    if now - sess["last_seen_at"] > 60:
        core.db.execute("UPDATE session SET last_seen_at = ? WHERE token_hash = ?", (now, h))
    tenant = core.db.one("SELECT * FROM tenant WHERE id = ?", (user["tenant_id"],)) if user["tenant_id"] else None
    return Ctx(user, tenant, h, sess["csrf_token"], client_ip(request), request.headers.get("user-agent", "")[:200])


def require(min_role: str | None = "viewer", *, platform: bool = False, allow_mfa_setup: bool = False):
    """Dependency: signed in, matching tenant/role, mandatory 2FA satisfied."""

    def dep(request: Request) -> Ctx:
        ctx = load_ctx(request)
        if ctx is None:
            raise HTTPException(401, "not_authenticated")
        core = core_of(request)
        u = ctx.user
        if platform:
            if not u["is_platform_admin"]:
                raise HTTPException(403, "forbidden")
            mfa_needed = core.s.platform_admin_mfa_required or core.s.production
            if mfa_needed and not u["totp_enabled"] and not allow_mfa_setup:
                raise HTTPException(403, "mfa_setup_required")
            return ctx
        if min_role is None:  # just "signed in" (profile, own security settings)
            if ctx.tenant is not None and ctx.tenant["mfa_required"] and not u["totp_enabled"] and not allow_mfa_setup:
                raise HTTPException(403, "mfa_setup_required")
            return ctx
        if ctx.tenant is None:
            raise HTTPException(403, "forbidden")
        if ctx.tenant["status"] != "active":
            raise HTTPException(403, "tenant_suspended")
        if ctx.tenant["mfa_required"] and not u["totp_enabled"] and not allow_mfa_setup:
            raise HTTPException(403, "mfa_setup_required")
        if ROLE_RANK.get(u["role"], 0) < ROLE_RANK[min_role]:
            raise HTTPException(403, "forbidden")
        return ctx

    return dep


def limit(limiter_name: str, key_prefix: str = ""):
    def dep(request: Request) -> None:
        core = core_of(request)
        limiter: RateLimiter = getattr(core, limiter_name)
        if not limiter.allow(f"{key_prefix}{client_ip(request)}"):
            raise HTTPException(429, "rate_limited", headers={"Retry-After": "60"})

    return dep
