"""Authentication and session security."""

from __future__ import annotations

from app.security import totp_now

from conftest import PASSWORD


def test_register_verify_login_logout(env):
    api = env.register(login=False, verify=False, email="neu@example.org")
    # before verification: correct password -> email_not_verified
    r = api.post("/api/v1/auth/login", {"email": "neu@example.org", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["detail"] == "email_not_verified"
    assert api.post("/api/v1/auth/verify-email", {"token": env.last_token("neu@example.org")}).status_code == 200
    # token can only be used once
    assert api.post("/api/v1/auth/verify-email", {"token": env.last_token("neu@example.org")}).status_code == 400
    r = api.post("/api/v1/auth/login", {"email": "NEU@example.org", "password": PASSWORD})
    assert r.status_code == 200
    me = api.me().json()
    assert me["user"]["role"] == "owner" and me["tenant"]["plan"]["id"] == "free"
    assert api.post("/api/v1/auth/logout").status_code == 200
    assert api.get("/api/v1/auth/me").status_code == 401


def test_session_cookie_flags(env):
    api = env.register(login=False)
    r = api.post("/api/v1/auth/login", {"email": api.email, "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    # the session token is stored only as a hash
    raw = api.c.cookies.get("bs_session")
    assert env.core.db.scalar("SELECT COUNT(*) FROM session WHERE token_hash = ?", (raw,)) == 0


def test_no_account_enumeration(env):
    env.register(email="da@example.org")
    anon = env.client()
    a = anon.post("/api/v1/auth/login", {"email": "da@example.org", "password": "wrong-wrong-wrong"})
    b = anon.post("/api/v1/auth/login", {"email": "nobody@example.org", "password": "wrong-wrong-wrong"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    r1 = anon.post("/api/v1/auth/register", {"org_name": "X", "name": "Y Z", "email": "da@example.org",
                                              "password": PASSWORD, "accept_terms": True})
    r2 = anon.post("/api/v1/auth/password/forgot", {"email": "nobody@example.org"})
    r3 = anon.post("/api/v1/auth/password/forgot", {"email": "da@example.org"})
    assert r1.status_code == 202 and r2.json() == r3.json()


def test_weak_passwords_rejected(env):
    anon = env.client()
    for pw in ("short", "password1234", "aaaaaaaaaaaaaaaa", "maxexample-12345"):
        r = anon.post("/api/v1/auth/register", {"org_name": "X", "name": "Max Example", "email": "max@example.org",
                                                 "password": pw, "accept_terms": True})
        assert r.status_code == 422, pw
        assert r.json()["detail"]["code"] == "weak_password"


def test_lockout_after_failed_attempts(env):
    api = env.register(login=False)
    anon = env.client()
    for _ in range(5):
        assert anon.post("/api/v1/auth/login", {"email": api.email, "password": "wrong-wrong-wrong"}).status_code == 401
    # now locked - even the correct password does not help (same response)
    assert anon.post("/api/v1/auth/login", {"email": api.email, "password": PASSWORD}).status_code == 401
    assert any("locked" in m.subject for m in env.outbox)
    env.clock.advance(6 * 60)
    assert anon.post("/api/v1/auth/login", {"email": api.email, "password": PASSWORD}).status_code == 200


def test_login_rate_limit_per_ip(env):
    anon = env.client()
    codes = [anon.post("/api/v1/auth/login", {"email": f"x{i}@example.org", "password": "x"}).status_code for i in range(15)]
    assert 429 in codes


def test_csrf_required_for_cookie_requests(env):
    api = env.register()
    token = api.csrf
    api.csrf = None
    r = api.post("/api/v1/stations", {"name": "S"})
    assert r.status_code == 403 and r.json()["detail"] == "csrf_failed"
    api.csrf = "falsch"
    assert api.post("/api/v1/stations", {"name": "S"}).status_code == 403
    api.csrf = token
    assert api.post("/api/v1/stations", {"name": "S"}).status_code == 201


def test_cross_origin_requests_rejected(env):
    api = env.register()
    r = api.post("/api/v1/stations", {"name": "S"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["detail"] == "origin_rejected"
    r = api.post("/api/v1/stations", {"name": "S"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert api.post("/api/v1/stations", {"name": "S"}, headers={"Origin": "http://testserver"}).status_code == 201


def test_session_idle_and_absolute_timeout(env):
    api = env.register()
    env.clock.advance(61 * 60)
    assert api.get("/api/v1/auth/me").status_code == 401
    api2 = env.client()
    api2.post("/api/v1/auth/login", {"email": api.email, "password": PASSWORD})
    for _ in range(14):  # active, but > 12 h
        env.clock.advance(55 * 60)
        api2.get("/api/v1/auth/me")
    assert api2.get("/api/v1/auth/me").status_code == 401


def test_password_reset_revokes_sessions(env):
    api = env.register()
    anon = env.client()
    anon.post("/api/v1/auth/password/forgot", {"email": api.email})
    token = env.last_token(api.email)
    assert anon.post("/api/v1/auth/password/reset", {"token": token, "password": "kurz"}).status_code == 422
    assert anon.post("/api/v1/auth/password/reset", {"token": token, "password": "Neues-Passwort-2026?"}).status_code == 200
    assert anon.post("/api/v1/auth/password/reset", {"token": token, "password": "Neues-Passwort-2027?"}).status_code == 400
    assert api.get("/api/v1/auth/me").status_code == 401
    assert anon.post("/api/v1/auth/login", {"email": api.email, "password": "Neues-Passwort-2026?"}).status_code == 200


def test_password_change_revokes_other_sessions(env):
    a = env.register()
    b = env.client()
    b.post("/api/v1/auth/login", {"email": a.email, "password": PASSWORD})
    assert a.post("/api/v1/auth/password/change", {"current_password": "falsch", "new_password": "Neu-Passwort-2026!!"}).status_code == 403
    r = a.post("/api/v1/auth/password/change", {"current_password": PASSWORD, "new_password": "Neu-Passwort-2026!!"})
    assert r.json()["other_sessions_revoked"] == 1
    assert a.me().status_code == 200 and b.get("/api/v1/auth/me").status_code == 401


def test_sessions_list_and_revoke(env):
    a = env.register()
    b = env.client()
    b.post("/api/v1/auth/login", {"email": a.email, "password": PASSWORD})
    sessions = a.get("/api/v1/auth/sessions").json()["sessions"]
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = [s for s in sessions if not s["current"]][0]
    assert a.delete(f"/api/v1/auth/sessions/{other['id']}").status_code == 200
    assert b.get("/api/v1/auth/me").status_code == 401


def _enable_mfa(env, api):
    setup = api.post("/api/v1/auth/mfa/setup").json()
    secret = setup["secret"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert api.post("/api/v1/auth/mfa/enable", {"code": "000000"}).status_code == 400
    r = api.post("/api/v1/auth/mfa/enable", {"code": totp_now(secret, env.clock())})
    assert r.status_code == 200
    return secret, r.json()["recovery_codes"]


def test_mfa_login_replay_and_recovery_codes(env):
    api = env.register()
    secret, codes = _enable_mfa(env, api)
    assert len(codes) == 10
    # secret stored encrypted
    blob = env.core.db.scalar("SELECT totp_secret_enc FROM user WHERE email = ?", (api.email,))
    assert secret.encode() not in blob
    anon = env.client()
    r = anon.post("/api/v1/auth/login", {"email": api.email, "password": PASSWORD})
    assert r.json()["mfa_required"] is True and "csrf_token" not in r.json()
    mfa_token = r.json()["mfa_token"]
    assert anon.get("/api/v1/auth/me").status_code == 401  # no session yet
    assert anon.post("/api/v1/auth/login/mfa", {"mfa_token": mfa_token, "code": "123456"}).status_code == 401
    env.clock.advance(30)
    code = totp_now(secret, env.clock())
    assert anon.post("/api/v1/auth/login/mfa", {"mfa_token": mfa_token, "code": code}).status_code == 200
    # replay: same code rejected at a new sign-in
    other = env.client()
    t2 = other.post("/api/v1/auth/login", {"email": api.email, "password": PASSWORD}).json()["mfa_token"]
    assert other.post("/api/v1/auth/login/mfa", {"mfa_token": t2, "code": code}).status_code == 401
    # a recovery code works exactly once
    assert other.post("/api/v1/auth/login/mfa", {"mfa_token": t2, "code": codes[0].upper()}).status_code == 200
    third = env.client()
    t3 = third.post("/api/v1/auth/login", {"email": api.email, "password": PASSWORD}).json()["mfa_token"]
    assert third.post("/api/v1/auth/login/mfa", {"mfa_token": t3, "code": codes[0]}).status_code == 401


def test_tenant_mfa_enforcement(env):
    owner = env.register()
    assert owner.patch("/api/v1/org", {"mfa_required": True}).json()["detail"] == "enable_own_mfa_first"
    _enable_mfa(env, owner)
    owner.me()
    assert owner.patch("/api/v1/org", {"mfa_required": True}).status_code == 200
    member = env.invite(owner, "m@example.org", "viewer")
    r = member.get("/api/v1/stations")
    assert r.status_code == 403 and r.json()["detail"] == "mfa_setup_required"
    assert member.me().json()["mfa_setup_required"] is True
    _enable_mfa(env, member)
    assert member.get("/api/v1/stations").status_code == 200
    # disabling is not allowed while the organisation requires 2FA
    assert member.post("/api/v1/auth/mfa/disable", {"password": PASSWORD, "code": "000000"}).json()["detail"] == "mfa_enforced"


def test_invite_token_single_use_and_email_bound(env):
    owner = env.register()
    owner.post("/api/v1/org/invitations", {"email": "neu@example.org", "role": "operator"})
    token = env.last_token("neu@example.org")
    info = env.client().post("/api/v1/auth/invite/info", {"token": token}).json()
    assert info["email"] == "neu@example.org" and info["role"] == "operator"
    a = env.client()
    assert a.post("/api/v1/auth/invite/accept", {"token": token, "name": "N N", "password": PASSWORD}).status_code == 200
    assert a.me().json()["user"]["role"] == "operator"
    assert env.client().post("/api/v1/auth/invite/accept", {"token": token, "name": "N N", "password": PASSWORD}).status_code == 400


def test_account_delete_protects_last_owner(env):
    owner = env.register()
    assert owner.post("/api/v1/auth/me/delete", {"password": PASSWORD}).json()["detail"] == "last_owner"
    member = env.invite(owner, "weg@example.org", "viewer")
    assert member.post("/api/v1/auth/me/delete", {"password": PASSWORD}).status_code == 200
    assert env.core.db.scalar("SELECT COUNT(*) FROM user WHERE email = 'weg@example.org'") == 0
