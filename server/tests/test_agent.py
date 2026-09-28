"""Agent installation system: pairing, heartbeat, configuration, commands, token rotation, package."""

from __future__ import annotations

import hashlib
import io
import tarfile

from conftest import Device


def setup_station(env, plan="school"):
    owner = env.register()
    env.set_plan(owner, plan)
    r = owner.post("/api/v1/stations", {"name": "Yard", "slots": [{"key": k, "label": f"Space {k}"} for k in "AB"]})
    return owner, r.json()["id"]


def enroll(env, owner, sid, **extra):
    code = owner.post(f"/api/v1/stations/{sid}/enrollments", {"name": "Pi yard"}).json()["code"]
    r = env.client().c.post("/api/v1/agent/enroll", json={"code": code, "hostname": "raspi-yard", "agent_version": "0.9.0",
                                                          "os_info": "Raspberry Pi OS", **extra})
    return code, r


def hb(env, token, **body):
    return env.client().c.post("/api/v1/agent/heartbeat", json={"agent_version": "1.0.0", **body},
                               headers={"Authorization": f"Bearer {token}"})


def test_enrollment_code_flow(env):
    owner, sid = setup_station(env)
    r = owner.post(f"/api/v1/stations/{sid}/enrollments", {"name": "Pi yard"})
    assert r.status_code == 201
    data = r.json()
    assert len(data["code"]) == 11 and data["code"][5] == "-"
    assert data["code"] in data["install"]["commands"]["install"]
    assert data["install"]["script_sha256"] and data["install"]["version"]
    assert len(owner.get(f"/api/v1/stations/{sid}/enrollments").json()["enrollments"]) == 1
    # the code is stored only as a hash
    assert env.core.db.scalar("SELECT COUNT(*) FROM enrollment WHERE code_hash = ?", (data["code"],)) == 0
    # lower case / without hyphen is accepted
    r = env.client().c.post("/api/v1/agent/enroll", json={"code": data["code"].replace("-", "").lower(), "hostname": "raspi-yard"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["station_id"] == sid and e["slot_map"] == {"A": "A", "B": "B"} and e["token"].startswith("bsd_")
    # single use
    again = env.client().c.post("/api/v1/agent/enroll", json={"code": data["code"]})
    assert again.status_code == 400
    devices = owner.get(f"/api/v1/stations/{sid}/devices").json()["devices"]
    assert devices[0]["managed"] and devices[0]["hostname"] == "raspi-yard"
    # the token works for measurements of this station
    assert Device(owner, sid, e["token"]).send("A", occupied=True).status_code == 202


def test_enrollment_expires_and_can_be_revoked(env):
    owner, sid = setup_station(env)
    code = owner.post(f"/api/v1/stations/{sid}/enrollments", {}).json()["code"]
    env.clock.advance(31 * 60)
    assert env.client().c.post("/api/v1/agent/enroll", json={"code": code}).status_code == 400
    r = owner.post(f"/api/v1/stations/{sid}/enrollments", {}).json()
    assert owner.delete(f"/api/v1/stations/{sid}/enrollments/{r['id']}").status_code == 200
    assert env.client().c.post("/api/v1/agent/enroll", json={"code": r["code"]}).status_code == 400


def test_enrollment_permissions_and_isolation(env):
    owner, sid = setup_station(env)
    other = env.register(org="Fremd")
    assert other.post(f"/api/v1/stations/{sid}/enrollments", {}).status_code == 404
    viewer = env.invite(owner, "v@example.org", "viewer")
    assert viewer.post(f"/api/v1/stations/{sid}/enrollments", {}).status_code == 403
    assert viewer.get("/api/v1/devices").status_code == 200


def test_enroll_rate_limited(env):
    anon = env.client()
    codes = [anon.c.post("/api/v1/agent/enroll", json={"code": f"AAAAA-AAAA{i % 10}"}).status_code for i in range(15)]
    assert 429 in codes


def test_heartbeat_health_config_and_commands(env):
    owner, sid = setup_station(env)
    _, r = enroll(env, owner, sid)
    token = r.json()["token"]
    resp = hb(env, token, serial_connected=True, buffer_len=3, cpu_temp_c=48.5, uptime_s=120, config_version=1).json()
    assert resp["config_version"] == 1 and resp["commands"] == [] and resp["update"] is None
    fleet = owner.get("/api/v1/devices").json()
    d = fleet["devices"][0]
    assert d["online"] and d["health"]["cpu_temp_c"] == 48.5 and d["station_name"] == "Yard"
    # new slot -> new configuration version
    owner.post(f"/api/v1/stations/{sid}/slots", {"key": "C", "label": "Space C"})
    resp = hb(env, token).json()
    assert resp["config_version"] == 2 and "C" in resp["slot_map"]
    # remote command, delivered exactly once
    assert owner.post(f"/api/v1/stations/{sid}/devices/{d['id']}/command", {"command": "restart"}).status_code == 200
    assert hb(env, token).json()["commands"] == ["restart"]
    assert hb(env, token).json()["commands"] == []
    assert owner.post(f"/api/v1/stations/{sid}/devices/{d['id']}/command", {"command": "rm -rf /"}).status_code == 422
    # offline after 3 minutes without heartbeat
    env.clock.advance(200)
    assert owner.get("/api/v1/devices").json()["devices"][0]["online"] is False


def test_update_offered_only_when_newer_and_allowed(env):
    owner, sid = setup_station(env)
    _, r = enroll(env, owner, sid)
    token = r.json()["token"]
    latest = env.app.state.agent_bundle.version
    upd = hb(env, token, agent_version="0.1.0").json()["update"]
    assert upd["version"] == latest and len(upd["sha256"]) == 64
    assert hb(env, token, agent_version=latest).json()["update"] is None
    assert hb(env, token, agent_version="99.0.0").json()["update"] is None  # no downgrade
    owner.patch(f"/api/v1/stations/{sid}", {"auto_update": False})
    assert hb(env, token, agent_version="0.1.0").json()["update"] is None
    did = owner.get("/api/v1/devices").json()["devices"][0]["id"]
    owner.post(f"/api/v1/stations/{sid}/devices/{did}/command", {"command": "update"})
    assert hb(env, token, agent_version="0.1.0").json()["update"] is not None


def test_token_rotation_with_grace_period(env):
    owner, sid = setup_station(env)
    _, r = enroll(env, owner, sid)
    old = r.json()["token"]
    new = env.client().c.post("/api/v1/agent/rotate-token", headers={"Authorization": f"Bearer {old}"}).json()["token"]
    assert new != old
    assert hb(env, old).status_code == 200      # grace period
    assert hb(env, new).status_code == 200      # new token used ...
    assert hb(env, old).status_code == 401      # ... old one invalid immediately
    # without using the new token the grace period expires
    newer = env.client().c.post("/api/v1/agent/rotate-token", headers={"Authorization": f"Bearer {new}"}).json()["token"]
    env.clock.advance(16 * 60)
    assert hb(env, new).status_code == 401 and hb(env, newer).status_code == 200


def test_revoked_device_rejected(env):
    owner, sid = setup_station(env)
    _, r = enroll(env, owner, sid)
    token = r.json()["token"]
    did = owner.get("/api/v1/devices").json()["devices"][0]["id"]
    owner.delete(f"/api/v1/stations/{sid}/devices/{did}")
    assert hb(env, token).status_code == 401


def test_device_limit_applies_to_enrollment(env):
    owner, sid = setup_station(env)
    for _ in range(5):
        owner.post(f"/api/v1/stations/{sid}/devices", {"name": "x"})
    assert owner.post(f"/api/v1/stations/{sid}/enrollments", {}).status_code == 409


def test_install_script_and_bundle(env):
    anon = env.client()
    b = env.app.state.agent_bundle
    script = anon.get("/install/agent.sh")
    assert script.status_code == 200 and script.content == b.script
    text = script.text
    assert text.startswith("#!/bin/sh") and b.sha256 in text and "http://testserver" in text
    assert "__SHA256__" not in text and "--code" in text
    assert hashlib.sha256(script.content).hexdigest() == b.script_sha256
    tgz = anon.get("/install/agent.tar.gz")
    assert hashlib.sha256(tgz.content).hexdigest() == b.sha256 == tgz.headers["x-content-sha256"]
    with tarfile.open(fileobj=io.BytesIO(tgz.content), mode="r:gz") as tar:
        names = sorted(m.name for m in tar.getmembers())
        assert names[0] == "VERSION" and {"bikeagent/__init__.py", "bikeagent/__main__.py", "bikeagent/agent.py", "bikeagent/gateway.py", "bikeagent/simulator.py"} <= set(names)
        assert all(m.isfile() and m.mtime == 0 for m in tar.getmembers())
    assert b.sha256 in anon.get("/install/agent.sha256").text
    # reproducible
    from app.agent_bundle import AgentBundle

    assert AgentBundle.build("http://testserver").sha256 == b.sha256


def test_migration_from_schema_2(tmp_path):
    import sqlite3

    from app.db import Database

    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript(
        "CREATE TABLE station (id TEXT PRIMARY KEY, tenant_id TEXT, name TEXT, location TEXT, alert_source TEXT, "
        "display_token_hash TEXT, display_enabled INTEGER, created_at REAL);"
        "CREATE TABLE device (id TEXT PRIMARY KEY, tenant_id TEXT, station_id TEXT, name TEXT, token_prefix TEXT, "
        "token_hash TEXT UNIQUE, created_at REAL, created_by TEXT, last_seen_at REAL, last_ip TEXT, revoked_at REAL);"
        "PRAGMA user_version = 2;")
    con.close()
    db = Database(p)
    cols = {r[1] for r in db.all("PRAGMA table_info(device)")}
    assert {"hostname", "prev_token_hash", "last_heartbeat_at"} <= cols
    assert db.scalar("PRAGMA user_version") == 4


def test_migration_from_schema_3_keeps_events(tmp_path):
    import sqlite3

    from app.db import SCHEMA_VERSION, Database

    p = tmp_path / "v3.db"
    con = sqlite3.connect(p)
    con.executescript(
        "CREATE TABLE tenant (id TEXT PRIMARY KEY, name TEXT, plan TEXT, status TEXT, mfa_required INTEGER, created_at REAL);"
        "CREATE TABLE station (id TEXT PRIMARY KEY, tenant_id TEXT, name TEXT, location TEXT, alert_source TEXT, "
        "display_token_hash TEXT, display_enabled INTEGER, config_version INTEGER, auto_update INTEGER, created_at REAL);"
        "CREATE TABLE slot (id TEXT PRIMARY KEY, station_id TEXT, key TEXT, label TEXT, position INTEGER);"
        "CREATE TABLE device (id TEXT PRIMARY KEY, tenant_id TEXT, station_id TEXT, name TEXT, token_prefix TEXT, "
        "token_hash TEXT UNIQUE, created_at REAL, created_by TEXT, last_seen_at REAL, last_ip TEXT, revoked_at REAL, "
        "enrolled_at REAL, hostname TEXT, agent_version TEXT, os_info TEXT, source TEXT, last_heartbeat_at REAL, health TEXT, "
        "pending_command TEXT, update_requested INTEGER NOT NULL DEFAULT 0, prev_token_hash TEXT, "
        "prev_token_valid_until REAL, token_rotated_at REAL);"
        "CREATE TABLE event (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, station_id TEXT NOT NULL, slot_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, severity TEXT NOT NULL, detector TEXT, detail TEXT, occurred_at REAL NOT NULL, "
        "acknowledged_at REAL, acknowledged_by TEXT, source TEXT NOT NULL DEFAULT 'live');"
        "INSERT INTO tenant VALUES ('org_1', 'A', 'free', 'active', 0, 1);"
        "INSERT INTO station VALUES ('st_1', 'org_1', 'S', '', 'rule', NULL, 0, 1, 1, 1);"
        "INSERT INTO slot VALUES ('sl_1', 'st_1', 'A', 'Space A', 1);"
        "INSERT INTO event VALUES ('evt_1', 'org_1', 'st_1', 'sl_1', 'sensor_fault', 'info', NULL, NULL, 5, NULL, NULL, 'live');"
        "PRAGMA user_version = 3;")
    con.close()
    db = Database(p)
    assert db.scalar("PRAGMA user_version") == SCHEMA_VERSION == 4
    assert db.one("SELECT slot_id, kind FROM event WHERE id = 'evt_1'")["slot_id"] == "sl_1"
    cols = {r[1]: r for r in db.all("PRAGMA table_info(event)")}
    assert "device_id" in cols and cols["slot_id"][3] == 0  # slot_id is nullable now
    assert "offline_notified" in {r[1] for r in db.all("PRAGMA table_info(device)")}
    assert {"lead", "api_key", "webhook", "setting"} <= {r[0] for r in db.all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert db.scalar("PRAGMA foreign_keys") == 1
    db.close()
    Database(p).close()  # opening again is a no-op
