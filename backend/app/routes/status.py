"""Öffentliche Status-Seite einer Organisation (/status#bst_…) und Verwaltung im Portal."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from ..core import Ctx, core_of, limit, require
from ..schemas import ShortText, Strict
from ..security import hash_token, new_token

router = APIRouter(tags=["status"])
read_limit = Depends(limit("read_limiter", "r:"))


class NoteIn(Strict):
    note: ShortText = Field(default="")


@router.get("/api/v1/incidents")
def list_incidents(request: Request, ctx: Ctx = Depends(require("viewer"))):
    rep = request.app.state.incidents.report(ctx.tenant, public=False)
    t = ctx.tenant
    rep["status_page"] = {"enabled": bool(t["status_enabled"]), "has_link": t["status_token_hash"] is not None}
    return rep


@router.patch("/api/v1/incidents/{incident_id}")
def note_incident(incident_id: str, body: NoteIn, request: Request, ctx: Ctx = Depends(require("operator"))):
    """Öffentlicher Hinweis zur Störung, z. B. „Techniker ist informiert“. Keine Personendaten eintragen."""
    core = core_of(request)
    cur = core.db.execute("UPDATE incident SET note = ? WHERE id = ? AND tenant_id = ?", (body.note, incident_id[:64], ctx.tenant_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    return {"status": "ok"}


@router.post("/api/v1/org/status-page")
def status_link(request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    token = new_token("bst_")
    core.db.execute("UPDATE tenant SET status_token_hash = ?, status_enabled = 1 WHERE id = ?", (hash_token(token), ctx.tenant_id))
    core.audit("status_page_rotated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip)
    return {"url": f"{core.s.base_url}/status#{token}", "token": token}


@router.delete("/api/v1/org/status-page")
def status_off(request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    core.db.execute("UPDATE tenant SET status_token_hash = NULL, status_enabled = 0 WHERE id = ?", (ctx.tenant_id,))
    return {"status": "disabled"}


@router.get("/api/v1/public/status", dependencies=[read_limit])
def public_status_page(request: Request):
    """Nur Betriebszustand und Störungen – keine Belegung, Karten, Parkvorgänge oder Kameradaten."""
    core = core_of(request)
    token = request.headers.get("x-status-token", "")
    if not token or len(token) > 200:
        raise HTTPException(404, "not_found")
    t = core.db.one("SELECT * FROM tenant WHERE status_token_hash = ? AND status_enabled = 1 AND status = 'active'",
                    (hash_token(token),))
    if t is None:
        raise HTTPException(404, "not_found")
    return request.app.state.incidents.report(t, public=True)
