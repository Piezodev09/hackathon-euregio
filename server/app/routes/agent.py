"""Agent management: installation, pairing with one-time codes, heartbeat/configuration, token rotation, remote commands."""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field

from ..agent_bundle import version_tuple
from ..core import Ctx, client_ip, core_of, limit, require
from ..plans import get_plan
from ..schemas import Name, Strict
from ..security import hash_token, new_id, new_token
from ..service import iso
from .stations import _station, require_device

router = APIRouter(tags=["agent"])
enroll_limit = Depends(limit("auth_limiter", "enroll:"))

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # without 0/O, 1/I
CODE_LIFETIME_S = 30 * 60
HEARTBEAT_S = 60
ONLINE_WITHIN_S = 3 * HEARTBEAT_S
PREV_TOKEN_GRACE_S = 15 * 60
MAX_DEVICES_PER_STATION = 5
COMMANDS = ("restart", "rotate_token", "update")


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))  # ~50 bit
    return f"{raw[:5]}-{raw[5:]}"


def normalize_code(code: str) -> str:
    c = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{c[:5]}-{c[5:]}" if len(c) == 10 else c


def bundle(request: Request):
    return request.app.state.agent_bundle


def install_info(request: Request) -> dict:
    core = core_of(request)
    b = bundle(request)
    url = f"{core.s.base_url}/install/agent.sh"
    fingerprint = request.app.state.ca_fingerprint
    # With an own CA the Pi does not trust the platform yet: the script is fetched without TLS
    # verification, but the checksum below (shown over the admin's HTTPS session) pins it, and the
    # script itself embeds the CA for every further connection.
    download = f"curl -fsSLk -o agent.sh {url}" if fingerprint else f"curl -fsSLO {url}"
    return {
        "version": b.version,
        "script_url": url,
        "script_sha256": b.script_sha256,
        "bundle_sha256": b.sha256,
        "ca_fingerprint": fingerprint,
        "ca_url": f"{core.s.base_url}/install/ca.crt" if fingerprint else None,
        "platform_url": core.s.base_url,
        "commands": {
            "download": download,
            "verify": f"echo '{b.script_sha256}  agent.sh' | sha256sum -c -",
        },
    }


def device_out(core, d, latest: str) -> dict:
    health = json.loads(d["health"]) if d["health"] else {}
    now = core.clock()
    online = d["last_heartbeat_at"] is not None and now - d["last_heartbeat_at"] <= ONLINE_WITHIN_S
    return {
        "id": d["id"], "name": d["name"], "station_id": d["station_id"], "token_prefix": d["token_prefix"],
        "created_at": iso(d["created_at"]), "created_by": d["created_by"], "revoked_at": iso(d["revoked_at"]),
        "last_seen_at": iso(d["last_seen_at"]), "last_ip": d["last_ip"], "enrolled_at": iso(d["enrolled_at"]),
        "hostname": d["hostname"], "agent_version": d["agent_version"], "os_info": d["os_info"], "source": d["source"],
        "last_heartbeat_at": iso(d["last_heartbeat_at"]), "online": online, "health": health,
        "pending_command": d["pending_command"], "update_requested": bool(d["update_requested"]),
        "update_available": bool(d["agent_version"]) and version_tuple(latest) > version_tuple(d["agent_version"]),
        "managed": d["enrolled_at"] is not None, "token_rotated_at": iso(d["token_rotated_at"]),
    }


# ---------------------------------------------------------------------- downloads
@router.get("/install/agent.sh", include_in_schema=False)
def install_script(request: Request):
    b = bundle(request)
    return Response(b.script, media_type="text/x-shellscript; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="agent.sh"', "X-Content-SHA256": b.script_sha256,
                             "Cache-Control": "no-store"})


@router.get("/install/agent.tar.gz", include_in_schema=False)
def install_bundle(request: Request):
    b = bundle(request)
    return Response(b.data, media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="bike-agent-{b.version}.tar.gz"',
                             "X-Content-SHA256": b.sha256, "X-Agent-Version": b.version})


@router.get("/install/ca.crt", include_in_schema=False)
def install_ca(request: Request):
    """CA certificate of a self-hosted platform (public data). Verify its fingerprint before trusting it."""
    pem = request.app.state.ca_pem
    if not pem:
        raise HTTPException(404, "not_found")
    return Response(pem, media_type="application/x-pem-file",
                    headers={"Content-Disposition": 'attachment; filename="bike-station-ca.crt"',
                             "X-Certificate-SHA256": request.app.state.ca_fingerprint.replace(":", "").lower()})


@router.get("/install/agent.sha256", include_in_schema=False)
def install_sha(request: Request):
    b = bundle(request)
    return Response(f"{b.sha256}  bike-agent-{b.version}.tar.gz\n{b.script_sha256}  agent.sh\n", media_type="text/plain")


# ---------------------------------------------------------------------- portal: pairing codes
class EnrollmentIn(Strict):
    name: Name = "Pi-Gateway"


@router.post("/api/v1/stations/{station_id}/enrollments", status_code=201)
def create_enrollment(station_id: str, body: EnrollmentIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    active = core.db.scalar("SELECT COUNT(*) FROM device WHERE station_id = ? AND revoked_at IS NULL", (st["id"],))
    if active >= MAX_DEVICES_PER_STATION:
        raise HTTPException(409, {"code": "plan_limit", "limit": "devices_per_station", "value": MAX_DEVICES_PER_STATION})
    code = new_code()
    eid = new_id("enr")
    now = core.clock()
    core.db.execute(
        "INSERT INTO enrollment (id, tenant_id, station_id, code_hash, name, created_by, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (eid, ctx.tenant_id, st["id"], hash_token(code), body.name, ctx.actor, now, now + CODE_LIFETIME_S))
    core.audit("enrollment_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=st["id"])
    info = install_info(request)
    info["commands"]["install"] = f"sudo sh agent.sh --code {code}"
    if info["ca_fingerprint"]:
        info["commands"]["oneliner"] = None  # an unverified pipe into sh would defeat the pinning
    else:
        info["commands"]["oneliner"] = f"curl -fsSL {info['script_url']} | sudo sh -s -- --code {code}"
    return {"id": eid, "code": code, "expires_at": iso(now + CODE_LIFETIME_S), "install": info}


@router.get("/api/v1/stations/{station_id}/enrollments")
def list_enrollments(station_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    rows = core.db.all("SELECT * FROM enrollment WHERE station_id = ? AND used_at IS NULL AND expires_at > ? ORDER BY created_at DESC",
                       (st["id"], core.clock()))
    return {"enrollments": [{"id": r["id"], "name": r["name"], "created_by": r["created_by"], "expires_at": iso(r["expires_at"])}
                            for r in rows]}


@router.delete("/api/v1/stations/{station_id}/enrollments/{enrollment_id}")
def revoke_enrollment(station_id: str, enrollment_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    cur = core.db.execute("DELETE FROM enrollment WHERE id = ? AND station_id = ? AND used_at IS NULL", (enrollment_id[:64], st["id"]))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    return {"status": "revoked"}


@router.get("/api/v1/agent/install-info")
def get_install_info(request: Request, ctx: Ctx = Depends(require("viewer"))):
    return install_info(request)


# ---------------------------------------------------------------------- portal: fleet & commands
@router.get("/api/v1/devices")
def fleet(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    latest = bundle(request).version
    rows = core.db.all(
        "SELECT d.*, s.name AS station_name FROM device d JOIN station s ON s.id = d.station_id "
        "WHERE d.tenant_id = ? ORDER BY d.revoked_at IS NOT NULL, s.name, d.created_at", (ctx.tenant_id,))
    out = []
    for r in rows:
        d = device_out(core, r, latest)
        d["station_name"] = r["station_name"]
        out.append(d)
    return {"devices": out, "latest_version": latest}


class CommandIn(Strict):
    command: str = Field(pattern="^(restart|rotate_token|update)$")


@router.post("/api/v1/stations/{station_id}/devices/{device_id}/command")
def device_command(station_id: str, device_id: str, body: CommandIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    d = core.db.one("SELECT * FROM device WHERE id = ? AND station_id = ? AND revoked_at IS NULL", (device_id[:64], st["id"]))
    if d is None:
        raise HTTPException(404, "not_found")
    if d["enrolled_at"] is None:
        raise HTTPException(409, "device_not_managed")
    if body.command == "update":
        core.db.execute("UPDATE device SET update_requested = 1 WHERE id = ?", (d["id"],))
    else:
        core.db.execute("UPDATE device SET pending_command = ? WHERE id = ?", (body.command, d["id"]))
    core.audit("device_command", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=d["id"],
               detail={"command": body.command})
    return {"status": "queued"}


# ---------------------------------------------------------------------- agent endpoints
class EnrollIn(Strict):
    code: str = Field(min_length=10, max_length=20)
    hostname: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9._-]*$")
    name: str = Field(default="", max_length=100)
    agent_version: str = Field(default="", max_length=20, pattern=r"^[0-9.]*$")
    os_info: str = Field(default="", max_length=120)
    source: str = Field(default="serial", pattern="^(serial|simulator|stdin)$")


@router.post("/api/v1/agent/enroll", dependencies=[enroll_limit])
def enroll(body: EnrollIn, request: Request):
    core = core_of(request)
    ip = client_ip(request)
    h = hash_token(normalize_code(body.code))
    now = core.clock()
    with core.db.tx() as c:
        e = c.execute(
            "SELECT e.*, t.status AS tenant_status FROM enrollment e JOIN tenant t ON t.id = e.tenant_id "
            "WHERE e.code_hash = ? AND e.used_at IS NULL AND e.expires_at > ?", (h, now)).fetchone()
        if e is None or e["tenant_status"] != "active":
            core.audit("enroll_failed", ip=ip)
            raise HTTPException(400, "invalid_or_expired_code")
        token = new_token("bsd_")
        did = new_id("dev")
        name = (body.name or body.hostname or e["name"])[:100]
        c.execute(
            "INSERT INTO device (id, tenant_id, station_id, name, token_prefix, token_hash, created_at, created_by, "
            "enrolled_at, hostname, agent_version, os_info, source, last_ip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, e["tenant_id"], e["station_id"], name, token[:10], hash_token(token), now, e["created_by"], now,
             body.hostname or None, body.agent_version or None, body.os_info or None, body.source, ip))
        c.execute("UPDATE enrollment SET used_at = ?, device_id = ? WHERE id = ?", (now, did, e["id"]))
    core.audit("device_enrolled", tenant_id=e["tenant_id"], actor=e["created_by"], ip=ip, target=did,
               detail={"hostname": body.hostname, "version": body.agent_version})
    st = core.db.one("SELECT * FROM station WHERE id = ?", (e["station_id"],))
    return {"device_id": did, "token": token, "station_id": st["id"], "station_name": st["name"],
            "api_url": core.s.base_url, **_agent_config(core, st)}


def _agent_config(core, st) -> dict:
    slots = core.db.all("SELECT key FROM slot WHERE station_id = ? ORDER BY position, key", (st["id"],))
    return {"config_version": st["config_version"], "slot_map": {s["key"]: s["key"] for s in slots},
            "heartbeat_s": HEARTBEAT_S}


class HeartbeatIn(Strict):
    agent_version: str = Field(default="", max_length=20, pattern=r"^[0-9.]*$")
    hostname: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9._-]*$")
    os_info: str = Field(default="", max_length=120)
    source: str = Field(default="serial", pattern="^(serial|simulator|stdin)$")
    uptime_s: int = Field(default=0, ge=0, le=10**9)
    serial_connected: bool | None = None
    buffer_len: int = Field(default=0, ge=0, le=10**6)
    api_online: bool | None = None
    cpu_temp_c: float | None = Field(default=None, ge=-50, le=150)
    load_1m: float | None = Field(default=None, ge=0, le=1000)
    disk_free_mb: int | None = Field(default=None, ge=0, le=10**8)
    last_error: str = Field(default="", max_length=300)
    config_version: int = Field(default=0, ge=0)
    self_update: bool = True                # False: updates come with the container / add-on image
    mqtt_connected: bool | None = None      # None: local MQTT not configured


@router.post("/api/v1/agent/heartbeat")
def heartbeat(body: HeartbeatIn, request: Request, dev=Depends(require_device)):
    core = core_of(request)
    now = core.clock()
    health = body.model_dump(exclude={"agent_version", "hostname", "os_info", "source", "config_version"})
    commands = []
    if dev["pending_command"]:
        commands.append(dev["pending_command"])
    st = core.db.one("SELECT * FROM station WHERE id = ?", (dev["station_id"],))
    latest = bundle(request)
    update = None
    newer = version_tuple(latest.version) > version_tuple(body.agent_version)
    if newer and (st["auto_update"] or dev["update_requested"]):
        update = {"version": latest.version, "sha256": latest.sha256, "url": f"{core.s.base_url}/install/agent.tar.gz"}
    core.db.execute(
        "UPDATE device SET last_heartbeat_at = ?, health = ?, agent_version = ?, hostname = COALESCE(NULLIF(?, ''), hostname), "
        "os_info = COALESCE(NULLIF(?, ''), os_info), source = ?, pending_command = NULL, "
        "update_requested = CASE WHEN ? THEN 0 ELSE update_requested END WHERE id = ?",
        (now, json.dumps(health), body.agent_version or None, body.hostname, body.os_info, body.source,
         int(not newer), dev["id"]))
    return {"station_id": st["id"], **_agent_config(core, st), "commands": commands, "update": update,
            "latest_version": latest.version, "server_time": iso(now)}


@router.post("/api/v1/agent/rotate-token")
def rotate_token(request: Request, dev=Depends(require_device)):
    """Issue a new token. The old one stays valid briefly in case the response gets lost."""
    core = core_of(request)
    now = core.clock()
    token = new_token("bsd_")
    core.db.execute(
        "UPDATE device SET prev_token_hash = token_hash, prev_token_valid_until = ?, token_hash = ?, token_prefix = ?, "
        "token_rotated_at = ? WHERE id = ?", (now + PREV_TOKEN_GRACE_S, hash_token(token), token[:10], now, dev["id"]))
    core.audit("device_token_rotated", tenant_id=dev["tenant_id"], actor="agent", ip=client_ip(request), target=dev["id"])
    return {"token": token}
