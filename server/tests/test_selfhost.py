"""Self-hosting without domain and mail: setup token, sign-up approval, links instead of e-mails,
several access hosts, production validation with IP URLs, CA pinning for the agent."""

from __future__ import annotations

import base64
import datetime
import hashlib
import re

import pytest

from app.config import ConfigError, load_settings

from conftest import PASSWORD
from test_auth import _enable_mfa

NO_MAIL = {"BIKE_MAIL_BACKEND": "none"}


def setup_admin(env, email="ops@example.org"):
    token = env.core.setup_token_path.read_text().strip()
    api = env.client()
    r = api.post("/api/v1/auth/setup", {"token": token, "org_name": "City of Aachen", "name": "Olivia Ops",
                                        "email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    api.email = email
    return api


# ---------------------------------------------------------------------- first-run setup
def test_setup_token_is_single_use(env):
    anon = env.client()
    assert anon.get("/api/v1/meta").json()["setup_required"] is True
    path = env.core.setup_token_path
    assert path.exists() and (path.stat().st_mode & 0o777) == 0o600
    bad = anon.post("/api/v1/auth/setup", {"token": "bss_" + "x" * 40, "org_name": "X", "name": "Eve Evil",
                                           "email": "eve@example.org", "password": PASSWORD})
    assert bad.status_code == 400
    ops = setup_admin(env)
    me = ops.me().json()
    assert me["user"]["is_platform_admin"] and me["user"]["role"] == "owner"
    assert me["tenant"]["name"] == "City of Aachen" and me["tenant"]["plan"]["id"] == "pro"
    assert me["mfa_setup_required"] is True  # platform admins must set up 2FA first
    assert not path.exists()
    assert anon.get("/api/v1/meta").json()["setup_required"] is False
    again = env.client().post("/api/v1/auth/setup", {"token": "bss_" + "x" * 40, "org_name": "Y", "name": "Eve Evil",
                                                      "email": "eve@example.org", "password": PASSWORD})
    assert again.status_code == 400
    # A restart does not create a new token once an admin exists.
    assert env.core.ensure_setup_token() is None and not path.exists()


def test_setup_token_survives_restart_until_used(env):
    first = env.core.setup_token_path.read_text().strip()
    assert env.core.ensure_setup_token() == first


# ---------------------------------------------------------------------- sign-up approval without mail
def test_signup_needs_approval_without_mail(make_env):
    env = make_env(**NO_MAIL)
    assert env.core.s.signup == "approval"
    meta = env.client().get("/api/v1/meta").json()
    assert meta["signup"] == "approval" and meta["mail_enabled"] is False
    anon = env.client()
    r = anon.post("/api/v1/auth/register", {"org_name": "School B", "name": "Bea Owner", "email": "bea@example.org",
                                            "password": PASSWORD, "accept_terms": True})
    assert r.status_code == 202 and r.json() == {"status": "pending_approval", "verify_email": False}
    # an existing address yields the identical answer (no enumeration)
    r2 = anon.post("/api/v1/auth/register", {"org_name": "School C", "name": "Bea Owner", "email": "bea@example.org",
                                             "password": PASSWORD, "accept_terms": True})
    assert r2.json() == r.json()
    assert env.core.db.scalar("SELECT COUNT(*) FROM tenant") == 1
    assert anon.post("/api/v1/auth/login", {"email": "bea@example.org", "password": "wrong-password-123"}).status_code == 401
    pending = anon.post("/api/v1/auth/login", {"email": "bea@example.org", "password": PASSWORD})
    assert pending.status_code == 403 and pending.json()["detail"] == "pending_approval"
    # the "sign-up attempt" notice for the existing address could not be sent - audited instead of mailed
    assert env.core.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'mail_not_sent' AND target = 'bea@example.org'") == 1

    ops = setup_admin(env)
    _enable_mfa(env, ops)
    tenants = ops.get("/api/v1/platform/tenants").json()["tenants"]
    assert tenants[0]["name"] == "School B" and tenants[0]["status"] == "pending"
    assert ops.get("/api/v1/platform/stats").json()["pending_tenants"] == 1
    assert ops.patch(f"/api/v1/platform/tenants/{tenants[0]['id']}", {"status": "active"}).status_code == 200
    ok = env.client().post("/api/v1/auth/login", {"email": "bea@example.org", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["tenant"]["status"] == "active"


def test_signup_modes_open_and_closed(make_env):
    env = make_env(BIKE_SIGNUP="closed")
    r = env.client().post("/api/v1/auth/register", {"org_name": "X", "name": "Xavier Xu", "email": "x@example.org",
                                                    "password": PASSWORD, "accept_terms": True})
    assert r.status_code == 403 and r.json()["detail"] == "signup_disabled"
    env2 = make_env(BIKE_SIGNUP="approval")  # approval with mail: verify e-mail AND wait for approval
    r = env2.client().post("/api/v1/auth/register", {"org_name": "Y", "name": "Yara Yu", "email": "y@example.org",
                                                     "password": PASSWORD, "accept_terms": True})
    assert r.json() == {"status": "pending_approval", "verify_email": True}
    assert env2.last_token("y@example.org")


# ---------------------------------------------------------------------- links instead of e-mails
def test_invitation_link_and_qr_without_mail(make_env):
    env = make_env(**NO_MAIL)
    ops = setup_admin(env)
    _enable_mfa(env, ops)
    r = ops.post("/api/v1/org/invitations", {"email": "tim@example.org", "role": "operator"})
    assert r.status_code == 201
    inv = r.json()
    assert inv["delivery"] == "link" and inv["link"].startswith("http://testserver/app#/invite?token=")
    assert inv["qr"].startswith("data:image/svg+xml;base64,")
    assert b"<svg" in base64.b64decode(inv["qr"].split(",", 1)[1])
    assert env.core.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'mail_not_sent' AND target = 'tim@example.org'") == 1
    assert "token=" not in (env.core.db.scalar("SELECT detail FROM audit_log WHERE target = 'tim@example.org'") or "")
    token = inv["link"].split("token=")[1]
    tim = env.client()
    assert tim.post("/api/v1/auth/invite/accept", {"token": token, "name": "Tim Team", "password": PASSWORD}).status_code == 200
    assert tim.me().json()["user"]["role"] == "operator"
    # the invitee cannot invite and never sees links
    assert tim.post("/api/v1/org/invitations", {"email": "z@example.org", "role": "viewer"}).status_code == 403


def test_invitation_with_mail_does_not_return_link(env):
    owner = env.register()
    inv = owner.post("/api/v1/org/invitations", {"email": "tim@example.org", "role": "viewer"}).json()
    assert inv == {"status": "invited", "delivery": "email"}


def test_forgot_password_without_mail_asks_admin(make_env):
    env = make_env(**NO_MAIL)
    r = env.client().post("/api/v1/auth/password/forgot", {"email": "anyone@example.org"})
    assert r.status_code == 202 and r.json() == {"status": "ask_admin"}


def test_reset_links_respect_roles(env):
    owner = env.register()
    env.set_plan(owner, "school")
    admin = env.invite(owner, "admin@example.org", "admin")
    admin2 = env.invite(owner, "admin2@example.org", "admin")
    op = env.invite(owner, "op@example.org", "operator")
    users = {u["email"]: u["id"] for u in owner.get("/api/v1/org/users").json()["users"]}
    assert admin.post(f"/api/v1/org/users/{users[owner.email]}/reset-link").status_code == 403
    assert admin.post(f"/api/v1/org/users/{users['admin2@example.org']}/reset-link").status_code == 403
    assert admin.post(f"/api/v1/org/users/{users['admin@example.org']}/reset-link").status_code == 409
    assert op.post(f"/api/v1/org/users/{users['op@example.org']}/reset-link").status_code == 403
    r = admin.post(f"/api/v1/org/users/{users['op@example.org']}/reset-link")
    assert r.status_code == 200 and r.json()["qr"].startswith("data:image/svg+xml")
    token = r.json()["link"].split("token=")[1]
    assert env.client().post("/api/v1/auth/password/reset", {"token": token, "password": "Another-Passphrase-2027!"}).status_code == 200
    assert op.get("/api/v1/auth/me").status_code == 401  # sessions ended
    assert env.client().post("/api/v1/auth/login", {"email": "op@example.org", "password": "Another-Passphrase-2027!"}).status_code == 200
    assert owner.post(f"/api/v1/org/users/{users['admin2@example.org']}/reset-link").status_code == 200
    assert env.core.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'reset_link_created'") == 2
    other = env.register(org="Other")
    assert other.post(f"/api/v1/org/users/{users['op@example.org']}/reset-link").status_code == 404
    del admin2


def test_platform_owner_reset_link(make_env):
    env = make_env(**NO_MAIL)
    ops = setup_admin(env)
    _enable_mfa(env, ops)
    env.client().post("/api/v1/auth/register", {"org_name": "School B", "name": "Bea Owner", "email": "bea@example.org",
                                                "password": PASSWORD, "accept_terms": True})
    tid = next(t["id"] for t in ops.get("/api/v1/platform/tenants").json()["tenants"] if t["name"] == "School B")
    r = ops.post(f"/api/v1/platform/tenants/{tid}/owner-reset-link")
    assert r.status_code == 200 and r.json()["email"] == "bea@example.org"
    own = ops.me().json()["tenant"]["id"]
    assert ops.post(f"/api/v1/platform/tenants/{own}/owner-reset-link").status_code == 409


# ---------------------------------------------------------------------- several access hosts
def test_all_allowed_hosts_are_valid_origins(make_env):
    env = make_env(BIKE_ALLOWED_HOSTS="testserver,10.0.0.5,bikestation.local")
    assert env.core.s.allowed_origins == {"http://testserver", "http://10.0.0.5", "http://bikestation.local"}
    anon = env.client()
    body = {"email": "nobody@example.org", "password": "wrong-wrong-wrong"}
    assert anon.post("/api/v1/auth/login", body, headers={"Origin": "http://bikestation.local"}).status_code == 401
    assert anon.post("/api/v1/auth/login", body, headers={"Origin": "http://10.0.0.5"}).status_code == 401
    r = anon.post("/api/v1/auth/login", body, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["detail"] == "origin_rejected"
    assert anon.get("/health", headers={"Host": "bikestation.local"}).status_code == 200
    assert anon.get("/health", headers={"Host": "evil.example"}).status_code == 400


def test_origins_keep_the_port_of_the_base_url(make_env):
    env = make_env(BIKE_BASE_URL="https://10.0.0.5:8443", BIKE_ALLOWED_HOSTS="10.0.0.5,bikestation.local")
    assert env.core.s.allowed_origins == {"https://10.0.0.5:8443", "https://bikestation.local:8443"}


# ---------------------------------------------------------------------- production validation
def _prod(monkeypatch, tmp_path, **extra):
    env = {"BIKE_ENV": "production", "BIKE_BASE_URL": "https://192.168.1.50", "BIKE_ALLOWED_HOSTS": "192.168.1.50,bikestation.local",
           "BIKE_MAIL_BACKEND": "none", "BIKE_DATA_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
           "BIKE_DB_PATH": str(tmp_path / "p.db"), "BIKE_SCRYPT_N": "32768"}
    env.update(extra)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return load_settings()


def test_production_allows_ip_url_without_mail(monkeypatch, tmp_path):
    s = _prod(monkeypatch, tmp_path)
    assert s.production and s.signup == "approval" and not s.mail_enabled


@pytest.mark.parametrize("extra,problem", [
    ({"BIKE_BASE_URL": "http://192.168.1.50"}, "https"),
    ({"BIKE_ALLOWED_HOSTS": "*"}, "allowed_hosts"),
    ({"BIKE_MAIL_BACKEND": "console"}, "mail.backend"),
    ({"BIKE_MAIL_BACKEND": "smtp"}, "smtp_host"),
    ({"BIKE_ALLOWED_HOSTS": "bikestation.local"}, "must be in allowed_hosts"),
    ({"BIKE_DATA_KEY": ""}, "BIKE_DATA_KEY"),
])
def test_production_rejects_insecure_settings(monkeypatch, tmp_path, extra, problem):
    with pytest.raises(ConfigError, match=re.escape(problem)):
        _prod(monkeypatch, tmp_path, **extra)


# ---------------------------------------------------------------------- CA pinning
def make_ca(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Smart Bike Station test CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    path = tmp_path / "ca.crt"
    path.write_bytes(pem)
    return path, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def test_ca_is_served_embedded_and_pinned(make_env, tmp_path):
    ca, digest = make_ca(tmp_path)
    env = make_env(BIKE_CA_FILE=str(ca))
    anon = env.client()
    r = anon.get("/install/ca.crt")
    assert r.status_code == 200 and r.content == ca.read_bytes() and r.headers["x-certificate-sha256"] == digest
    script = anon.get("/install/agent.sh").text
    assert ca.read_text().strip() in script and "__CA_PEM__" not in script
    owner = env.register()
    sid, _ = env.station(owner)
    info = owner.post(f"/api/v1/stations/{sid}/enrollments", {}).json()["install"]
    assert info["ca_fingerprint"].replace(":", "").lower() == digest
    assert info["commands"]["download"].startswith("curl -fsSLk -o agent.sh ")
    assert info["commands"]["oneliner"] is None


def test_without_ca_nothing_is_embedded(env):
    assert env.client().get("/install/ca.crt").status_code == 404
    script = env.client().get("/install/agent.sh").text
    assert "BEGIN CERTIFICATE" not in script


def test_missing_ca_file_is_a_config_error(monkeypatch, tmp_path):
    monkeypatch.setenv("BIKE_CA_FILE", str(tmp_path / "nope.crt"))
    monkeypatch.setenv("BIKE_DB_PATH", str(tmp_path / "x.db"))
    with pytest.raises(ConfigError, match="CA file"):
        load_settings()


# ---------------------------------------------------------------------- HTTP -> HTTPS redirect
def test_redirect_target_never_leaves_the_allowed_hosts():
    from app.redirect import target_for

    allowed = {"192.168.1.50", "bikestation.local"}
    base = "https://192.168.1.50"
    assert target_for("bikestation.local", "/app?x=1", base, allowed) == "https://bikestation.local/app?x=1"
    assert target_for("bikestation.local:80", "/", base, allowed) == "https://bikestation.local/"
    assert target_for("evil.example", "/app", base, allowed) == "https://192.168.1.50/app"
    assert target_for("", "//evil.example/x", base, allowed) == "https://192.168.1.50/"
    assert target_for("192.168.1.50", "/x", "https://192.168.1.50:8443", allowed) == "https://192.168.1.50:8443/x"
