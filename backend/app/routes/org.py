"""Organisation (Mandant): Einstellungen, Team, Einladungen, Tarif, Audit-Log, Export, Löschung."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..core import ROLE_RANK, Ctx, core_of, require
from ..mailer import link
from ..plans import PLANS, get_plan
from ..schemas import DeleteOrgIn, InviteIn, OrgPatch, PlanIn, RoleIn
from ..security import verify_password
from ..service import iso

router = APIRouter(prefix="/api/v1/org", tags=["org"])


@router.get("")
def get_org(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    t = ctx.tenant
    return {"id": t["id"], "name": t["name"], "status": t["status"], "mfa_required": bool(t["mfa_required"]),
            "created_at": iso(t["created_at"]), "plan": get_plan(t["plan"]).to_dict(), "usage": core.tenant_usage(t["id"])}


@router.patch("")
def patch_org(body: OrgPatch, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    changes = {}
    if body.name is not None:
        core.db.execute("UPDATE tenant SET name = ? WHERE id = ?", (body.name, ctx.tenant_id))
        changes["name"] = body.name
    if body.mfa_required is not None:
        if ctx.role != "owner":
            raise HTTPException(403, "owner_only")
        if body.mfa_required and not ctx.user["totp_enabled"]:
            # Sonst sperrt sich der Owner selbst aus.
            raise HTTPException(409, "enable_own_mfa_first")
        core.db.execute("UPDATE tenant SET mfa_required = ? WHERE id = ?", (int(body.mfa_required), ctx.tenant_id))
        changes["mfa_required"] = body.mfa_required
    core.audit("org_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, detail=changes)
    return {"status": "ok"}


@router.get("/plans")
def list_plans(ctx: Ctx = Depends(require("viewer"))):
    return {"plans": [p.to_dict() for p in PLANS.values()], "current": ctx.tenant["plan"]}


@router.post("/plan")
def change_plan(body: PlanIn, request: Request, ctx: Ctx = Depends(require("owner"))):
    core = core_of(request)
    if body.plan not in PLANS:
        raise HTTPException(422, "unknown_plan")
    plan = PLANS[body.plan]
    usage = core.tenant_usage(ctx.tenant_id)
    if usage["stations"] > plan.max_stations or usage["users"] > plan.max_users:
        raise HTTPException(409, {"code": "plan_limits_exceeded", "usage": usage})
    old = ctx.tenant["plan"]
    core.db.execute("UPDATE tenant SET plan = ? WHERE id = ?", (plan.id, ctx.tenant_id))
    if not plan.ml_enabled:
        core.db.execute("UPDATE station SET alert_source = 'rule' WHERE tenant_id = ?", (ctx.tenant_id,))
    core.audit("plan_changed", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip,
               detail={"from": old, "to": plan.id})
    return {"status": "ok", "plan": plan.to_dict(), "billing": "manual"}


# ---------------------------------------------------------------------- Team
@router.get("/users")
def list_users(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM user WHERE tenant_id = ? ORDER BY created_at", (ctx.tenant_id,))
    return {"users": [
        {"id": r["id"], "email": r["email"], "name": r["name"], "role": r["role"], "mfa_enabled": bool(r["totp_enabled"]),
         "last_login_at": iso(r["last_login_at"]), "created_at": iso(r["created_at"]), "is_self": r["id"] == ctx.user["id"]}
        for r in rows
    ]}


def _target_user(core, ctx: Ctx, user_id: str):
    u = core.db.one("SELECT * FROM user WHERE id = ? AND tenant_id = ?", (user_id[:64], ctx.tenant_id))
    if u is None:
        raise HTTPException(404, "not_found")
    return u


def _guard_last_owner(core, ctx: Ctx, target) -> None:
    if target["role"] == "owner":
        owners = core.db.scalar("SELECT COUNT(*) FROM user WHERE tenant_id = ? AND role = 'owner'", (ctx.tenant_id,))
        if owners <= 1:
            raise HTTPException(409, "last_owner")


@router.patch("/users/{user_id}")
def change_role(user_id: str, body: RoleIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    target = _target_user(core, ctx, user_id)
    # Niemand vergibt mehr Rechte als er selbst hat oder ändert Höhergestellte.
    if ROLE_RANK[body.role] > ROLE_RANK[ctx.role] or ROLE_RANK[target["role"]] > ROLE_RANK[ctx.role]:
        raise HTTPException(403, "forbidden")
    if target["role"] == "owner" and body.role != "owner":
        _guard_last_owner(core, ctx, target)
    core.db.execute("UPDATE user SET role = ? WHERE id = ?", (body.role, target["id"]))
    core.revoke_sessions(target["id"])  # neue Rechte gelten sofort
    core.audit("role_changed", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip,
               target=target["email"], detail={"from": target["role"], "to": body.role})
    return {"status": "ok"}


@router.delete("/users/{user_id}")
def remove_user(user_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    target = _target_user(core, ctx, user_id)
    if target["id"] == ctx.user["id"]:
        raise HTTPException(409, "cannot_remove_self")
    if ROLE_RANK[target["role"]] > ROLE_RANK[ctx.role]:
        raise HTTPException(403, "forbidden")
    _guard_last_owner(core, ctx, target)
    core.db.execute("DELETE FROM user WHERE id = ?", (target["id"],))
    core.audit("user_removed", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=target["email"])
    return {"status": "removed"}


@router.get("/invitations")
def list_invitations(request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    rows = core.db.all(
        "SELECT * FROM auth_token WHERE tenant_id = ? AND purpose = 'invite' AND used_at IS NULL AND expires_at > ? ORDER BY created_at DESC",
        (ctx.tenant_id, core.clock()))
    return {"invitations": [{"id": r["public_id"], "email": r["email"], "role": r["role"], "expires_at": iso(r["expires_at"]),
                             "created_by": r["created_by"]} for r in rows]}


@router.post("/invitations", status_code=201)
def invite(body: InviteIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    plan = get_plan(ctx.tenant["plan"])
    if core.tenant_usage(ctx.tenant_id)["users"] >= plan.max_users:
        raise HTTPException(409, {"code": "plan_limit", "limit": "max_users", "value": plan.max_users})
    if ROLE_RANK[body.role] > ROLE_RANK[ctx.role]:
        raise HTTPException(403, "forbidden")
    if core.db.one("SELECT 1 FROM user WHERE email = ?", (body.email,)):
        raise HTTPException(409, "email_in_use")
    token = core.issue_token("invite", core.s.invite_token_s, tenant_id=ctx.tenant_id, email=body.email, role=body.role,
                             created_by=ctx.actor)
    core.mailer.send(
        body.email,
        f"{core.s.product_name}: Einladung zu {ctx.tenant['name']} / Invitation",
        f"{ctx.user['name']} lädt Sie zur Organisation \"{ctx.tenant['name']}\" ein (Rolle: {body.role}).\n\n"
        f"Einladung annehmen:\n{link(core.s, 'invite', token)}\n\nDer Link ist {int(core.s.invite_token_s // 3600)} Stunden gültig.",
    )
    core.audit("user_invited", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=body.email,
               detail={"role": body.role})
    return {"status": "invited"}


@router.delete("/invitations/{invite_id}")
def revoke_invitation(invite_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    cur = core.db.execute("DELETE FROM auth_token WHERE public_id = ? AND tenant_id = ? AND purpose = 'invite'",
                          (invite_id[:64], ctx.tenant_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    core.audit("invite_revoked", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip)
    return {"status": "revoked"}


# ---------------------------------------------------------------------- Audit / Export / Löschen
@router.get("/audit")
def audit_log(request: Request, limit: int = Query(100, ge=1, le=500), ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    if not get_plan(ctx.tenant["plan"]).audit_log:
        raise HTTPException(402, {"code": "plan_feature", "feature": "audit_log"})
    rows = core.db.all("SELECT * FROM audit_log WHERE tenant_id = ? ORDER BY at DESC, id DESC LIMIT ?", (ctx.tenant_id, limit))
    return {"entries": [{"at": iso(r["at"]), "actor": r["actor"], "action": r["action"], "target": r["target"], "ip": r["ip"],
                         "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]}


@router.get("/export")
def export(request: Request, ctx: Ctx = Depends(require("owner"))):
    """Datenexport (DSGVO Art. 20). Enthält keine Passwort-Hashes, Tokens oder 2FA-Geheimnisse."""
    core = core_of(request)
    tid = ctx.tenant_id
    db = core.db

    def rows(sql, params=(tid,)):
        return [dict(r) for r in db.all(sql, params)]

    data = {
        "exported_at": iso(core.clock()),
        "organization": {k: ctx.tenant[k] for k in ("id", "name", "plan", "status", "mfa_required", "created_at")},
        "users": rows("SELECT id, email, name, role, locale, created_at, last_login_at, totp_enabled FROM user WHERE tenant_id = ?"),
        "stations": rows("SELECT id, name, location, alert_source, display_enabled, created_at FROM station WHERE tenant_id = ?"),
        "devices": rows("SELECT id, station_id, name, token_prefix, created_at, last_seen_at, revoked_at FROM device WHERE tenant_id = ?"),
        "events": rows("SELECT id, station_id, kind, severity, detector, occurred_at, acknowledged_at, acknowledged_by, source FROM event WHERE tenant_id = ?"),
        "measurements": rows("SELECT m.station_id, m.server_time, m.occupied, m.vibration_score, m.sensor_state, m.source "
                             "FROM measurement m JOIN station st ON st.id = m.station_id WHERE st.tenant_id = ? ORDER BY m.server_time"),
        # Karten ohne UID-Hash (nicht rückrechenbar, aber auch nicht nötig)
        "cards": rows("SELECT id, label, status, created_at, last_seen_at FROM card WHERE tenant_id = ?"),
        "parking_sessions": rows("SELECT id, station_id, card_id, started_at, ended_at, amount_cents, tariff, status, source "
                                 "FROM parking_session WHERE tenant_id = ? ORDER BY started_at"),
        "invoices": rows("SELECT number, month, created_at, lines, total_cents, status, paid_at FROM invoice WHERE tenant_id = ?"),
        "audit_log": rows("SELECT at, actor, action, target, ip, detail FROM audit_log WHERE tenant_id = ? ORDER BY at"),
    }
    core.audit("org_exported", tenant_id=tid, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip)
    return Response(json.dumps(data, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="export-{tid}.json"'})


@router.post("/delete")
def delete_org(body: DeleteOrgIn, request: Request, response: Response, ctx: Ctx = Depends(require("owner"))):
    core = core_of(request)
    if not verify_password(body.password, ctx.user["password_hash"]):
        raise HTTPException(403, "wrong_password")
    if body.confirm_name.strip() != ctx.tenant["name"]:
        raise HTTPException(422, "confirm_name_mismatch")
    tid = ctx.tenant_id
    core.audit("org_deleted", tenant_id=None, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=tid,
               detail={"name": ctx.tenant["name"]})
    core.db.execute("DELETE FROM audit_log WHERE tenant_id = ?", (tid,))
    core.db.execute("DELETE FROM tenant WHERE id = ?", (tid,))  # ON DELETE CASCADE entfernt alles Weitere
    request.app.state.snapshots.purge()  # Kamerabilder auch von der Platte
    core.clear_cookie(response)
    return {"status": "deleted"}
