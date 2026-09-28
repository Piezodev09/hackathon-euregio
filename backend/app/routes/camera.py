"""Kamera am Raspberry Pi: Freigabe je Stellplatz, Einzelbilder vom Gateway, Anzeige nur für Admins.

Grundsätze: standardmäßig aus; Einschalten nur mit Tarif-Funktion und dokumentierter Genehmigung;
automatische Löschung nach 1–72 h; jeder Abruf eines Bildes steht im Audit-Log; Ausschalten löscht alle Bilder.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..billing import iso
from ..core import Ctx, core_of, require
from ..plans import get_plan
from ..schemas import CameraIn
from .stations import _station, require_device

router = APIRouter(tags=["camera"])


def snaps(request: Request):
    return request.app.state.snapshots


def _out(r) -> dict:
    return {"id": r["id"], "station_id": r["station_id"], "event_id": r["event_id"], "reason": r["reason"],
            "taken_at": iso(r["taken_at"]), "expires_at": iso(r["expires_at"]), "size": r["size"], "simulated": r["source"] == "simulated"}


@router.put("/api/v1/stations/{station_id}/camera")
def set_camera(station_id: str, body: CameraIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    if body.enabled and not get_plan(ctx.tenant["plan"]).camera:
        raise HTTPException(402, {"code": "plan_feature", "feature": "camera"})
    if body.enabled and not (body.approved_by or "").strip():
        raise HTTPException(422, "camera_approval_required")
    core.db.execute("UPDATE station SET camera_enabled = ?, camera_retention_h = ?, camera_approved_by = ? WHERE id = ?",
                    (int(body.enabled), body.retention_h, (body.approved_by or "").strip() if body.enabled else None, st["id"]))
    deleted = 0
    if not body.enabled:
        deleted = snaps(request).delete_rows(core.db.all("SELECT id, file FROM snapshot WHERE station_id = ?", (st["id"],)))
    core.audit("camera_enabled" if body.enabled else "camera_disabled", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor,
               ip=ctx.ip, target=st["id"], detail={"retention_h": body.retention_h, "approved_by": body.approved_by, "deleted": deleted})
    return {"enabled": body.enabled, "retention_h": body.retention_h, "deleted": deleted}


@router.post("/api/v1/stations/{station_id}/camera/snapshot")
def request_snapshot(station_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    """Testbild anfordern: Das Gateway nimmt beim nächsten Kontakt (≤ 1 min) ein Bild auf."""
    core = core_of(request)
    st = _station(core, ctx, station_id)
    if not st["camera_enabled"]:
        raise HTTPException(409, "camera_disabled")
    dev = core.db.one("SELECT id FROM device WHERE station_id = ? AND revoked_at IS NULL AND enrolled_at IS NOT NULL "
                      "ORDER BY last_heartbeat_at DESC LIMIT 1", (st["id"],))
    if dev is None:
        raise HTTPException(409, "no_managed_gateway")
    core.db.execute("UPDATE device SET pending_command = 'snapshot' WHERE id = ?", (dev["id"],))
    core.audit("camera_snapshot_requested", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=st["id"])
    return {"status": "queued"}


@router.get("/api/v1/stations/{station_id}/snapshots")
def list_snapshots(station_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    rows = core.db.all("SELECT * FROM snapshot WHERE station_id = ? ORDER BY taken_at DESC LIMIT 50", (st["id"],))
    return {"snapshots": [_out(r) for r in rows], "enabled": bool(st["camera_enabled"]), "retention_h": st["camera_retention_h"]}


@router.get("/api/v1/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    r = core.db.one("SELECT * FROM snapshot WHERE id = ? AND tenant_id = ?", (snapshot_id[:64], ctx.tenant_id))
    data = snaps(request).read(r) if r else None
    if data is None:
        raise HTTPException(404, "not_found")
    core.audit("camera_snapshot_viewed", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=r["id"])
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff"})


@router.delete("/api/v1/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    r = core.db.one("SELECT id, file FROM snapshot WHERE id = ? AND tenant_id = ?", (snapshot_id[:64], ctx.tenant_id))
    if r is None:
        raise HTTPException(404, "not_found")
    snaps(request).delete_rows([r])
    core.audit("camera_snapshot_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=r["id"])
    return {"status": "deleted"}


@router.post("/api/v1/agent/snapshot", status_code=201)
async def upload_snapshot(request: Request, reason: str = Query("manual", pattern="^(manual|alert)$"),
                          event_id: str | None = Query(None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"), dev=Depends(require_device)):
    core = core_of(request)
    st = core.db.one("SELECT * FROM station WHERE id = ?", (dev["station_id"],))
    if not st["camera_enabled"]:
        raise HTTPException(403, "camera_disabled")
    if request.headers.get("content-type", "").split(";")[0].strip() != "image/jpeg":
        raise HTTPException(415, "unsupported_media_type")
    data = await request.body()
    if event_id and not core.db.one("SELECT 1 FROM event WHERE id = ? AND station_id = ?", (event_id, st["id"])):
        event_id = None
    try:
        out = snaps(request).store(st, data, reason, event_id, "simulated" if dev["source"] == "simulator" else "live")
    except ValueError:
        raise HTTPException(422, "invalid_image") from None
    return out
