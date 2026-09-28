"""Integrationen für die Schul-IT: API-Schlüssel und Webhooks verwalten (Portal) und die externe API (/api/v1/ext).

Externe API: `Authorization: Bearer sbk_…`. Rechte je Schlüssel: `read` (Stellplätze, Status, Ereignisse, Karten,
Berichte) und `reservations` (Reservierungen anlegen/beenden). Kein Zugriff auf Konten, Rollen oder Einstellungen.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..core import Ctx, core_of, require
from ..integrations import EVENTS, MAX_WEBHOOKS, UrlRejected, Integrations
from ..plans import get_plan
from ..schemas import ApiKeyIn, ReservationIn, WebhookIn, WebhookPatch
from .ops import create_reservation, res

router = APIRouter(tags=["integrations"])


def integ(request: Request) -> Integrations:
    return request.app.state.integrations


def _feature(ctx: Ctx) -> None:
    if not get_plan(ctx.tenant["plan"]).integrations:
        raise HTTPException(402, {"code": "plan_feature", "feature": "integrations"})


# ---------------------------------------------------------------------- Portal: API-Schlüssel
@router.get("/api/v1/integrations")
def overview(request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    i = integ(request)
    keys = core.db.all("SELECT * FROM api_key WHERE tenant_id = ? ORDER BY revoked_at IS NOT NULL, created_at DESC", (ctx.tenant_id,))
    hooks = core.db.all("SELECT * FROM webhook WHERE tenant_id = ? ORDER BY created_at", (ctx.tenant_id,))
    return {"enabled": get_plan(ctx.tenant["plan"]).integrations, "api_keys": [i.key_out(k) for k in keys],
            "webhooks": [i.webhook_out(w) for w in hooks], "events": list(EVENTS), "base_url": core.s.base_url,
            "allow_private": core.s.webhooks_allow_private}


@router.post("/api/v1/integrations/keys", status_code=201)
def create_key(body: ApiKeyIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    _feature(ctx)
    if core.db.scalar("SELECT COUNT(*) FROM api_key WHERE tenant_id = ? AND revoked_at IS NULL", (ctx.tenant_id,)) >= 10:
        raise HTTPException(409, {"code": "plan_limit", "limit": "api_keys", "value": 10})
    out = integ(request).create_key(ctx.tenant_id, body.name, list(body.scopes), ctx.actor)
    core.audit("api_key_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=out["id"],
               detail={"scopes": out["scopes"]})
    return out  # Schlüssel wird genau einmal angezeigt


@router.delete("/api/v1/integrations/keys/{key_id}")
def revoke_key(key_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    cur = core.db.execute("UPDATE api_key SET revoked_at = ? WHERE id = ? AND tenant_id = ? AND revoked_at IS NULL",
                          (core.clock(), key_id[:64], ctx.tenant_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    core.audit("api_key_revoked", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=key_id[:64])
    return {"status": "revoked"}


# ---------------------------------------------------------------------- Portal: Webhooks
def _hook(core, ctx: Ctx, wid: str):
    w = core.db.one("SELECT * FROM webhook WHERE id = ? AND tenant_id = ?", (wid[:64], ctx.tenant_id))
    if w is None:
        raise HTTPException(404, "not_found")
    return w


@router.post("/api/v1/integrations/webhooks", status_code=201)
def create_webhook(body: WebhookIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    _feature(ctx)
    if core.db.scalar("SELECT COUNT(*) FROM webhook WHERE tenant_id = ?", (ctx.tenant_id,)) >= MAX_WEBHOOKS:
        raise HTTPException(409, {"code": "plan_limit", "limit": "webhooks", "value": MAX_WEBHOOKS})
    try:
        out = integ(request).create_webhook(ctx.tenant_id, body.url, list(body.events), ctx.actor)
    except UrlRejected as exc:
        raise HTTPException(422, str(exc)) from None
    core.audit("webhook_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=out["id"],
               detail={"url": body.url, "events": out["events"]})
    return out  # Geheimnis wird genau einmal angezeigt


@router.patch("/api/v1/integrations/webhooks/{wid}")
def patch_webhook(wid: str, body: WebhookPatch, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    w = _hook(core, ctx, wid)
    if body.active is not None:
        core.db.execute("UPDATE webhook SET active = ? WHERE id = ?", (int(body.active), w["id"]))
    if body.events is not None:
        core.db.execute("UPDATE webhook SET events = ? WHERE id = ?", (json.dumps(sorted(set(body.events))), w["id"]))
    core.audit("webhook_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=w["id"],
               detail=body.model_dump(exclude_none=True))
    return integ(request).webhook_out(_hook(core, ctx, wid))


@router.delete("/api/v1/integrations/webhooks/{wid}")
def delete_webhook(wid: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    w = _hook(core, ctx, wid)
    core.db.execute("DELETE FROM webhook WHERE id = ?", (w["id"],))
    core.audit("webhook_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=w["id"])
    return {"status": "deleted"}


@router.post("/api/v1/integrations/webhooks/{wid}/secret")
def rotate_webhook_secret(wid: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    w = _hook(core, ctx, wid)
    secret = integ(request).rotate_secret(w)
    core.audit("webhook_secret_rotated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=w["id"])
    return {"secret": secret}


@router.post("/api/v1/integrations/webhooks/{wid}/test")
def test_webhook(wid: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    """Sendet sofort ein Test-Ereignis und liefert das Ergebnis zurück (ohne Wiederholungen)."""
    core = core_of(request)
    _feature(ctx)
    w = _hook(core, ctx, wid)
    i = integ(request)
    sync = i.sync
    i.sync = True
    try:
        i.dispatch(w, "test", {"message": "Test von der Smart Bicycle Box", "by": ctx.actor})
    finally:
        i.sync = sync
    return {"deliveries": i.deliveries(w["id"], 1)}


@router.get("/api/v1/integrations/webhooks/{wid}/deliveries")
def webhook_deliveries(wid: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    w = _hook(core, ctx, wid)
    return {"deliveries": integ(request).deliveries(w["id"])}


# ---------------------------------------------------------------------- Externe API (API-Schlüssel)
def api_key(scope: str):
    def dep(request: Request):
        core = core_of(request)
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not core.read_limiter.allow(f"k:{token[:12] or 'none'}"):
            raise HTTPException(429, "rate_limited")
        key = integ(request).authenticate(token)
        if key is None:
            raise HTTPException(401, "invalid_api_key", headers={"WWW-Authenticate": "Bearer"})
        if key["tenant_status"] != "active":
            raise HTTPException(403, "tenant_suspended")
        if not get_plan(key["tenant_plan"]).integrations:
            raise HTTPException(402, {"code": "plan_feature", "feature": "integrations"})
        if scope not in json.loads(key["scopes"]):
            raise HTTPException(403, "scope_missing")
        return key

    return dep


def _ext_station(core, key, station_id: str):
    st = core.db.one("SELECT * FROM station WHERE id = ? AND tenant_id = ?", (station_id[:64], key["tenant_id"]))
    if st is None:
        raise HTTPException(404, "not_found")
    return st


def _ext_status(request: Request, st) -> dict:
    s = request.app.state.monitoring.status(st, public=True)
    keep = ("station_id", "display_name", "location", "state", "presence", "unknown_reason", "last_update", "age_s",
            "maintenance", "closed", "hours", "reservation", "simulated_data", "server_time")
    return {k: s.get(k) for k in keep} | {"alert_active": s["alert"] is not None, "parked": s["session"] is not None}


@router.get("/api/v1/ext/stations")
def ext_stations(request: Request, key=Depends(api_key("read"))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM station WHERE tenant_id = ? ORDER BY created_at", (key["tenant_id"],))
    return {"stations": [_ext_status(request, st) for st in rows]}


@router.get("/api/v1/ext/stations/{station_id}")
def ext_station(station_id: str, request: Request, key=Depends(api_key("read"))):
    return _ext_status(request, _ext_station(core_of(request), key, station_id))


@router.get("/api/v1/ext/events")
def ext_events(request: Request, limit: int = Query(50, ge=1, le=200), key=Depends(api_key("read"))):
    return {"events": [{k: e[k] for k in ("id", "station_id", "station_name", "kind", "severity", "occurred_at", "acknowledged_at",
                                          "simulated")}
                       for e in request.app.state.monitoring.events(key["tenant_id"], None, limit)]}


@router.get("/api/v1/ext/cards")
def ext_cards(request: Request, key=Depends(api_key("read"))):
    core = core_of(request)
    rows = core.db.all("SELECT id, label, status FROM card WHERE tenant_id = ? AND status = 'active' ORDER BY label", (key["tenant_id"],))
    return {"cards": [dict(r) for r in rows]}


@router.get("/api/v1/ext/reports")
def ext_report(request: Request, period: str = Query("week", pattern="^(day|week|month)$"),
               day: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"), key=Depends(api_key("read"))):
    core = core_of(request)
    tenant = core.db.one("SELECT * FROM tenant WHERE id = ?", (key["tenant_id"],))
    if not request.app.state.reports.allowed(tenant):
        raise HTTPException(402, {"code": "plan_feature", "feature": "reports"})
    return request.app.state.reports.build(tenant, period, day)


@router.post("/api/v1/ext/stations/{station_id}/reservations", status_code=201)
def ext_reserve(station_id: str, body: ReservationIn, request: Request, key=Depends(api_key("reservations"))):
    core = core_of(request)
    st = _ext_station(core, key, station_id)
    out = create_reservation(request, key["tenant_id"], st, body, f"api:{key['name']}", "api")
    core.audit("reservation_created", tenant_id=key["tenant_id"], actor=f"api:{key['prefix']}", target=out["id"],
               detail={"minutes": body.minutes, "via": "api"})
    return out


@router.delete("/api/v1/ext/reservations/{reservation_id}")
def ext_cancel(reservation_id: str, request: Request, key=Depends(api_key("reservations"))):
    core = core_of(request)
    row = core.db.one("SELECT * FROM reservation WHERE id = ? AND tenant_id = ?", (reservation_id[:64], key["tenant_id"]))
    if row is None:
        raise HTTPException(404, "not_found")
    res(request).end(row, "cancelled")
    core.audit("reservation_cancelled", tenant_id=key["tenant_id"], actor=f"api:{key['prefix']}", target=row["id"])
    return {"status": "cancelled"}
