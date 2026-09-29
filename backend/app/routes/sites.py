"""Anlagen (mehrere Stellplätze) im Portal und als öffentliche Großanzeige."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import Ctx, core_of, limit, require
from ..plans import get_plan
from ..schemas import SiteIn, SitePatch
from ..security import hash_token, new_id, new_token

router = APIRouter(tags=["sites"])
read_limit = Depends(limit("read_limiter", "r:"))


def sites(request: Request):
    return request.app.state.sites


def _site(core, ctx: Ctx, site_id: str):
    s = core.db.one("SELECT * FROM site WHERE id = ? AND tenant_id = ?", (site_id[:64], ctx.tenant_id))
    if s is None:
        raise HTTPException(404, "not_found")
    return s


def _assign(core, ctx: Ctx, site_id: str, station_ids: list[str]) -> None:
    ids = list(dict.fromkeys(station_ids))
    found = {r["id"] for r in core.db.all(
        f"SELECT id FROM station WHERE tenant_id = ? AND id IN ({','.join('?' * len(ids)) or 'NULL'})", (ctx.tenant_id, *ids))}
    if len(found) != len(ids):
        raise HTTPException(404, "not_found")
    with core.db.tx() as c:
        c.execute("UPDATE station SET site_id = NULL WHERE site_id = ?", (site_id,))
        for sid in ids:  # ein Stellplatz gehört zu höchstens einer Anlage
            c.execute("UPDATE station SET site_id = ? WHERE id = ?", (site_id, sid))


@router.get("/api/v1/sites")
def list_sites(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM site WHERE tenant_id = ? ORDER BY name, created_at", (ctx.tenant_id,))
    return {"sites": [sites(request).summary(r, public=False) for r in rows]}


@router.post("/api/v1/sites", status_code=201)
def create_site(body: SiteIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    sid = new_id("site")
    core.db.execute("INSERT INTO site (id, tenant_id, name, location, created_at) VALUES (?,?,?,?,?)",
                    (sid, ctx.tenant_id, body.name, body.location, core.clock()))
    if body.station_ids:
        _assign(core, ctx, sid, body.station_ids)
    core.audit("site_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=sid)
    return sites(request).summary(_site(core, ctx, sid), public=False)


@router.patch("/api/v1/sites/{site_id}")
def update_site(site_id: str, body: SitePatch, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    s = _site(core, ctx, site_id)
    if body.waitlist_enabled and not get_plan(ctx.tenant["plan"]).reservations:
        raise HTTPException(402, {"code": "plan_feature", "feature": "reservations"})
    for col in ("name", "location", "hold_minutes"):
        if getattr(body, col) is not None:
            core.db.execute(f"UPDATE site SET {col} = ? WHERE id = ?", (getattr(body, col), s["id"]))  # noqa: S608
    if body.waitlist_enabled is not None:
        core.db.execute("UPDATE site SET waitlist_enabled = ? WHERE id = ?", (int(body.waitlist_enabled), s["id"]))
        if not body.waitlist_enabled:
            request.app.state.waitlist.close_site(s["id"], "cancelled")
    if body.station_ids is not None:
        _assign(core, ctx, s["id"], body.station_ids)
    core.audit("site_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=s["id"],
               detail=body.model_dump(exclude_none=True))
    return sites(request).summary(_site(core, ctx, s["id"]), public=False)


@router.delete("/api/v1/sites/{site_id}")
def delete_site(site_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    s = _site(core, ctx, site_id)
    request.app.state.waitlist.close_site(s["id"], "cancelled")
    with core.db.tx() as c:
        c.execute("UPDATE station SET site_id = NULL WHERE site_id = ?", (s["id"],))
        c.execute("DELETE FROM site WHERE id = ?", (s["id"],))
    core.audit("site_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=s["id"])
    return {"status": "deleted"}


@router.post("/api/v1/sites/{site_id}/display-link")
def site_display_link(site_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    """Öffentlicher, nur lesender Link für eine Großanzeige der Anlage. Ersetzt einen alten Link."""
    core = core_of(request)
    s = _site(core, ctx, site_id)
    if not get_plan(ctx.tenant["plan"]).public_display:
        raise HTTPException(402, {"code": "plan_feature", "feature": "public_display"})
    token = new_token("bsa_")
    core.db.execute("UPDATE site SET display_token_hash = ?, display_enabled = 1 WHERE id = ?", (hash_token(token), s["id"]))
    core.audit("site_display_rotated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=s["id"])
    return {"url": f"{core.s.base_url}/a#{token}", "token": token}


@router.delete("/api/v1/sites/{site_id}/display-link")
def site_display_off(site_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    s = _site(core, ctx, site_id)
    core.db.execute("UPDATE site SET display_token_hash = NULL, display_enabled = 0 WHERE id = ?", (s["id"],))
    return {"status": "disabled"}


@router.get("/api/v1/public/site/status", dependencies=[read_limit])
def public_site(request: Request):
    """Großanzeige einer Anlage: Frei-Zähler und Zustand je Stellplatz – ohne Karten- oder Personendaten."""
    core = core_of(request)
    token = request.headers.get("x-site-token", "")
    if not token or len(token) > 200:
        raise HTTPException(404, "not_found")
    s = core.db.one("SELECT s.* FROM site s JOIN tenant t ON t.id = s.tenant_id WHERE s.display_token_hash = ? "
                    "AND s.display_enabled = 1 AND t.status = 'active'", (hash_token(token),))
    if s is None:
        raise HTTPException(404, "not_found")
    return sites(request).summary(s, public=True)
