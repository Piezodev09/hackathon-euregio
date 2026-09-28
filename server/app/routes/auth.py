"""Sign-up, sign-in (incl. 2FA), e-mail verification, passwords, sessions, invitations."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..core import Core, Ctx, client_ip, core_of, limit, load_ctx, require
from ..mailer import link
from ..plans import DEFAULT_PLAN, SELF_HOSTED_PLAN, get_plan
from ..schemas import (
    CodeIn,
    EmailIn,
    InviteAcceptIn,
    LoginIn,
    MfaDisableIn,
    MfaLoginIn,
    PasswordChangeIn,
    PasswordConfirmIn,
    ProfilePatch,
    RegisterIn,
    ResetIn,
    SetupIn,
    TokenIn,
)
from ..security import (
    dummy_verify,
    hash_password,
    needs_rehash,
    new_id,
    new_recovery_codes,
    new_totp_secret,
    normalize_recovery_code,
    password_problems,
    totp_uri,
    totp_verify,
    verify_password,
)
from ..service import iso

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
auth_limit = Depends(limit("auth_limiter", "auth:"))
mail_limit = Depends(limit("mail_limiter", "mail:"))
MFA_TOKEN_S = 300


def _rc_hash(code: str) -> str:
    return hashlib.sha256(normalize_recovery_code(code).encode()).hexdigest()


def check_password(core: Core, password: str, email: str = "", name: str = "") -> None:
    problems = password_problems(password, core.s.password_min_length, email, name)
    if problems:
        raise HTTPException(422, {"code": "weak_password", "problems": problems, "min_length": core.s.password_min_length})


def me_payload(core: Core, ctx: Ctx) -> dict:
    u, t = ctx.user, ctx.tenant
    plan = get_plan(t["plan"]) if t else None
    mfa_setup_required = (
        (t is not None and bool(t["mfa_required"]) and not u["totp_enabled"])
        or (bool(u["is_platform_admin"]) and (core.s.platform_admin_mfa_required or core.s.production) and not u["totp_enabled"])
    )
    return {
        "user": {
            "id": u["id"],
            "email": u["email"],
            "name": u["name"],
            "role": u["role"],
            "locale": u["locale"],
            "mfa_enabled": bool(u["totp_enabled"]),
            "is_platform_admin": bool(u["is_platform_admin"]),
            "last_login_at": iso(u["last_login_at"]),
        },
        "tenant": None
        if t is None
        else {
            "id": t["id"],
            "name": t["name"],
            "status": t["status"],
            "mfa_required": bool(t["mfa_required"]),
            "plan": plan.to_dict(),
            "usage": core.tenant_usage(t["id"]),
        },
        "mfa_setup_required": mfa_setup_required,
        "csrf_token": ctx.csrf,
        "product_name": core.s.product_name,
    }


def _finish_login(core: Core, request: Request, response: Response, user) -> dict:
    # Prevent session fixation: drop any existing session and always issue a new one.
    old = request.cookies.get(core.cookie_name)
    if old:
        from ..security import hash_token

        core.db.execute("DELETE FROM session WHERE token_hash = ?", (hash_token(old),))
    ip = client_ip(request)
    csrf = core.create_session(response, user, ip, request.headers.get("user-agent", ""))
    core.db.execute("UPDATE user SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE id = ?", (core.clock(), user["id"]))
    core.audit("login", tenant_id=user["tenant_id"], user_id=user["id"], actor=user["email"], ip=ip)
    user = core.db.one("SELECT * FROM user WHERE id = ?", (user["id"],))
    tenant = core.db.one("SELECT * FROM tenant WHERE id = ?", (user["tenant_id"],)) if user["tenant_id"] else None
    return me_payload(core, Ctx(user, tenant, "", csrf, ip, ""))


def _register_failure(core: Core, user, ip: str) -> None:
    fails = user["failed_logins"] + 1
    locked_until = None
    if fails >= core.s.lockout_threshold:
        # Growing lock duration: 5, 10, 20 ... minutes, at most 24 h
        factor = 2 ** min(fails - core.s.lockout_threshold, 8)
        locked_until = core.clock() + min(core.s.lockout_base_s * factor, 86400)
        if fails == core.s.lockout_threshold:
            core.mailer.send(
                user["email"],
                f"{core.s.product_name}: account temporarily locked",
                "Your account was temporarily locked after several failed sign-in attempts.\n"
                "If this was not you, please reset your password.",
            )
    core.db.execute("UPDATE user SET failed_logins = ?, locked_until = ? WHERE id = ?", (fails, locked_until, user["id"]))
    core.audit("login_failed", tenant_id=user["tenant_id"], user_id=user["id"], actor=user["email"], ip=ip,
               detail={"locked": locked_until is not None})


# ---------------------------------------------------------------------- sign-up
@router.post("/register", status_code=202, dependencies=[mail_limit])
def register(body: RegisterIn, request: Request):
    core = core_of(request)
    mode = core.s.signup
    if mode == "closed":
        raise HTTPException(403, "signup_disabled")
    check_password(core, body.password, body.email, body.name)
    # The response only depends on the configuration, never on whether the address exists.
    result = {"status": "pending_approval" if mode == "approval" else "check_email", "verify_email": core.mailer.enabled}
    existing = core.db.one("SELECT id FROM user WHERE email = ?", (body.email,))
    if existing:
        # No account enumeration: same response, a notice goes to the account owner instead.
        dummy_verify(body.password, core.s.scrypt_n)
        core.mailer.send(
            body.email,
            f"{core.s.product_name}: sign-up attempt",
            "Someone tried to sign up with this e-mail address, but an account already exists.\n"
            f"If you forgot your password: {core.s.base_url}/app#/forgot",
        )
        return result
    now = core.clock()
    tenant_id = new_id("org")
    user_id = new_id("usr")
    pw = hash_password(body.password, core.s.scrypt_n)
    with core.db.tx() as c:
        c.execute("INSERT INTO tenant (id, name, plan, status, created_at) VALUES (?,?,?,?,?)",
                  (tenant_id, body.org_name, DEFAULT_PLAN, "pending" if mode == "approval" else "active", now))
        c.execute(
            "INSERT INTO user (id, tenant_id, email, name, role, password_hash, locale, created_at, password_changed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, tenant_id, body.email, body.name, "owner", pw, body.locale, now, now),
        )
    if core.mailer.enabled:
        token = core.issue_token("verify", core.s.verify_token_s, user_id=user_id, email=body.email)
        core.mailer.send(
            body.email,
            f"{core.s.product_name}: confirm your e-mail address",
            f"Welcome to {core.s.product_name}!\n\nPlease confirm your e-mail address:\n"
            f"{link(core.s, 'verify', token)}\n\nThe link is valid for {int(core.s.verify_token_s // 3600)} hours.",
        )
    core.audit("tenant_registered", tenant_id=tenant_id, user_id=user_id, actor=body.email, ip=client_ip(request),
               detail={"approval_required": mode == "approval"})
    return result


@router.post("/verify-email", dependencies=[auth_limit])
def verify_email(body: TokenIn, request: Request):
    core = core_of(request)
    tok = core.consume_token(body.token, "verify")
    if tok is None:
        raise HTTPException(400, "invalid_or_expired_token")
    core.db.execute("UPDATE user SET email_verified_at = ? WHERE id = ? AND email = ?", (core.clock(), tok["user_id"], tok["email"]))
    core.audit("email_verified", user_id=tok["user_id"], actor=tok["email"], ip=client_ip(request))
    return {"status": "verified"}


@router.post("/resend-verification", status_code=202, dependencies=[mail_limit])
def resend_verification(body: EmailIn, request: Request):
    core = core_of(request)
    user = core.db.one("SELECT * FROM user WHERE email = ?", (body.email.strip().lower(),))
    if user and not user["email_verified_at"] and core.mailer.enabled:
        token = core.issue_token("verify", core.s.verify_token_s, user_id=user["id"], email=user["email"])
        core.mailer.send(user["email"], f"{core.s.product_name}: confirm your e-mail address",
                         f"Please confirm your e-mail address:\n{link(core.s, 'verify', token)}")
    return {"status": "check_email"}


# ---------------------------------------------------------------------- sign-in
@router.post("/login", dependencies=[auth_limit])
def login(body: LoginIn, request: Request, response: Response):
    core = core_of(request)
    ip = client_ip(request)
    email = body.email.strip().lower()
    user = core.db.one("SELECT * FROM user WHERE email = ?", (email,))
    if user is None:
        dummy_verify(body.password, core.s.scrypt_n)
        core.audit("login_failed", actor=email[:254], ip=ip, detail={"reason": "unknown"})
        raise HTTPException(401, "invalid_credentials")
    locked = user["locked_until"] is not None and user["locked_until"] > core.clock()
    ok = verify_password(body.password, user["password_hash"])
    if locked or not ok:
        if not ok:
            _register_failure(core, user, ip)
        else:
            core.audit("login_blocked_locked", tenant_id=user["tenant_id"], user_id=user["id"], actor=user["email"], ip=ip)
        raise HTTPException(401, "invalid_credentials")  # same response, also when locked
    # Only revealed after the correct password: the organisation still awaits approval.
    if user["tenant_id"] and core.db.scalar("SELECT status FROM tenant WHERE id = ?", (user["tenant_id"],)) == "pending":
        raise HTTPException(403, "pending_approval")
    if not user["email_verified_at"]:
        raise HTTPException(403, "email_not_verified")
    if needs_rehash(user["password_hash"], core.s.scrypt_n):
        core.db.execute("UPDATE user SET password_hash = ? WHERE id = ?", (hash_password(body.password, core.s.scrypt_n), user["id"]))
    if user["totp_enabled"]:
        token = core.issue_token("mfa", MFA_TOKEN_S, user_id=user["id"])
        return {"mfa_required": True, "mfa_token": token}
    return _finish_login(core, request, response, user)


@router.post("/login/mfa", dependencies=[auth_limit])
def login_mfa(body: MfaLoginIn, request: Request, response: Response):
    core = core_of(request)
    ip = client_ip(request)
    tok = core.peek_token(body.mfa_token, "mfa")
    if tok is None:
        raise HTTPException(401, "mfa_session_expired")
    user = core.db.one("SELECT * FROM user WHERE id = ?", (tok["user_id"],))
    if user is None or not user["totp_enabled"] or (user["locked_until"] and user["locked_until"] > core.clock()):
        raise HTTPException(401, "invalid_code")
    secret = core.box.decrypt(user["totp_secret_enc"], f"totp:{user['id']}")
    step = totp_verify(secret, body.code, user["totp_last_step"], core.clock())
    used_recovery = False
    if step is None:
        rc = core.db.execute(
            "UPDATE recovery_code SET used_at = ? WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
            (core.clock(), user["id"], _rc_hash(body.code)),
        )
        used_recovery = rc.rowcount == 1
        if not used_recovery:
            _register_failure(core, user, ip)
            raise HTTPException(401, "invalid_code")
    else:
        core.db.execute("UPDATE user SET totp_last_step = ? WHERE id = ?", (step, user["id"]))
    if core.consume_token(body.mfa_token, "mfa") is None:  # single use
        raise HTTPException(401, "mfa_session_expired")
    if used_recovery:
        core.audit("recovery_code_used", tenant_id=user["tenant_id"], user_id=user["id"], actor=user["email"], ip=ip)
        core.mailer.send(user["email"], f"{core.s.product_name}: recovery code used",
                         "A recovery code was used to sign in to your account. If this was not you, "
                         "change your password immediately.")
    return _finish_login(core, request, response, user)


@router.post("/logout")
def logout(request: Request, response: Response):
    core = core_of(request)
    ctx = load_ctx(request)
    if ctx:
        core.db.execute("DELETE FROM session WHERE token_hash = ?", (ctx.session_hash,))
        core.audit("logout", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip)
    core.clear_cookie(response)
    return {"status": "logged_out"}


@router.get("/me")
def me(request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    return me_payload(core_of(request), ctx)


@router.patch("/me")
def update_profile(body: ProfilePatch, request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    if body.name is not None:
        core.db.execute("UPDATE user SET name = ? WHERE id = ?", (body.name, ctx.user["id"]))
    if body.locale is not None:
        core.db.execute("UPDATE user SET locale = ? WHERE id = ?", (body.locale, ctx.user["id"]))
    return {"status": "ok"}


@router.post("/me/delete")
def delete_account(body: PasswordConfirmIn, request: Request, response: Response,
                   ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    if not verify_password(body.password, ctx.user["password_hash"]):
        raise HTTPException(403, "wrong_password")
    if ctx.role == "owner":
        owners = core.db.scalar("SELECT COUNT(*) FROM user WHERE tenant_id = ? AND role = 'owner'", (ctx.tenant_id,))
        if owners <= 1:
            raise HTTPException(409, "last_owner")
    core.audit("account_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip)
    core.db.execute("DELETE FROM user WHERE id = ?", (ctx.user["id"],))
    core.clear_cookie(response)
    return {"status": "deleted"}


# ---------------------------------------------------------------------- password
@router.post("/password/forgot", status_code=202, dependencies=[mail_limit])
def forgot_password(body: EmailIn, request: Request):
    core = core_of(request)
    if not core.mailer.enabled:
        # Without e-mail an administrator creates a reset link in the team area.
        return {"status": "ask_admin"}
    user = core.db.one("SELECT * FROM user WHERE email = ?", (body.email.strip().lower(),))
    if user:
        token = core.issue_token("reset", core.s.reset_token_s, user_id=user["id"], email=user["email"])
        core.mailer.send(
            user["email"],
            f"{core.s.product_name}: reset your password",
            f"To reset your password open:\n{link(core.s, 'reset', token)}\n\n"
            f"The link is valid for {int(core.s.reset_token_s // 60)} minutes. If you did not request this, "
            "you can ignore this e-mail.",
        )
        core.audit("password_reset_requested", tenant_id=user["tenant_id"], user_id=user["id"], actor=user["email"],
                   ip=client_ip(request))
    return {"status": "check_email"}


@router.post("/password/reset", dependencies=[auth_limit])
def reset_password(body: ResetIn, request: Request):
    core = core_of(request)
    tok = core.peek_token(body.token, "reset")
    if tok is None:
        raise HTTPException(400, "invalid_or_expired_token")
    user = core.db.one("SELECT * FROM user WHERE id = ?", (tok["user_id"],))
    if user is None:
        raise HTTPException(400, "invalid_or_expired_token")
    check_password(core, body.password, user["email"], user["name"])
    if core.consume_token(body.token, "reset") is None:
        raise HTTPException(400, "invalid_or_expired_token")
    now = core.clock()
    core.db.execute(
        "UPDATE user SET password_hash = ?, password_changed_at = ?, failed_logins = 0, locked_until = NULL, "
        "email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
        (hash_password(body.password, core.s.scrypt_n), now, now, user["id"]),
    )
    core.revoke_sessions(user["id"])  # sign out everywhere
    core.audit("password_reset", tenant_id=user["tenant_id"], user_id=user["id"], actor=user["email"], ip=client_ip(request))
    core.mailer.send(user["email"], f"{core.s.product_name}: password changed",
                     "Your password was reset. If this was not you, contact your administrator immediately.")
    return {"status": "password_reset"}


@router.post("/password/change")
def change_password(body: PasswordChangeIn, request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    if not verify_password(body.current_password, ctx.user["password_hash"]):
        raise HTTPException(403, "wrong_password")
    check_password(core, body.new_password, ctx.user["email"], ctx.user["name"])
    core.db.execute("UPDATE user SET password_hash = ?, password_changed_at = ? WHERE id = ?",
                    (hash_password(body.new_password, core.s.scrypt_n), core.clock(), ctx.user["id"]))
    n = core.revoke_sessions(ctx.user["id"], except_hash=ctx.session_hash)
    core.audit("password_changed", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip,
               detail={"other_sessions_revoked": n})
    core.mailer.send(ctx.user["email"], f"{core.s.product_name}: password changed",
                     "Your password was changed. All other sessions were signed out.")
    return {"status": "changed", "other_sessions_revoked": n}


# ---------------------------------------------------------------------- sessions
@router.get("/sessions")
def list_sessions(request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM session WHERE user_id = ? ORDER BY last_seen_at DESC", (ctx.user["id"],))
    return {"sessions": [
        {"id": r["public_id"], "current": r["token_hash"] == ctx.session_hash, "created_at": iso(r["created_at"]),
         "last_seen_at": iso(r["last_seen_at"]), "expires_at": iso(r["expires_at"]), "ip": r["ip"], "user_agent": r["user_agent"]}
        for r in rows
    ]}


@router.delete("/sessions/{public_id}")
def revoke_session(public_id: str, request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    cur = core.db.execute("DELETE FROM session WHERE public_id = ? AND user_id = ?", (public_id[:64], ctx.user["id"]))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    core.audit("session_revoked", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip)
    return {"status": "revoked"}


@router.post("/sessions/revoke-others")
def revoke_other_sessions(request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    n = core.revoke_sessions(ctx.user["id"], except_hash=ctx.session_hash)
    core.audit("sessions_revoked", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, detail={"count": n})
    return {"revoked": n}


# ---------------------------------------------------------------------- 2FA
@router.post("/mfa/setup")
def mfa_setup(request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    if ctx.user["totp_enabled"]:
        raise HTTPException(409, "mfa_already_enabled")
    secret = new_totp_secret()
    core.db.execute("UPDATE user SET totp_pending_enc = ? WHERE id = ?",
                    (core.box.encrypt(secret, f"totp-pending:{ctx.user['id']}"), ctx.user["id"]))
    return {"secret": secret, "otpauth_uri": totp_uri(secret, ctx.user["email"], core.s.product_name)}


@router.post("/mfa/enable")
def mfa_enable(body: CodeIn, request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    u = ctx.user
    if u["totp_enabled"] or not u["totp_pending_enc"]:
        raise HTTPException(409, "mfa_setup_not_started")
    secret = core.box.decrypt(u["totp_pending_enc"], f"totp-pending:{u['id']}")
    step = totp_verify(secret, body.code, None, core.clock())
    if step is None:
        raise HTTPException(400, "invalid_code")
    codes = new_recovery_codes()
    with core.db.tx() as c:
        c.execute("UPDATE user SET totp_secret_enc = ?, totp_pending_enc = NULL, totp_enabled = 1, totp_last_step = ? WHERE id = ?",
                  (core.box.encrypt(secret, f"totp:{u['id']}"), step, u["id"]))
        c.execute("DELETE FROM recovery_code WHERE user_id = ?", (u["id"],))
        c.executemany("INSERT INTO recovery_code (user_id, code_hash) VALUES (?, ?)", [(u["id"], _rc_hash(x)) for x in codes])
    core.revoke_sessions(u["id"], except_hash=ctx.session_hash)
    core.audit("mfa_enabled", tenant_id=ctx.tenant_id, user_id=u["id"], actor=ctx.actor, ip=ctx.ip)
    core.mailer.send(u["email"], f"{core.s.product_name}: two-factor sign-in enabled",
                     "Two-factor sign-in was enabled for your account.")
    return {"recovery_codes": codes}


@router.post("/mfa/disable")
def mfa_disable(body: MfaDisableIn, request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    u = ctx.user
    if not u["totp_enabled"]:
        raise HTTPException(409, "mfa_not_enabled")
    if (ctx.tenant and ctx.tenant["mfa_required"]) or (u["is_platform_admin"] and (core.s.platform_admin_mfa_required or core.s.production)):
        raise HTTPException(403, "mfa_enforced")
    if not verify_password(body.password, u["password_hash"]):
        raise HTTPException(403, "wrong_password")
    secret = core.box.decrypt(u["totp_secret_enc"], f"totp:{u['id']}")
    if totp_verify(secret, body.code, u["totp_last_step"], core.clock()) is None:
        raise HTTPException(400, "invalid_code")
    core.db.execute("UPDATE user SET totp_enabled = 0, totp_secret_enc = NULL, totp_last_step = NULL WHERE id = ?", (u["id"],))
    core.db.execute("DELETE FROM recovery_code WHERE user_id = ?", (u["id"],))
    core.audit("mfa_disabled", tenant_id=ctx.tenant_id, user_id=u["id"], actor=ctx.actor, ip=ctx.ip)
    core.mailer.send(u["email"], f"{core.s.product_name}: two-factor sign-in disabled",
                     "Two-factor sign-in was disabled for your account. If this was not you, change your password immediately.")
    return {"status": "disabled"}


@router.post("/mfa/recovery-codes")
def regenerate_recovery_codes(body: PasswordConfirmIn, request: Request, ctx: Ctx = Depends(require(None, allow_mfa_setup=True))):
    core = core_of(request)
    u = ctx.user
    if not u["totp_enabled"]:
        raise HTTPException(409, "mfa_not_enabled")
    if not verify_password(body.password, u["password_hash"]):
        raise HTTPException(403, "wrong_password")
    codes = new_recovery_codes()
    with core.db.tx() as c:
        c.execute("DELETE FROM recovery_code WHERE user_id = ?", (u["id"],))
        c.executemany("INSERT INTO recovery_code (user_id, code_hash) VALUES (?, ?)", [(u["id"], _rc_hash(x)) for x in codes])
    core.audit("recovery_codes_regenerated", tenant_id=ctx.tenant_id, user_id=u["id"], actor=ctx.actor, ip=ctx.ip)
    return {"recovery_codes": codes}


# ---------------------------------------------------------------------- accepting invitations
@router.post("/invite/info", dependencies=[auth_limit])
def invite_info(body: TokenIn, request: Request):
    core = core_of(request)
    tok = core.peek_token(body.token, "invite")
    if tok is None:
        raise HTTPException(400, "invalid_or_expired_token")
    t = core.db.one("SELECT name FROM tenant WHERE id = ?", (tok["tenant_id"],))
    return {"email": tok["email"], "org_name": t["name"] if t else None, "role": tok["role"]}


@router.post("/invite/accept", dependencies=[auth_limit])
def invite_accept(body: InviteAcceptIn, request: Request, response: Response):
    core = core_of(request)
    tok = core.peek_token(body.token, "invite")
    if tok is None:
        raise HTTPException(400, "invalid_or_expired_token")
    check_password(core, body.password, tok["email"], body.name)
    if core.db.one("SELECT 1 FROM user WHERE email = ?", (tok["email"],)):
        raise HTTPException(409, "email_in_use")
    tenant = core.db.one("SELECT * FROM tenant WHERE id = ?", (tok["tenant_id"],))
    if tenant is None or tenant["status"] != "active":
        raise HTTPException(400, "invalid_or_expired_token")
    if core.consume_token(body.token, "invite") is None:
        raise HTTPException(400, "invalid_or_expired_token")
    now = core.clock()
    uid = new_id("usr")
    core.db.execute(
        "INSERT INTO user (id, tenant_id, email, name, role, password_hash, email_verified_at, created_at, password_changed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (uid, tenant["id"], tok["email"], body.name, tok["role"], hash_password(body.password, core.s.scrypt_n), now, now, now),
    )
    core.audit("invite_accepted", tenant_id=tenant["id"], user_id=uid, actor=tok["email"], ip=client_ip(request),
               detail={"role": tok["role"]})
    user = core.db.one("SELECT * FROM user WHERE id = ?", (uid,))
    return _finish_login(core, request, response, user)


# ---------------------------------------------------------------------- first-run setup
@router.post("/setup", dependencies=[auth_limit])
def setup(body: SetupIn, request: Request, response: Response):
    """Create the first platform admin together with the first organisation (owner) - once.

    Protected by the setup token from the data directory (printed by install-server.sh and in the log).
    """
    core = core_of(request)
    ip = client_ip(request)
    if not core.check_setup_token(body.token):
        core.audit("setup_failed", ip=ip)
        raise HTTPException(400, "invalid_or_expired_token")
    check_password(core, body.password, body.email, body.name)
    if core.db.one("SELECT 1 FROM user WHERE email = ?", (body.email,)):
        raise HTTPException(409, "email_in_use")
    now = core.clock()
    tid, uid = new_id("org"), new_id("usr")
    with core.db.tx() as c:
        # Re-check inside the transaction: exactly one request can win the setup.
        if c.execute("SELECT 1 FROM user WHERE is_platform_admin = 1").fetchone():
            raise HTTPException(400, "invalid_or_expired_token")
        c.execute("INSERT INTO tenant (id, name, plan, status, created_at) VALUES (?,?,?,?,?)",
                  (tid, body.org_name, SELF_HOSTED_PLAN, "active", now))
        c.execute(
            "INSERT INTO user (id, tenant_id, email, name, role, password_hash, email_verified_at, is_platform_admin, locale, "
            "created_at, password_changed_at) VALUES (?,?,?,?,?,?,?,1,?,?,?)",
            (uid, tid, body.email, body.name, "owner", hash_password(body.password, core.s.scrypt_n), now, body.locale, now, now))
    core.finish_setup()
    core.audit("platform_setup", tenant_id=tid, user_id=uid, actor=body.email, ip=ip)
    return _finish_login(core, request, response, core.db.one("SELECT * FROM user WHERE id = ?", (uid,)))
