"""Mandantentrennung, Rollen, Tarif-Limits, Export/Löschung, Plattform-Admin, Header."""

from __future__ import annotations

import json
import time

from app.security import hash_password

from conftest import PASSWORD, Device


def test_tenants_cannot_see_each_other(env):
    a = env.register(org="A")
    b = env.register(org="B")
    sid_a, token_a = env.station(a)
    Device(a, sid_a, token_a).send("A", occupied=True)
    assert b.get("/api/v1/stations").json()["stations"] == []
    for url in (f"/api/v1/stations/{sid_a}", f"/api/v1/stations/{sid_a}/status", f"/api/v1/stations/{sid_a}/devices",
                f"/api/v1/stations/{sid_a}/occupancy"):
        assert b.get(url).status_code == 404, url
    assert b.patch(f"/api/v1/stations/{sid_a}", {"name": "gekapert"}).status_code == 404
    assert b.delete(f"/api/v1/stations/{sid_a}").status_code == 404
    assert b.post(f"/api/v1/stations/{sid_a}/devices", {"name": "x"}).status_code == 404
    env.core.db.execute("INSERT INTO event (id, tenant_id, station_id, slot_id, kind, severity, occurred_at) "
                        "SELECT 'evt_x', tenant_id, station_id, slot_id, 'sensor_fault', 'info', 1 FROM measurement "
                        "JOIN station ON station.id = measurement.station_id LIMIT 1")
    assert b.get("/api/v1/events").json()["events"] == []
    assert b.post("/api/v1/events/evt_x/ack").status_code in (403, 404)
    a_users = a.get("/api/v1/org/users").json()["users"]
    assert b.patch(f"/api/v1/org/users/{a_users[0]['id']}", {"role": "viewer"}).status_code == 404


def test_device_token_bound_to_its_station(env):
    a = env.register(org="A")
    b = env.register(org="B")
    sid_a, token_a = env.station(a)
    sid_b, _ = env.station(b)
    r = Device(a, sid_a, token_a).send("A", station=sid_b)
    assert r.status_code == 403 and r.json()["detail"] == "station_mismatch"


def test_roles(env):
    owner = env.register()
    env.set_plan(owner, "school")
    sid, _ = env.station(owner)
    viewer = env.invite(owner, "v@example.org", "viewer")
    operator = env.invite(owner, "o@example.org", "operator")
    admin = env.invite(owner, "a@example.org", "admin")
    assert viewer.get(f"/api/v1/stations/{sid}/status").status_code == 200
    assert viewer.post("/api/v1/stations", {"name": "x"}).status_code == 403
    assert operator.post("/api/v1/stations", {"name": "x"}).status_code == 403
    assert operator.get(f"/api/v1/stations/{sid}/devices").status_code == 403
    assert viewer.post("/api/v1/org/invitations", {"email": "z@example.org", "role": "viewer"}).status_code == 403
    assert admin.get(f"/api/v1/stations/{sid}/devices").status_code == 200
    # Admin darf niemanden zum Owner machen und keine Owner entfernen
    users = {u["email"]: u for u in owner.get("/api/v1/org/users").json()["users"]}
    assert admin.patch(f"/api/v1/org/users/{users['v@example.org']['id']}", {"role": "owner"}).status_code == 403
    assert admin.delete(f"/api/v1/org/users/{users[owner.email]['id']}").status_code == 403
    assert admin.patch("/api/v1/org", {"mfa_required": True}).status_code == 403
    assert admin.post("/api/v1/org/plan", {"plan": "pro"}).status_code == 403
    # Rollenänderung beendet die Sitzungen des Betroffenen
    assert owner.patch(f"/api/v1/org/users/{users['v@example.org']['id']}", {"role": "operator"}).status_code == 200
    assert viewer.get("/api/v1/auth/me").status_code == 401


def test_last_owner_cannot_be_demoted(env):
    owner = env.register()
    me = owner.me().json()["user"]["id"]
    assert owner.patch(f"/api/v1/org/users/{me}", {"role": "admin"}).json()["detail"] == "last_owner"


def test_plan_limits_and_upgrade(env):
    owner = env.register()
    env.station(owner, name="Eins")
    r = owner.post("/api/v1/stations", {"name": "Zwei"})
    assert r.status_code == 409 and r.json()["detail"]["limit"] == "max_stations"
    r = owner.post("/api/v1/stations", {"name": "x", "slots": [{"key": str(i), "label": "p"} for i in range(9)]})
    assert r.status_code == 409
    env.invite(owner, "zwei@example.org", "viewer")  # Free: 2 Nutzer
    assert owner.post("/api/v1/org/invitations", {"email": "drei@example.org", "role": "viewer"}).status_code == 409
    sid = owner.get("/api/v1/stations").json()["stations"][0]["id"]
    assert owner.patch(f"/api/v1/stations/{sid}", {"alert_source": "ml"}).status_code == 402
    assert owner.get("/api/v1/org/audit").status_code == 402
    assert owner.post("/api/v1/org/plan", {"plan": "school"}).status_code == 200
    assert owner.post("/api/v1/stations", {"name": "Zwei"}).status_code == 201
    assert owner.get("/api/v1/org/audit").status_code == 200
    actions = [e["action"] for e in owner.get("/api/v1/org/audit").json()["entries"]]
    assert "plan_changed" in actions and "station_created" in actions
    # Downgrade blockiert, solange Nutzung über dem Limit liegt
    assert owner.post("/api/v1/org/plan", {"plan": "free"}).status_code == 409


def test_export_contains_no_secrets(env):
    owner = env.register()
    sid, token = env.station(owner)
    Device(owner, sid, token).send("A", occupied=True)
    r = owner.get("/api/v1/org/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    text = r.text
    data = json.loads(text)
    assert data["stations"][0]["id"] == sid and len(data["measurements"]) == 1
    for secret_word in ("password_hash", "token_hash", "totp_secret", "scrypt$", token):
        assert secret_word not in text


def test_delete_org_cascades(env):
    owner = env.register(org="Weg GmbH")
    sid, token = env.station(owner)
    Device(owner, sid, token).send("A", occupied=True)
    assert owner.post("/api/v1/org/delete", {"password": PASSWORD, "confirm_name": "Falsch"}).status_code == 422
    assert owner.post("/api/v1/org/delete", {"password": PASSWORD, "confirm_name": "Weg GmbH"}).status_code == 200
    db = env.core.db
    for table in ("tenant", "user", "station", "slot", "device", "measurement", "session"):
        assert db.scalar(f"SELECT COUNT(*) FROM {table}") == 0, table


def _platform_admin(env):
    now = time.time()
    env.core.db.execute(
        "INSERT INTO user (id, tenant_id, email, name, role, password_hash, email_verified_at, is_platform_admin, created_at, password_changed_at) "
        "VALUES ('usr_ops', NULL, 'ops@example.org', 'Ops', 'platform', ?, ?, 1, ?, ?)",
        (hash_password(PASSWORD, 1024), now, now, now))
    api = env.client()
    assert api.post("/api/v1/auth/login", {"email": "ops@example.org", "password": PASSWORD}).status_code == 200
    return api


def test_platform_admin_requires_mfa_and_can_suspend(env):
    owner = env.register()
    sid, token = env.station(owner)
    ops = _platform_admin(env)
    assert ops.get("/api/v1/platform/tenants").json()["detail"] == "mfa_setup_required"
    assert owner.get("/api/v1/platform/tenants").status_code == 403
    from test_auth import _enable_mfa

    _enable_mfa(env, ops)
    tenants = ops.get("/api/v1/platform/tenants").json()["tenants"]
    assert len(tenants) == 1
    assert ops.get("/api/v1/platform/stats").json()["stations"] == 1
    assert ops.patch(f"/api/v1/platform/tenants/{tenants[0]['id']}", {"status": "suspended"}).status_code == 200
    # Gesperrter Mandant: Sitzungen beendet, Geräte abgewiesen
    assert owner.get("/api/v1/stations").status_code == 401
    assert Device(owner, sid, token).send().status_code == 403
    again = env.client()
    again.post("/api/v1/auth/login", {"email": owner.email, "password": PASSWORD})
    assert again.get("/api/v1/stations").json()["detail"] == "tenant_suspended"


def test_security_headers(env):
    api = env.client()
    r = api.get("/")
    h = r.headers
    assert "default-src 'none'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer" and "camera=()" in h["permissions-policy"]
    assert h["cross-origin-opener-policy"] == "same-origin" and "x-request-id" in h
    assert api.get("/api/v1/meta").headers["cache-control"] == "no-store"
    assert api.get("/docs").status_code == 404 and api.get("/openapi.json").status_code == 404
    assert api.get("/.well-known/security.txt").status_code == 200


def test_untrusted_host_rejected(env):
    api = env.client()
    assert api.get("/health", headers={"Host": "evil.example"}).status_code == 400


def test_body_limit(env):
    api = env.client()
    r = api.c.post("/api/v1/auth/login", content=b"{" + b" " * 70_000 + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_validation_errors_do_not_echo_input(env):
    api = env.client()
    r = api.post("/api/v1/auth/register", {"org_name": "X", "name": "Y", "email": "kaputt", "password": "GeheimGeheim123!",
                                            "accept_terms": True})
    assert r.status_code == 422 and "GeheimGeheim123!" not in r.text and "kaputt" not in r.text
