"""Plattform-Administration (Betreiber der SaaS): Mandanten, Tarife, Sperren, Kennzahlen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import Ctx, core_of, require
from ..plans import PLANS, get_plan
from ..schemas import TenantPatch
from ..service import iso

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


@router.get("/tenants")
def list_tenants(request: Request, ctx: Ctx = Depends(require(platform=True))):
    core = core_of(request)
    out = []
    for t in core.db.all("SELECT * FROM tenant ORDER BY created_at DESC"):
        owner = core.db.one("SELECT email FROM user WHERE tenant_id = ? AND role = 'owner' ORDER BY created_at LIMIT 1", (t["id"],))
        out.append({"id": t["id"], "name": t["name"], "plan": t["plan"], "status": t["status"], "created_at": iso(t["created_at"]),
                    "owner_email": owner["email"] if owner else None, "usage": core.tenant_usage(t["id"]),
                    "mrr_eur": get_plan(t["plan"]).price_eur_month if t["status"] == "active" else 0})
    return {"tenants": out}


@router.patch("/tenants/{tenant_id}")
def patch_tenant(tenant_id: str, body: TenantPatch, request: Request, ctx: Ctx = Depends(require(platform=True))):
    core = core_of(request)
    t = core.db.one("SELECT * FROM tenant WHERE id = ?", (tenant_id[:64],))
    if t is None:
        raise HTTPException(404, "not_found")
    changes = body.model_dump(exclude_none=True)
    if "plan" in changes and changes["plan"] not in PLANS:
        raise HTTPException(422, "unknown_plan")
    for k, v in changes.items():
        core.db.execute(f"UPDATE tenant SET {k} = ? WHERE id = ?", (v, t["id"]))
    if changes.get("status") == "suspended":
        # Alle Sitzungen des Mandanten sofort beenden.
        core.db.execute("DELETE FROM session WHERE user_id IN (SELECT id FROM user WHERE tenant_id = ?)", (t["id"],))
    core.audit("platform_tenant_updated", tenant_id=t["id"], user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, detail=changes)
    return {"status": "ok"}


@router.get("/stats")
def stats(request: Request, ctx: Ctx = Depends(require(platform=True))):
    core = core_of(request)
    db = core.db
    tenants = db.all("SELECT plan, status FROM tenant")
    return {
        "tenants": len(tenants),
        "active_tenants": sum(1 for t in tenants if t["status"] == "active"),
        "mrr_eur": sum(get_plan(t["plan"]).price_eur_month for t in tenants if t["status"] == "active"),
        "users": db.scalar("SELECT COUNT(*) FROM user WHERE tenant_id IS NOT NULL"),
        "stations": db.scalar("SELECT COUNT(*) FROM station"),
        "devices_online": db.scalar("SELECT COUNT(*) FROM device WHERE revoked_at IS NULL AND last_seen_at > ?", (core.clock() - 120,)),
        "measurements_24h": db.scalar("SELECT COUNT(*) FROM measurement WHERE server_time > ?", (core.clock() - 86400,)),
        "by_plan": {p: sum(1 for t in tenants if t["plan"] == p) for p in PLANS},
    }


@router.get("/audit")
def platform_audit(request: Request, ctx: Ctx = Depends(require(platform=True))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM audit_log ORDER BY at DESC, id DESC LIMIT 200")
    return {"entries": [{"at": iso(r["at"]), "tenant_id": r["tenant_id"], "actor": r["actor"], "action": r["action"],
                         "target": r["target"], "ip": r["ip"]} for r in rows]}
