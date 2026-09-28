"""Agent-Installationssystem: Kopplung, Heartbeat, Konfiguration, Befehle, Token-Rotation, Paket."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

from conftest import Device


def setup_station(env, plan="school"):
    owner = env.register()
    env.set_plan(owner, plan)
    r = owner.post("/api/v1/stations", {"name": "Hof"})
    return owner, r.json()["id"]


def enroll(env, owner, sid, **extra):
    code = owner.post(f"/api/v1/stations/{sid}/enrollments", {"name": "Pi Hof"}).json()["code"]
    r = env.client().c.post("/api/v1/agent/enroll", json={"code": code, "hostname": "raspi-hof", "agent_version": "0.9.0",
                                                          "os_info": "Raspberry Pi OS", **extra})
    return code, r


AGENT_VERSION = (Path(__file__).resolve().parents[2] / "pi-gateway" / "VERSION").read_text().strip()


def hb(env, token, **body):
    return env.client().c.post("/api/v1/agent/heartbeat", json={"agent_version": AGENT_VERSION, **body},
                               headers={"Authorization": f"Bearer {token}"})


def test_enrollment_code_flow(env):
    owner, sid = setup_station(env)
    r = owner.post(f"/api/v1/stations/{sid}/enrollments", {"name": "Pi Hof"})
    assert r.status_code == 201
    data = r.json()
    assert len(data["code"]) == 11 and data["code"][5] == "-"
    assert data["code"] in data["install"]["commands"]["install"]
    assert data["install"]["script_sha256"] and data["install"]["version"]
    assert len(owner.get(f"/api/v1/stations/{sid}/enrollments").json()["enrollments"]) == 1
    # Code wird nur gehasht gespeichert
    assert env.core.db.scalar("SELECT COUNT(*) FROM enrollment WHERE code_hash = ?", (data["code"],)) == 0
    # Kleinschreibung/ohne Bindestrich wird akzeptiert
    r = env.client().c.post("/api/v1/agent/enroll", json={"code": data["code"].replace("-", "").lower(), "hostname": "raspi-hof"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["station_id"] == sid and "slot_map" not in e and e["token"].startswith("bsd_")
    # Einmalig
    again = env.client().c.post("/api/v1/agent/enroll", json={"code": data["code"]})
    assert again.status_code == 400
    devices = owner.get(f"/api/v1/stations/{sid}/devices").json()["devices"]
    assert devices[0]["managed"] and devices[0]["hostname"] == "raspi-hof"
    # Token funktioniert für Messungen dieser Station
    assert Device(owner, sid, e["token"]).send(occupied=True).status_code == 202


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
    assert d["online"] and d["health"]["cpu_temp_c"] == 48.5 and d["station_name"] == "Hof"
    # Fernbefehl, genau einmal ausgeliefert
    assert owner.post(f"/api/v1/stations/{sid}/devices/{d['id']}/command", {"command": "restart"}).status_code == 200
    assert hb(env, token).json()["commands"] == ["restart"]
    assert hb(env, token).json()["commands"] == []
    assert owner.post(f"/api/v1/stations/{sid}/devices/{d['id']}/command", {"command": "rm -rf /"}).status_code == 422
    # offline nach 3 Minuten ohne Heartbeat
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
    assert hb(env, token, agent_version="99.0.0").json()["update"] is None  # kein Downgrade
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
    assert hb(env, old).status_code == 200      # Übergangsfrist
    assert hb(env, new).status_code == 200      # neues Token benutzt ...
    assert hb(env, old).status_code == 401      # ... altes sofort ungültig
    # Ohne Nutzung des neuen Tokens läuft die Frist ab
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
        assert names == ["VERSION", "agent.py", "camera.py", "gateway.py", "sim-camera.jpg", "simulator.py"]
        assert all(m.isfile() and m.mtime == 0 for m in tar.getmembers())
    assert b.sha256 in anon.get("/install/agent.sha256").text
    # Reproduzierbar
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
    assert db.scalar("PRAGMA user_version") == 5


def test_migration_to_single_stall(tmp_path):
    """Schema 3 -> 4: jede Station behält genau einen Stellplatz (den ersten)."""
    from app.db import SCHEMA, Database

    p = tmp_path / "v3.db"
    import sqlite3

    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.executescript(
        "INSERT INTO tenant (id, name, created_at) VALUES ('t', 'T', 0);"
        "INSERT INTO station (id, tenant_id, name, created_at) VALUES ('s', 't', 'S', 0);"
        "INSERT INTO slot (id, station_id, key, label, position) VALUES ('a', 's', 'X', 'x', 2), ('b', 's', 'Y', 'y', 1);"
        "PRAGMA user_version = 3;")
    con.close()
    db = Database(p)
    rows = [tuple(r) for r in db.all("SELECT id, key, position FROM slot")]
    assert rows == [("b", "A", 1)] and db.scalar("PRAGMA user_version") == 5


def test_pinned_install_commands_for_self_signed_cert(env, tmp_path):
    """Selbst signiertes Zertifikat: Portal liefert Befehle mit angeheftetem Schlüssel (curl --pinnedpubkey)."""
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    from app.agent_bundle import AgentBundle
    from app.tlsinfo import TlsInfo

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "192.168.0.114")])
    now = datetime.datetime(2026, 1, 1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=10))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("192.168.0.114"))]), False)
            .sign(key, hashes.SHA256()))
    path = tmp_path / "server.crt"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    tls = TlsInfo.load(path)
    assert tls.pin.startswith("sha256//") and tls.self_signed and "192.168.0.114" in tls.names
    assert TlsInfo.load(tmp_path / "missing.crt") is None

    env.app.state.tls = tls
    env.app.state.agent_bundle = AgentBundle.build("http://testserver", pin=tls.pin)
    owner, sid = setup_station(env)
    r = owner.post(f"/api/v1/stations/{sid}/enrollments", {"name": "Pi"}).json()["install"]
    assert r["tls"]["pin"] == tls.pin and r["tls"]["fingerprint"] == tls.fingerprint
    assert f"--pinnedpubkey '{tls.pin}'" in r["commands"]["fetch_cert"]
    assert "--cacert bike-ca.crt" in r["commands"]["download"]
    assert r["commands"]["install"].endswith("--ca-file bike-ca.crt")
    assert f"--pinnedpubkey '{tls.pin}'" in r["commands"]["oneliner"]
    crt = env.client().get("/install/server.crt")
    assert crt.status_code == 200 and crt.content == path.read_bytes()
    assert f'PIN="{tls.pin}"'.encode() in env.client().get("/install/agent.sh").content


def test_no_cert_endpoint_without_tls(env):
    assert env.client().get("/install/server.crt").status_code == 404
