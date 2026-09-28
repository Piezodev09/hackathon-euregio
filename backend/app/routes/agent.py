"""Agent-Verwaltung: Installation, Kopplung per Einmal-Code, Heartbeat/Konfiguration, Token-Rotation, Fernbefehle."""

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

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne 0/O, 1/I
CODE_LIFETIME_S = 30 * 60
HEARTBEAT_S = 60
ONLINE_WITHIN_S = 3 * HEARTBEAT_S
PREV_TOKEN_GRACE_S = 15 * 60
MAX_DEVICES_PER_STATION = 5
COMMANDS = ("restart", "rotate_token", "update", "snapshot", "identify")
MAX_STALLS_PER_GATEWAY = 16


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))  # ~50 Bit
    return f"{raw[:5]}-{raw[5:]}"


def normalize_code(code: str) -> str:
    c = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{c[:5]}-{c[5:]}" if len(c) == 10 else c


def bundle(request: Request):
    return request.app.state.agent_bundle


def install_info(request: Request) -> dict:
    """Befehle für die Einrichtung. Bei eigenem (selbst signiertem) Zertifikat wird dessen Schlüssel angeheftet:
    `curl --pinnedpubkey` lädt das Zertifikat nur, wenn der Schlüssel exakt passt; danach prüft curl normal."""
    core = core_of(request)
    b = bundle(request)
    base = core.s.base_url
    url = f"{base}/install/agent.sh"
    tls = getattr(request.app.state, "tls", None)
    info = {
        "version": b.version,
        "script_url": url,
        "script_sha256": b.script_sha256,
        "bundle_sha256": b.sha256,
        "tls": None,
        "commands": {
            "download": f"curl -fsSLO {url}",
            "verify": f"echo '{b.script_sha256}  agent.sh' | sha256sum -c -",
        },
        "ca_arg": "",
    }
    if tls is not None:
        info["tls"] = {"pin": tls.pin, "fingerprint": tls.fingerprint, "names": list(tls.names), "not_after": tls.not_after,
                       "self_signed": tls.self_signed}
        info["commands"] = {
            "fetch_cert": f"curl -fsSk --pinnedpubkey '{tls.pin}' -o bike-ca.crt {base}/install/server.crt",
            "download": f"curl -fsSLO --cacert bike-ca.crt {url}",
            "verify": f"echo '{b.script_sha256}  agent.sh' | sha256sum -c -",
        }
        info["ca_arg"] = " --ca-file bike-ca.crt"
    return info


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
        "gateway_id": d["gateway_id"] or d["id"], "hw": json.loads(d["hw"]) if d["hw"] else None,
        "assigned_port": d["port"], "assigned_reader": d["reader"],
    }


# ---------------------------------------------------------------------- Auslieferung
@router.get("/install/agent.sh", include_in_schema=False)
def install_script(request: Request):
    b = bundle(request)
    return Response(b.script, media_type="text/x-shellscript; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="agent.sh"', "X-Content-SHA256": b.script_sha256,
                             "Cache-Control": "no-store"})


@router.get("/install/server.crt", include_in_schema=False)
def install_cert(request: Request):
    """Öffentliches Zertifikat der Plattform (kein Geheimnis). Wird per Pin geprüft abgeholt."""
    tls = getattr(request.app.state, "tls", None)
    if tls is None:
        raise HTTPException(404, "not_found")
    return Response(tls.pem, media_type="application/x-pem-file",
                    headers={"Content-Disposition": 'attachment; filename="bike-ca.crt"', "Cache-Control": "no-store"})


@router.get("/install/agent.tar.gz", include_in_schema=False)
def install_bundle(request: Request):
    b = bundle(request)
    return Response(b.data, media_type="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="bike-agent-{b.version}.tar.gz"',
                             "X-Content-SHA256": b.sha256, "X-Agent-Version": b.version})


@router.get("/install/agent.sha256", include_in_schema=False)
def install_sha(request: Request):
    b = bundle(request)
    return Response(f"{b.sha256}  bike-agent-{b.version}.tar.gz\n{b.script_sha256}  agent.sh\n", media_type="text/plain")


# ---------------------------------------------------------------------- Portal: Kopplungscodes
class EnrollmentIn(Strict):
    name: Name = "Pi-Gateway"


class MultiEnrollmentIn(Strict):
    name: Name = "Pi-Gateway"
    station_ids: list[str] = Field(min_length=1, max_length=MAX_STALLS_PER_GATEWAY)


@router.post("/api/v1/stations/{station_id}/enrollments", status_code=201)
def create_enrollment(station_id: str, body: EnrollmentIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    return _enrollment(request, ctx, [station_id], body.name)


@router.post("/api/v1/stations/enrollments", status_code=201)
def create_multi_enrollment(body: MultiEnrollmentIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    """Ein Kopplungscode für einen Pi mit mehreren Stellplätzen (je Stellplatz ein Arduino/Leser am selben Pi)."""
    if len(set(body.station_ids)) != len(body.station_ids):
        raise HTTPException(422, "duplicate_station")
    return _enrollment(request, ctx, body.station_ids, body.name)


def _enrollment(request: Request, ctx: Ctx, station_ids: list[str], name: str) -> dict:
    core = core_of(request)
    stations = [_station(core, ctx, sid) for sid in station_ids]
    for st in stations:
        active = core.db.scalar("SELECT COUNT(*) FROM device WHERE station_id = ? AND revoked_at IS NULL", (st["id"],))
        if active >= MAX_DEVICES_PER_STATION:
            raise HTTPException(409, {"code": "plan_limit", "limit": "devices_per_station", "value": MAX_DEVICES_PER_STATION})
    code = new_code()
    eid = new_id("enr")
    now = core.clock()
    ids = [st["id"] for st in stations]
    core.db.execute(
        "INSERT INTO enrollment (id, tenant_id, station_id, code_hash, name, created_by, created_at, expires_at, station_ids) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (eid, ctx.tenant_id, ids[0], hash_token(code), name, ctx.actor, now, now + CODE_LIFETIME_S,
         json.dumps(ids) if len(ids) > 1 else None))
    core.audit("enrollment_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=ids[0],
               detail={"stations": ids} if len(ids) > 1 else None)
    info = install_info(request)
    info["commands"]["install"] = f"sudo sh agent.sh --code {code}{info['ca_arg']}"
    if info["tls"]:
        info["commands"]["oneliner"] = (f"curl -fsSLk --pinnedpubkey '{info['tls']['pin']}' {info['script_url']}"
                                        f" | sudo sh -s -- --code {code}")
    else:
        info["commands"]["oneliner"] = f"curl -fsSL {info['script_url']} | sudo sh -s -- --code {code}"
    return {"id": eid, "code": code, "expires_at": iso(now + CODE_LIFETIME_S), "install": info, "station_ids": ids}


@router.get("/api/v1/stations/{station_id}/enrollments")
def list_enrollments(station_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    rows = core.db.all("SELECT * FROM enrollment WHERE tenant_id = ? AND used_at IS NULL AND expires_at > ? "
                       "AND (station_id = ? OR station_ids LIKE ?) ORDER BY created_at DESC",
                       (ctx.tenant_id, core.clock(), st["id"], f'%"{st["id"]}"%'))
    return {"enrollments": [{"id": r["id"], "name": r["name"], "created_by": r["created_by"], "expires_at": iso(r["expires_at"])}
                            for r in rows]}


@router.delete("/api/v1/stations/{station_id}/enrollments/{enrollment_id}")
def revoke_enrollment(station_id: str, enrollment_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    cur = core.db.execute("DELETE FROM enrollment WHERE id = ? AND tenant_id = ? AND (station_id = ? OR station_ids LIKE ?) "
                          "AND used_at IS NULL", (enrollment_id[:64], ctx.tenant_id, st["id"], f'%"{st["id"]}"%'))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    return {"status": "revoked"}


@router.get("/api/v1/agent/install-info")
def get_install_info(request: Request, ctx: Ctx = Depends(require("viewer"))):
    return install_info(request)


# ---------------------------------------------------------------------- Portal: Flotte & Befehle
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
    command: str = Field(pattern="^(restart|rotate_token|update|identify)$")


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


class AssignIn(Strict):
    port: str | None = Field(default=None, max_length=160, pattern=r"^[A-Za-z0-9 _.:/@()+,#-]*$")
    reader: str | None = Field(default=None, max_length=160, pattern=r"^[A-Za-z0-9 _.:/@()+,#-]*$")


@router.put("/api/v1/devices/{device_id}/assign")
def assign_hardware(device_id: str, body: AssignIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    """Port/Leser eines Pi einem Stellplatz fest zuordnen (leer = automatisch). Hat ein anderer Stellplatz desselben
    Pi diesen Port/Leser, werden die beiden getauscht, damit nie zwei Stellplätze dieselbe Hardware lesen."""
    core = core_of(request)
    d = core.db.one("SELECT * FROM device WHERE id = ? AND tenant_id = ? AND revoked_at IS NULL", (device_id[:64], ctx.tenant_id))
    if d is None:
        raise HTTPException(404, "not_found")
    gw = d["gateway_id"] or d["id"]
    hw = json.loads(d["hw"]) if d["hw"] else {}
    with core.db.tx() as c:
        for col, value, current in (("port", body.port, hw.get("port")), ("reader", body.reader, hw.get("reader"))):
            if value is None:
                continue
            value = value or None
            other = c.execute(f"SELECT id, hw FROM device WHERE gateway_id = ? AND id != ? AND revoked_at IS NULL "  # noqa: S608
                              f"AND ({col} = ? OR json_extract(hw, '$.{col}') = ?)", (gw, d["id"], value, value)).fetchone() if value else None
            if other is not None:
                c.execute(f"UPDATE device SET {col} = ? WHERE id = ?", (d[col] or current or None, other["id"]))  # noqa: S608
            c.execute(f"UPDATE device SET {col} = ? WHERE id = ?", (value, d["id"]))  # noqa: S608
    core.audit("device_assigned", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=d["id"],
               detail=body.model_dump(exclude_none=True))
    return device_out(core, core.db.one("SELECT * FROM device WHERE id = ?", (d["id"],)), bundle(request).version)


@router.get("/api/v1/readers")
def readers(request: Request, ctx: Ctx = Depends(require("admin"))):
    """Alle NFC-Leser je Pi (aus den Heartbeats) mit Stellplatz-Zuordnung und Taps der letzten 7 Tage."""
    core = core_of(request)
    since = core.clock() - 7 * 86400
    devs = core.db.all("SELECT d.*, s.name AS station_name FROM device d JOIN station s ON s.id = d.station_id "
                       "WHERE d.tenant_id = ? AND d.revoked_at IS NULL ORDER BY d.created_at", (ctx.tenant_id,))
    stats = {(r["device_id"], r["reader"]): r for r in core.db.all(
        "SELECT device_id, reader, COUNT(*) AS taps, SUM(result IN ('checked_in','checked_out','learned')) AS ok, MAX(at) AS last_at "
        "FROM nfc_tap WHERE tenant_id = ? AND at >= ? AND result != 'duplicate' GROUP BY device_id, reader", (ctx.tenant_id, since))}
    gateways: dict[str, dict] = {}
    for d in devs:
        hw = json.loads(d["hw"]) if d["hw"] else {}
        gid = d["gateway_id"] or d["id"]
        g = gateways.setdefault(gid, {"gateway_id": gid, "hostname": d["hostname"], "online": False, "readers": {}, "stalls": []})
        g["online"] = g["online"] or bool(d["last_heartbeat_at"] and core.clock() - d["last_heartbeat_at"] <= ONLINE_WITHIN_S)
        g["stalls"].append({"device_id": d["id"], "station_id": d["station_id"], "station_name": d["station_name"],
                            "reader": hw.get("reader") or None, "assigned_reader": d["reader"]})
        for r in hw.get("readers", []):
            g["readers"].setdefault(r["id"], {**r, "station_id": None, "station_name": None, "device_id": None,
                                              "taps": 0, "ok": 0, "last_at": None})
        if hw.get("reader"):
            rd = g["readers"].setdefault(hw["reader"], {"id": hw["reader"], "kind": hw["reader"].split(":")[0].split("@")[0],
                                                        "name": hw["reader"], "taps": 0, "ok": 0, "last_at": None})
            rd.update({"station_id": d["station_id"], "station_name": d["station_name"], "device_id": d["id"]})
        for (dev_id, reader), st in stats.items():
            if dev_id != d["id"]:
                continue
            key = reader or hw.get("reader") or "pn532"
            if key.startswith("pn532"):  # Leser am Arduino: je Stellplatz ein eigener
                key = hw["reader"] if (hw.get("reader") or "").startswith("pn532") else f"pn532@{d['id']}"
            rd = g["readers"].setdefault(key, {"id": key, "kind": key.split(":")[0].split("@")[0],
                                               "name": f"PN532 – {d['station_name']}" if key.startswith("pn532") else key,
                                               "station_id": d["station_id"], "station_name": d["station_name"], "device_id": d["id"],
                                               "taps": 0, "ok": 0, "last_at": None})
            rd["taps"] += st["taps"]
            rd["ok"] += st["ok"] or 0
            rd["last_at"] = max(rd["last_at"] or 0, st["last_at"])
    out = []
    for g in gateways.values():
        rl = []
        for r in g["readers"].values():
            rl.append({**r, "last_at": iso(r["last_at"]) if r["last_at"] else None,
                       "success": round(r["ok"] / r["taps"], 3) if r["taps"] else None})
        out.append({**g, "readers": rl})
    return {"gateways": out, "learn": request.app.state.parking.learn_status(ctx.tenant_id)}


# ---------------------------------------------------------------------- Agent-Endpunkte
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
        ids = json.loads(e["station_ids"]) if e["station_ids"] else [e["station_id"]]
        stations = [c.execute("SELECT * FROM station WHERE id = ? AND tenant_id = ?", (sid, e["tenant_id"])).fetchone() for sid in ids]
        stations = [st for st in stations if st is not None]  # inzwischen gelöschte Stellplätze überspringen
        if not stations:
            raise HTTPException(400, "invalid_or_expired_code")
        gateway_id = new_id("gw")
        base_name = (body.name or body.hostname or e["name"])[:100]
        stalls = []
        for st in stations:
            token = new_token("bsd_")
            did = new_id("dev")
            name = base_name if len(stations) == 1 else f"{base_name} · {st['name']}"[:100]
            c.execute(
                "INSERT INTO device (id, tenant_id, station_id, name, token_prefix, token_hash, created_at, created_by, "
                "enrolled_at, hostname, agent_version, os_info, source, last_ip, gateway_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, e["tenant_id"], st["id"], name, token[:10], hash_token(token), now, e["created_by"], now,
                 body.hostname or None, body.agent_version or None, body.os_info or None, body.source, ip, gateway_id))
            stalls.append({"station_id": st["id"], "station_name": st["name"], "device_id": did, "token": token,
                           **_agent_config(core, st)})
        c.execute("UPDATE enrollment SET used_at = ?, device_id = ? WHERE id = ?", (now, stalls[0]["device_id"], e["id"]))
    core.audit("device_enrolled", tenant_id=e["tenant_id"], actor=e["created_by"], ip=ip, target=stalls[0]["device_id"],
               detail={"hostname": body.hostname, "version": body.agent_version, "stalls": len(stalls)})
    first = stalls[0]
    # Einzelfelder bleiben für ältere Agents (<1.4) erhalten; neue Agents lesen "stalls".
    return {"device_id": first["device_id"], "token": first["token"], "station_id": first["station_id"],
            "station_name": first["station_name"], "api_url": core.s.base_url, "gateway_id": gateway_id, "stalls": stalls,
            **_agent_config(core, stations[0])}


def _agent_config(core, st) -> dict:
    return {"config_version": st["config_version"], "heartbeat_s": HEARTBEAT_S}


HwText = Field(default="", max_length=160, pattern=r"^[A-Za-z0-9 _.:/@()+,#-]*$")


class HwPort(Strict):
    path: str = HwText  # z. B. /dev/serial/by-id/usb-Arduino_Uno_...
    kind: str = Field(default="", max_length=40, pattern=r"^[a-z0-9_-]*$")  # arduino | ch340 | ftdi | cp210x | acm | usb-serial
    firmware: str = HwText  # aus der hello-Zeile des Sketches, sonst leer


class HwReader(Strict):
    id: str = HwText  # stabil: pn532@<port> | hid:<by-id> | pcsc:<name>
    kind: str = Field(default="", max_length=20, pattern=r"^(pn532|hid|pcsc)?$")
    name: str = HwText


class HwIn(Strict):
    ports: list[HwPort] = Field(default_factory=list, max_length=32)
    readers: list[HwReader] = Field(default_factory=list, max_length=32)
    port: str = HwText  # von diesem Stellplatz genutzter Port
    reader: str = HwText  # von diesem Stellplatz genutzter Leser
    camera: str = Field(default="", max_length=20, pattern=r"^[a-z-]*$")
    kiosk: bool | None = None
    stalls: int = Field(default=1, ge=1, le=MAX_STALLS_PER_GATEWAY)


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
    camera: str = Field(default="", max_length=20, pattern=r"^[a-z-]*$")
    hw: HwIn | None = None


@router.get("/api/v1/agent/whoami")
def whoami(request: Request, dev=Depends(require_device)):
    """Nebenwirkungsfreie Prüfung des Geräte-Tokens (für `agent.py doctor`)."""
    core = core_of(request)
    st = core.db.one("SELECT id, name FROM station WHERE id = ?", (dev["station_id"],))
    return {"device_id": dev["id"], "station_id": st["id"], "station_name": st["name"], "server_time": core.clock()}


@router.get("/api/v1/agent/status")
def agent_status(request: Request, dev=Depends(require_device)):
    """Status des eigenen Stellplatzes für die lokale Anzeige am Pi (wie die öffentliche Kiosk-Anzeige)."""
    core = core_of(request)
    st = core.db.one("SELECT * FROM station WHERE id = ?", (dev["station_id"],))
    return request.app.state.monitoring.status(st, public=True)


@router.post("/api/v1/agent/heartbeat")
def heartbeat(body: HeartbeatIn, request: Request, dev=Depends(require_device)):
    core = core_of(request)
    now = core.clock()
    health = body.model_dump(exclude={"agent_version", "hostname", "os_info", "source", "config_version", "hw"})
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
        "update_requested = CASE WHEN ? THEN 0 ELSE update_requested END, hw = COALESCE(?, hw) WHERE id = ?",
        (now, json.dumps(health), body.agent_version or None, body.hostname, body.os_info, body.source,
         int(not newer), json.dumps(body.hw.model_dump()) if body.hw else None, dev["id"]))
    # Zuordnung aus dem Portal (None = automatisch durch den Agent)
    return {"station_id": st["id"], **_agent_config(core, st), "commands": commands, "update": update,
            "latest_version": latest.version, "server_time": iso(now),
            "assign": {"port": dev["port"], "reader": dev["reader"]}}


@router.post("/api/v1/agent/rotate-token")
def rotate_token(request: Request, dev=Depends(require_device)):
    """Neues Token ausstellen. Das alte bleibt kurz gültig, falls die Antwort unterwegs verloren geht."""
    core = core_of(request)
    now = core.clock()
    token = new_token("bsd_")
    core.db.execute(
        "UPDATE device SET prev_token_hash = token_hash, prev_token_valid_until = ?, token_hash = ?, token_prefix = ?, "
        "token_rotated_at = ? WHERE id = ?", (now + PREV_TOKEN_GRACE_S, hash_token(token), token[:10], now, dev["id"]))
    core.audit("device_token_rotated", tenant_id=dev["tenant_id"], actor="agent", ip=client_ip(request), target=dev["id"])
    return {"token": token}
