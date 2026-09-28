"""Plattform-Administration (Betreiber der SaaS): Mandanten, Tarife, Sperren, Kennzahlen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..core import Ctx, core_of, require
from ..plans import PLANS, get_plan
from .. import billing
from ..schemas import InvoiceIn, InvoicePatch, LicenseIn, TenantPatch
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
                    "mrr_eur": _mrr(core, request, t)})
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
        "mrr_eur": round(sum(_mrr(core, request, t) for t in db.all("SELECT * FROM tenant")), 2),
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


def _mrr(core, request: Request, t) -> float:
    """Monatlicher Umsatz (Hochrechnung): Grundgebühr + aktuelle Stellplätze x 30 Tage x Tagespreis."""
    if t["status"] != "active":
        return 0
    lic = request.app.state.licensing.license(t)
    stalls = core.db.scalar("SELECT COUNT(*) FROM station WHERE tenant_id = ?", (t["id"],))
    return round((lic["base_month_cents"] + 30 * stalls * lic["price_per_stall_day_cents"]) / 100, 2)


# ---------------------------------------------------------------------- Lizenzen und Rechnungen
def _tenant(core, tenant_id: str):
    t = core.db.one("SELECT * FROM tenant WHERE id = ?", (tenant_id[:64],))
    if t is None:
        raise HTTPException(404, "not_found")
    return t


@router.put("/tenants/{tenant_id}/license")
def put_license(tenant_id: str, body: LicenseIn, request: Request, ctx: Ctx = Depends(require(platform=True))):
    core = core_of(request)
    t = _tenant(core, tenant_id)
    until = None
    if body.valid_until:
        from datetime import datetime

        until = datetime.fromisoformat(body.valid_until).replace(tzinfo=billing.TZ).timestamp() + 86399
    core.db.execute(
        "INSERT INTO license (tenant_id, valid_from, valid_until, price_per_stall_day_cents, base_month_cents, notes) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT (tenant_id) DO UPDATE SET valid_until = excluded.valid_until, price_per_stall_day_cents = excluded.price_per_stall_day_cents, "
        "base_month_cents = excluded.base_month_cents, notes = excluded.notes",
        (t["id"], core.clock(), until, body.price_per_stall_day_cents, body.base_month_cents, body.notes))
    core.audit("platform_license_updated", tenant_id=t["id"], user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip,
               detail=body.model_dump())
    return request.app.state.licensing.license(core.db.one("SELECT * FROM tenant WHERE id = ?", (t["id"],)))


@router.get("/invoices")
def invoices(request: Request, month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
             format: str = Query("json", pattern="^(json|csv)$"), ctx: Ctx = Depends(require(platform=True))):
    """Abrechnungsvorschau je Kunde für einen Monat + bereits festgeschriebene Rechnungen."""
    core = core_of(request)
    lic = request.app.state.licensing
    m = month or billing.current_month(core.clock())
    issued = {r["tenant_id"]: lic.invoice_out(r) for r in core.db.all("SELECT * FROM invoice WHERE month = ?", (m,))}
    rows = []
    for t in core.db.all("SELECT * FROM tenant ORDER BY name"):
        p = lic.preview(t, m)
        rows.append({**p, "invoice": issued.get(t["id"])})
    if format == "csv":
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Monat", "Kunde", "Tarif", "Stellplatz-Tage", "Betrag (EUR)", "Rechnungsnummer", "Status"])
        for r in rows:
            inv = r["invoice"] or {}
            w.writerow([m, r["tenant_name"], r["plan"], r["stall_days"], f"{(inv.get('total_cents', r['total_cents'])) / 100:.2f}".replace(".", ","),
                        inv.get("number", ""), inv.get("status", "Vorschau")])
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="lizenzen-{m}.csv"'})
    return {"month": m, "rows": rows, "total_cents": sum((r["invoice"] or r)["total_cents"] for r in rows)}


@router.post("/invoices", status_code=201)
def issue_invoice(body: InvoiceIn, request: Request, ctx: Ctx = Depends(require(platform=True))):
    core = core_of(request)
    t = _tenant(core, body.tenant_id)
    inv = request.app.state.licensing.issue(t, body.month)
    core.audit("platform_invoice_issued", tenant_id=t["id"], user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=inv["number"])
    return inv


@router.patch("/invoices/{invoice_id}")
def patch_invoice(invoice_id: str, body: InvoicePatch, request: Request, ctx: Ctx = Depends(require(platform=True))):
    core = core_of(request)
    r = core.db.one("SELECT * FROM invoice WHERE id = ?", (invoice_id[:64],))
    if r is None:
        raise HTTPException(404, "not_found")
    core.db.execute("UPDATE invoice SET status = ?, paid_at = ? WHERE id = ?",
                    (body.status, core.clock() if body.status == "paid" else None, r["id"]))
    core.audit("platform_invoice_" + body.status, tenant_id=r["tenant_id"], user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=r["number"])
    return request.app.state.licensing.invoice_out(core.db.one("SELECT * FROM invoice WHERE id = ?", (r["id"],)))
