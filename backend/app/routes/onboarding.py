"""Erste Schritte nach der Registrierung: Checkliste und Beispiel-Stellplatz (Simulation, ohne Hardware)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import Ctx, core_of, require
from ..plans import get_plan
from ..schemas import Strict

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


class HideIn(Strict):
    hidden: bool


@router.get("")
def checklist(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    tid = ctx.tenant_id
    q = lambda sql, *p: core.db.scalar(sql, (tid, *p)) or 0  # noqa: E731
    plan = get_plan(ctx.tenant["plan"])
    steps = [
        ("demo", q("SELECT COUNT(*) FROM station WHERE tenant_id = ? AND demo_sim = 1") > 0, "#/", True),
        ("station", q("SELECT COUNT(*) FROM station WHERE tenant_id = ? AND demo_sim = 0") > 0, "#/stations/new", False),
        ("gateway", q("SELECT COUNT(*) FROM device WHERE tenant_id = ? AND revoked_at IS NULL AND last_seen_at IS NOT NULL") > 0,
         "#/devices", False),
        ("display", q("SELECT COUNT(*) FROM station WHERE tenant_id = ? AND demo_sim = 0 AND "
                      "(display_token_hash IS NOT NULL OR stall_token_hash IS NOT NULL)") > 0, "#/stations", False),
        ("cards", q("SELECT COUNT(*) FROM card WHERE tenant_id = ? AND status = 'active' AND label != 'Beispiel-Karte'") > 0,
         "#/cards", False),
        ("tariff", ctx.tenant["tariff"] is not None or not plan.parking_billing, "#/parking-billing", not plan.parking_billing),
        ("notifications", ctx.user["notify"] is not None, "#/security", False),
        ("team", q("SELECT COUNT(*) FROM user WHERE tenant_id = ?") > 1
         or q("SELECT COUNT(*) FROM auth_token WHERE tenant_id = ? AND purpose = 'invite'") > 0, "#/team", True),
    ]
    out = [{"id": sid, "done": bool(done), "href": href, "optional": optional} for sid, done, href, optional in steps]
    required = [s for s in out if not s["optional"]]
    return {"steps": out, "done": sum(s["done"] for s in required), "total": len(required),
            "hidden": bool(ctx.tenant["onboarding_hidden"]), "demo_available": core.s.demo_stalls}


@router.post("/hide")
def hide(body: HideIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    core.db.execute("UPDATE tenant SET onboarding_hidden = ? WHERE id = ?", (int(body.hidden), ctx.tenant_id))
    return {"hidden": body.hidden}


@router.post("/demo-station", status_code=201)
def demo_station(request: Request, ctx: Ctx = Depends(require("admin"))):
    """Legt einen Beispiel-Stellplatz an, dessen Daten der Server simuliert (deutlich als SIMULATION gekennzeichnet)."""
    core = core_of(request)
    if not core.s.demo_stalls:
        raise HTTPException(403, "demo_disabled")
    plan = get_plan(ctx.tenant["plan"])
    if core.db.scalar("SELECT COUNT(*) FROM station WHERE tenant_id = ? AND demo_sim = 1", (ctx.tenant_id,)):
        raise HTTPException(409, "demo_exists")
    if core.tenant_usage(ctx.tenant_id)["stations"] >= plan.max_stations:
        raise HTTPException(409, {"code": "plan_limit", "limit": "max_stations", "value": plan.max_stations})
    out = request.app.state.demo.create(ctx.tenant_id, ctx.actor)
    core.audit("station_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=out["id"],
               detail={"demo": True})
    return out
