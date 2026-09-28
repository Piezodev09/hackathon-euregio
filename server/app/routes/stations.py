"""Stations, slots, device tokens, public display, events and telemetry ingestion."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..core import Ctx, client_ip, core_of, limit, require
from ..plans import get_plan
from ..schemas import DeviceIn, MeasurementBatchIn, MeasurementIn, SlotIn, SlotPatch, StationIn, StationPatch
from ..security import hash_token, new_id, new_token
from ..service import Monitoring, UnknownSlotError, iso

router = APIRouter(tags=["stations"])
read_limit = Depends(limit("read_limiter", "r:"))


def mon(request: Request) -> Monitoring:
    return request.app.state.monitoring


def _station(core, ctx: Ctx, station_id: str):
    st = core.db.one("SELECT * FROM station WHERE id = ? AND tenant_id = ?", (station_id[:64], ctx.tenant_id))
    if st is None:
        raise HTTPException(404, "not_found")  # also for other tenants: 404, not 403 (no existence oracle)
    return st


def _bump_config(core, station_id: str) -> None:
    """Gateways fetch the new slot list with their next heartbeat."""
    core.db.execute("UPDATE station SET config_version = config_version + 1 WHERE id = ?", (station_id,))


def _station_out(core, st, m: Monitoring | None = None) -> dict:
    out = {"id": st["id"], "name": st["name"], "location": st["location"], "alert_source": st["alert_source"],
           "display_enabled": bool(st["display_enabled"]), "display_configured": st["display_token_hash"] is not None,
           "auto_update": bool(st["auto_update"]), "config_version": st["config_version"],
           "created_at": iso(st["created_at"])}
    if m is not None:
        s = m.status(st)
        out["live"] = {"free_count": s["free_count"], "known_count": s["known_count"], "total": s["total"],
                       "alerts": sum(1 for x in s["slots"] if x["alert"]), "simulated_data": s["simulated_data"]}
    return out


# ---------------------------------------------------------------------- stations
@router.get("/api/v1/stations")
def list_stations(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM station WHERE tenant_id = ? ORDER BY created_at", (ctx.tenant_id,))
    return {"stations": [_station_out(core, r, mon(request)) for r in rows]}


@router.post("/api/v1/stations", status_code=201)
def create_station(body: StationIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    plan = get_plan(ctx.tenant["plan"])
    if core.tenant_usage(ctx.tenant_id)["stations"] >= plan.max_stations:
        raise HTTPException(409, {"code": "plan_limit", "limit": "max_stations", "value": plan.max_stations})
    if len(body.slots) > plan.max_slots_per_station:
        raise HTTPException(409, {"code": "plan_limit", "limit": "max_slots_per_station", "value": plan.max_slots_per_station})
    keys = [s.key for s in body.slots]
    if len(set(keys)) != len(keys):
        raise HTTPException(422, "duplicate_slot_key")
    sid = new_id("st")
    with core.db.tx() as c:
        c.execute("INSERT INTO station (id, tenant_id, name, location, created_at) VALUES (?,?,?,?,?)",
                  (sid, ctx.tenant_id, body.name, body.location, core.clock()))
        for i, s in enumerate(body.slots, start=1):
            c.execute("INSERT INTO slot (id, station_id, key, label, position) VALUES (?,?,?,?,?)",
                      (new_id("sl"), sid, s.key, s.label, i))
    core.audit("station_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=sid)
    return _station_out(core, core.db.one("SELECT * FROM station WHERE id = ?", (sid,)))


@router.get("/api/v1/stations/{station_id}")
def get_station(station_id: str, request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    out = _station_out(core, st)
    out["slots"] = [{"id": s["id"], "key": s["key"], "label": s["label"], "position": s["position"]}
                    for s in mon(request).slots(st["id"])]
    out["plan_allows_ml"] = get_plan(ctx.tenant["plan"]).ml_enabled
    return out


@router.patch("/api/v1/stations/{station_id}")
def patch_station(station_id: str, body: StationPatch, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    changes = body.model_dump(exclude_none=True)
    if changes.get("alert_source") == "ml" and not get_plan(ctx.tenant["plan"]).ml_enabled:
        raise HTTPException(402, {"code": "plan_feature", "feature": "ml"})
    if changes.get("display_enabled") and st["display_token_hash"] is None:
        raise HTTPException(409, "create_display_link_first")
    for k, v in changes.items():
        core.db.execute(f"UPDATE station SET {k} = ? WHERE id = ?", (int(v) if isinstance(v, bool) else v, st["id"]))
    core.audit("station_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=st["id"],
               detail=changes)
    return {"status": "ok"}


@router.delete("/api/v1/stations/{station_id}")
def delete_station(station_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    core.db.execute("DELETE FROM station WHERE id = ?", (st["id"],))
    core.audit("station_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=st["id"],
               detail={"name": st["name"]})
    return {"status": "deleted"}


# ---------------------------------------------------------------------- slots
@router.post("/api/v1/stations/{station_id}/slots", status_code=201)
def add_slot(station_id: str, body: SlotIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    plan = get_plan(ctx.tenant["plan"])
    count = core.db.scalar("SELECT COUNT(*) FROM slot WHERE station_id = ?", (st["id"],))
    if count >= plan.max_slots_per_station:
        raise HTTPException(409, {"code": "plan_limit", "limit": "max_slots_per_station", "value": plan.max_slots_per_station})
    if core.db.one("SELECT 1 FROM slot WHERE station_id = ? AND key = ?", (st["id"], body.key)):
        raise HTTPException(409, "duplicate_slot_key")
    pos = (core.db.scalar("SELECT MAX(position) FROM slot WHERE station_id = ?", (st["id"],)) or 0) + 1
    slot_id = new_id("sl")
    core.db.execute("INSERT INTO slot (id, station_id, key, label, position) VALUES (?,?,?,?,?)",
                    (slot_id, st["id"], body.key, body.label, pos))
    _bump_config(core, st["id"])
    core.audit("slot_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=slot_id)
    return {"id": slot_id, "key": body.key, "label": body.label, "position": pos}


def _slot(core, st, slot_id: str):
    sl = core.db.one("SELECT * FROM slot WHERE id = ? AND station_id = ?", (slot_id[:64], st["id"]))
    if sl is None:
        raise HTTPException(404, "not_found")
    return sl


@router.patch("/api/v1/stations/{station_id}/slots/{slot_id}")
def patch_slot(station_id: str, slot_id: str, body: SlotPatch, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    sl = _slot(core, _station(core, ctx, station_id), slot_id)
    if body.label is not None:
        core.db.execute("UPDATE slot SET label = ? WHERE id = ?", (body.label, sl["id"]))
    if body.position is not None:
        core.db.execute("UPDATE slot SET position = ? WHERE id = ?", (body.position, sl["id"]))
    _bump_config(core, sl["station_id"])
    return {"status": "ok"}


@router.delete("/api/v1/stations/{station_id}/slots/{slot_id}")
def delete_slot(station_id: str, slot_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    sl = _slot(core, _station(core, ctx, station_id), slot_id)
    core.db.execute("DELETE FROM slot WHERE id = ?", (sl["id"],))
    _bump_config(core, sl["station_id"])
    core.audit("slot_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=sl["id"])
    return {"status": "deleted"}


# ---------------------------------------------------------------------- live data
@router.get("/api/v1/stations/{station_id}/status")
def station_status(station_id: str, request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    return mon(request).status(_station(core, ctx, station_id))


@router.get("/api/v1/stations/{station_id}/occupancy")
def station_occupancy(station_id: str, request: Request, hours: int = Query(24, ge=1, le=168),
                      ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    return mon(request).occupancy_summary(_station(core, ctx, station_id), hours)


@router.get("/api/v1/events")
def list_events(request: Request, station_id: str | None = Query(None, max_length=64), limit: int = Query(100, ge=1, le=500),
                include_shadow: bool = False, open_only: bool = False, ctx: Ctx = Depends(require("viewer"))):
    return {"events": mon(request).events(ctx.tenant_id, station_id, limit, include_shadow, open_only)}


@router.post("/api/v1/events/{event_id}/ack")
def ack_event(event_id: str, request: Request, ctx: Ctx = Depends(require("operator"))):
    core = core_of(request)
    r = mon(request).acknowledge(ctx.tenant_id, event_id[:64], ctx.actor)
    if r is None:
        raise HTTPException(404, "not_found")
    core.audit("event_ack", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=event_id[:64])
    return {"acknowledged": True, "already_acknowledged": not r}


# ---------------------------------------------------------------------- devices (gateways)
@router.get("/api/v1/stations/{station_id}/devices")
def list_devices(station_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    rows = core.db.all("SELECT * FROM device WHERE station_id = ? ORDER BY revoked_at IS NOT NULL, created_at DESC", (st["id"],))
    from .agent import device_out

    latest = request.app.state.agent_bundle.version
    return {"devices": [device_out(core, r, latest) for r in rows], "latest_version": latest}


@router.post("/api/v1/stations/{station_id}/devices", status_code=201)
def create_device(station_id: str, body: DeviceIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    active = core.db.scalar("SELECT COUNT(*) FROM device WHERE station_id = ? AND revoked_at IS NULL", (st["id"],))
    if active >= 5:
        raise HTTPException(409, {"code": "plan_limit", "limit": "devices_per_station", "value": 5})
    token = new_token("bsd_")
    did = new_id("dev")
    core.db.execute(
        "INSERT INTO device (id, tenant_id, station_id, name, token_prefix, token_hash, created_at, created_by) VALUES (?,?,?,?,?,?,?,?)",
        (did, ctx.tenant_id, st["id"], body.name, token[:10], hash_token(token), core.clock(), ctx.actor))
    core.audit("device_token_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=did)
    # The token is shown exactly once and stored only as a hash.
    return {"id": did, "name": body.name, "token": token, "station_id": st["id"]}


@router.delete("/api/v1/stations/{station_id}/devices/{device_id}")
def revoke_device(station_id: str, device_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    cur = core.db.execute("UPDATE device SET revoked_at = ? WHERE id = ? AND station_id = ? AND revoked_at IS NULL",
                          (core.clock(), device_id[:64], st["id"]))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    core.audit("device_token_revoked", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=device_id[:64])
    return {"status": "revoked"}


@router.post("/api/v1/stations/{station_id}/display-link")
def rotate_display_link(station_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    """Creates (or replaces) the public, read-only display link for info screens."""
    core = core_of(request)
    st = _station(core, ctx, station_id)
    if not get_plan(ctx.tenant["plan"]).public_display:
        raise HTTPException(402, {"code": "plan_feature", "feature": "public_display"})
    token = new_token("bsp_")
    core.db.execute("UPDATE station SET display_token_hash = ?, display_enabled = 1 WHERE id = ?", (hash_token(token), st["id"]))
    core.audit("display_link_rotated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=st["id"])
    return {"url": f"{core.s.base_url}/display#{token}", "token": token}


# ---------------------------------------------------------------------- public display
@router.get("/api/v1/public/display/status", dependencies=[read_limit])
def public_status(request: Request):
    # Token in a header instead of the URL: never ends up in proxy/server logs.
    token = request.headers.get("x-display-token", "")
    core = core_of(request)
    if not token or len(token) > 200:
        raise HTTPException(404, "not_found")
    st = core.db.one(
        "SELECT st.* FROM station st JOIN tenant t ON t.id = st.tenant_id "
        "WHERE st.display_token_hash = ? AND st.display_enabled = 1 AND t.status = 'active'", (hash_token(token),))
    if st is None:
        raise HTTPException(404, "not_found")
    return mon(request).status(st, public=True)


# ---------------------------------------------------------------------- telemetry (gateway)
def require_device(request: Request):
    core = core_of(request)
    ip = client_ip(request)
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not core.device_limiter.allow(f"d:{token[:10] or ip}"):
        raise HTTPException(429, "rate_limited")
    dev = None
    if token and len(token) <= 200:
        h = hash_token(token)
        dev = core.db.one(
            "SELECT d.*, t.status AS tenant_status FROM device d JOIN tenant t ON t.id = d.tenant_id "
            "WHERE d.token_hash = ? AND d.revoked_at IS NULL", (h,))
        if dev is not None and dev["prev_token_hash"]:
            # The new token was used -> invalidate the old one immediately.
            core.db.execute("UPDATE device SET prev_token_hash = NULL, prev_token_valid_until = NULL WHERE id = ?", (dev["id"],))
        if dev is None:
            # grace period after rotation: the old token is still valid for a short time
            dev = core.db.one(
                "SELECT d.*, t.status AS tenant_status FROM device d JOIN tenant t ON t.id = d.tenant_id "
                "WHERE d.prev_token_hash = ? AND d.prev_token_valid_until > ? AND d.revoked_at IS NULL", (h, core.clock()))
    if dev is None:
        core.audit("rejected_device_write", ip=ip, detail={"path": request.url.path})
        raise HTTPException(401, "invalid_device_token", headers={"WWW-Authenticate": "Bearer"})
    if dev["tenant_status"] != "active":
        raise HTTPException(403, "tenant_suspended")
    now = core.clock()
    if dev["last_seen_at"] is None or now - dev["last_seen_at"] > 30 or dev["last_ip"] != ip:
        core.db.execute("UPDATE device SET last_seen_at = ?, last_ip = ? WHERE id = ?", (now, ip, dev["id"]))
    return dev


def _ingest(request: Request, dev, m: MeasurementIn):
    core = core_of(request)
    # A device token is only valid for its own station (tenant isolation).
    if m.station_id != dev["station_id"]:
        raise HTTPException(403, "station_mismatch")
    st = core.db.one("SELECT * FROM station WHERE id = ?", (dev["station_id"],))
    return mon(request).ingest(st, m)


@router.post("/api/v1/measurements", status_code=202)
def post_measurement(m: MeasurementIn, request: Request, dev=Depends(require_device)):
    try:
        r = _ingest(request, dev, m)
    except UnknownSlotError:
        raise HTTPException(422, "unknown_slot")
    return {"stored": r.stored, "duplicate": r.duplicate, "alert_created": r.alert_created}


@router.post("/api/v1/measurements/batch", status_code=202)
def post_batch(batch: MeasurementBatchIn, request: Request, dev=Depends(require_device)):
    stored = duplicate = rejected = 0
    for m in batch.measurements:
        if m.station_id != dev["station_id"]:
            rejected += 1
            continue
        try:
            r = _ingest(request, dev, m)
        except UnknownSlotError:
            rejected += 1
            continue
        stored += r.stored
        duplicate += r.duplicate
    return {"stored": stored, "duplicate": duplicate, "rejected": rejected}
