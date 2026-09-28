"""Betrieb: Reservierungen, Öffnungszeiten und Sperrzeiten."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from ..billing import TZ, iso
from ..core import Ctx, core_of, require
from ..hours import normalize_hours
from ..plans import get_plan
from ..reservations import ReservationError, Reservations
from ..schemas import ClosureIn, HoursIn, ReservationIn
from ..security import new_id
from .stations import _station

router = APIRouter(tags=["ops"])


def res(request: Request) -> Reservations:
    return request.app.state.reservations


def create_reservation(request: Request, tenant_id: str, st, body: ReservationIn, actor: str, via: str) -> dict:
    """Gemeinsam für Portal und API-Schlüssel."""
    if not get_plan(core_of(request).db.scalar("SELECT plan FROM tenant WHERE id = ?", (tenant_id,))).reservations:
        raise HTTPException(402, {"code": "plan_feature", "feature": "reservations"})
    presence = request.app.state.monitoring.status(st)["presence"]
    r = res(request)
    try:
        row = r.create(st, body.minutes, card_id=body.card_id, label=body.label, actor=actor, via=via, presence=presence,
                       source="simulated" if st["demo_sim"] else "live")
    except ReservationError as exc:
        raise HTTPException(409 if str(exc) not in ("invalid_minutes", "card_not_found") else 422, str(exc)) from None
    return r.out(row)


# ---------------------------------------------------------------------- Reservierungen
@router.get("/api/v1/reservations")
def list_reservations(request: Request, station_id: str | None = None, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    res(request).expire()
    sql, params = "SELECT * FROM reservation WHERE tenant_id = ?", [ctx.tenant_id]
    if station_id:
        sql += " AND station_id = ?"
        params.append(station_id[:64])
    sql += " ORDER BY status = 'active' DESC, created_at DESC LIMIT 100"
    names = {r["id"]: r["name"] for r in core.db.all("SELECT id, name FROM station WHERE tenant_id = ?", (ctx.tenant_id,))}
    return {"reservations": [{**res(request).out(r), "station_name": names.get(r["station_id"])} for r in core.db.all(sql, tuple(params))]}


@router.post("/api/v1/stations/{station_id}/reservations", status_code=201)
def reserve(station_id: str, body: ReservationIn, request: Request, ctx: Ctx = Depends(require("operator"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    out = create_reservation(request, ctx.tenant_id, st, body, ctx.actor, "portal")
    core.audit("reservation_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=out["id"],
               detail={"minutes": body.minutes, "for_card": body.card_id is not None})
    return out


@router.delete("/api/v1/reservations/{reservation_id}")
def cancel_reservation(reservation_id: str, request: Request, ctx: Ctx = Depends(require("operator"))):
    core = core_of(request)
    row = core.db.one("SELECT * FROM reservation WHERE id = ? AND tenant_id = ?", (reservation_id[:64], ctx.tenant_id))
    if row is None:
        raise HTTPException(404, "not_found")
    res(request).end(row, "cancelled")
    core.audit("reservation_cancelled", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=row["id"])
    return {"status": "cancelled"}


# ---------------------------------------------------------------------- Öffnungszeiten
@router.put("/api/v1/stations/{station_id}/hours")
def put_hours(station_id: str, body: HoursIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    try:
        hours = normalize_hours(body.hours)
    except ValueError:
        raise HTTPException(422, "invalid_hours") from None
    core.db.execute("UPDATE station SET hours = ? WHERE id = ?", (json.dumps(hours) if hours else None, st["id"]))
    core.audit("hours_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=st["id"], detail=hours)
    return {"hours": hours}


def _local(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=TZ).timestamp()


def _closure_out(r, names) -> dict:
    return {"id": r["id"], "station_id": r["station_id"], "station_name": names.get(r["station_id"]) if r["station_id"] else None,
            "starts_at": iso(r["starts_at"]), "ends_at": iso(r["ends_at"]), "note": r["note"]}


@router.get("/api/v1/closures")
def list_closures(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    names = {r["id"]: r["name"] for r in core.db.all("SELECT id, name FROM station WHERE tenant_id = ?", (ctx.tenant_id,))}
    rows = core.db.all("SELECT * FROM closure WHERE tenant_id = ? AND ends_at > ? ORDER BY starts_at", (ctx.tenant_id, core.clock()))
    return {"closures": [_closure_out(r, names) for r in rows]}


@router.post("/api/v1/closures", status_code=201)
def create_closure(body: ClosureIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    if body.station_id:
        _station(core, ctx, body.station_id)
    a, b = _local(body.starts_at), _local(body.ends_at)
    if b <= a or b - a > 366 * 86400:
        raise HTTPException(422, "invalid_period")
    cid = new_id("cl")
    core.db.execute("INSERT INTO closure (id, tenant_id, station_id, starts_at, ends_at, note, created_at) VALUES (?,?,?,?,?,?,?)",
                    (cid, ctx.tenant_id, body.station_id, a, b, body.note, core.clock()))
    core.audit("closure_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=cid,
               detail=body.model_dump())
    names = {r["id"]: r["name"] for r in core.db.all("SELECT id, name FROM station WHERE tenant_id = ?", (ctx.tenant_id,))}
    return _closure_out(core.db.one("SELECT * FROM closure WHERE id = ?", (cid,)), names)


@router.delete("/api/v1/closures/{closure_id}")
def delete_closure(closure_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    cur = core.db.execute("DELETE FROM closure WHERE id = ? AND tenant_id = ?", (closure_id[:64], ctx.tenant_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    core.audit("closure_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=closure_id[:64])
    return {"status": "deleted"}
