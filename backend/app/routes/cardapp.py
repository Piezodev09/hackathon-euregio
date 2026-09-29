"""Karten-App für Radfahrende (PWA unter /k).

Zugang über einen persönlichen Karten-Link (`/k#bck_…`), den die Betreuung je Karte erzeugt. Gespeichert wird nur
der Hash; ein neuer Link macht den alten ungültig. Keine Namen, keine E-Mail, kein Standort. Antworten enthalten
nie die UID oder Daten anderer Karten.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core import Ctx, client_ip, core_of, require
from ..integrations import UrlRejected
from ..parking import prepaid
from ..plans import get_plan
from ..reservations import ReservationError
from ..schemas import CardReservationIn, PushOffIn, PushSubIn, WaitlistIn
from ..security import hash_token, new_token
from ..service import iso
from ..waitlist import WaitlistError

router = APIRouter(tags=["cardapp"])


# ---------------------------------------------------------------------- Portal: Link je Karte
def _card(core, ctx: Ctx, card_id: str):
    c = core.db.one("SELECT * FROM card WHERE id = ? AND tenant_id = ?", (card_id[:64], ctx.tenant_id))
    if c is None:
        raise HTTPException(404, "not_found")
    return c


@router.post("/api/v1/cards/{card_id}/link")
def card_link(card_id: str, request: Request, ctx: Ctx = Depends(require("operator"))):
    """Persönlichen App-Link erzeugen oder erneuern (der alte wird ungültig)."""
    core = core_of(request)
    c = _card(core, ctx, card_id)
    if not get_plan(ctx.tenant["plan"]).nfc:
        raise HTTPException(402, {"code": "plan_feature", "feature": "nfc"})
    if c["status"] != "active":
        raise HTTPException(409, "card_not_active")
    token = new_token("bck_")
    core.db.execute("UPDATE card SET link_token_hash = ?, link_created_at = ? WHERE id = ?", (hash_token(token), core.clock(), c["id"]))
    request.app.state.push.unsubscribe(c["id"])  # alte Geräte erhalten keine Nachrichten mehr
    core.audit("card_link_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=c["id"])
    return {"url": f"{core.s.base_url}/k#{token}", "token": token}


@router.delete("/api/v1/cards/{card_id}/link")
def card_link_off(card_id: str, request: Request, ctx: Ctx = Depends(require("operator"))):
    core = core_of(request)
    c = _card(core, ctx, card_id)
    core.db.execute("UPDATE card SET link_token_hash = NULL, link_created_at = NULL WHERE id = ?", (c["id"],))
    request.app.state.push.unsubscribe(c["id"])
    core.audit("card_link_revoked", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=c["id"])
    return {"status": "revoked"}


# ---------------------------------------------------------------------- Öffentlich (Karten-Link)
def _holder(request: Request):
    """Karte zum Link. Gesperrte Karte, gesperrte Organisation oder unbekannter Link: 404."""
    core = core_of(request)
    token = request.headers.get("x-card-token", "")
    if not core.card_limiter.allow(f"c:{client_ip(request)}"):
        raise HTTPException(429, "rate_limited", headers={"Retry-After": "30"})
    if not token.startswith("bck_") or len(token) > 200:
        raise HTTPException(404, "not_found")
    c = core.db.one("SELECT c.* FROM card c JOIN tenant t ON t.id = c.tenant_id WHERE c.link_token_hash = ? "
                    "AND c.status = 'active' AND t.status = 'active'", (hash_token(token),))
    if c is None:
        raise HTTPException(404, "not_found")
    return c


def _station_names(core, tenant_id: str) -> dict:
    return {r["id"]: r["name"] for r in core.db.all("SELECT id, name FROM station WHERE tenant_id = ?", (tenant_id,))}


@router.get("/api/v1/public/push-key")
def push_key(request: Request):
    return {"key": request.app.state.push.vapid.public_key}


@router.get("/api/v1/public/card")
def card_home(request: Request):
    core = core_of(request)
    c = _holder(request)
    tenant = core.db.one("SELECT * FROM tenant WHERE id = ?", (c["tenant_id"],))
    plan = get_plan(tenant["plan"])
    park = request.app.state.parking
    res_mod = request.app.state.reservations
    sites_mod = request.app.state.sites
    wl = request.app.state.waitlist
    names = _station_names(core, c["tenant_id"])
    is_prepaid = prepaid(core, c["tenant_id"])

    open_s = core.db.one("SELECT * FROM parking_session WHERE card_id = ? AND status = 'open'", (c["id"],))
    session = None
    if open_s is not None:
        session = {**park.session_out(open_s, public=True), "station_name": names.get(open_s["station_id"])}
    res_mod.expire()
    r = core.db.one("SELECT * FROM reservation WHERE card_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1", (c["id"],))
    reservation = {**res_mod.out(r, public=True), "station_name": names.get(r["station_id"])} if r else None
    e = wl.active_entry(c["id"])
    waitlist = None
    if e is not None:
        site = core.db.one("SELECT name FROM site WHERE id = ?", (e["site_id"],))
        waitlist = {"site_id": e["site_id"], "site_name": site["name"] if site else "", "status": e["status"],
                    "position": wl.position(e), "since": iso(e["created_at"]),
                    "station_name": names.get(e["station_id"]) if e["station_id"] else None}

    # Plätze: Anlagen und einzelne Stellplätze ohne Anlage
    sites = [sites_mod.summary(s) for s in core.db.all("SELECT * FROM site WHERE tenant_id = ? ORDER BY name", (c["tenant_id"],))]
    loose = core.db.all("SELECT * FROM station WHERE tenant_id = ? AND site_id IS NULL ORDER BY name", (c["tenant_id"],))
    if loose:
        stalls = []
        for st in loose:
            s = request.app.state.monitoring.status(st, public=True)
            stalls.append({"station_id": st["id"], "name": st["name"], "state": s["state"], "category": sites_mod.category(s),
                           "simulated": s["simulated_data"], "unknown_reason": s["unknown_reason"],
                           "maintenance": s["maintenance"], "closed": bool(s["closed"])})
        sites.append({"id": None, "name": None, "stalls": stalls, "total": len(stalls),
                      "free": sum(1 for x in stalls if x["category"] == "available"), "waitlist_enabled": False})

    sessions = core.db.all("SELECT * FROM parking_session WHERE card_id = ? ORDER BY started_at DESC LIMIT 20", (c["id"],))
    return {
        "card": {"label": c["label"], "status": c["status"], "prepaid": is_prepaid,
                 "balance_cents": c["balance_cents"] if is_prepaid else None},
        "org": {"name": tenant["name"]},
        "session": session, "reservation": reservation, "waitlist": waitlist, "sites": sites,
        "sessions": [{**park.session_out(s, public=True), "station_name": names.get(s["station_id"])} for s in sessions],
        "transactions": park.transactions(c["id"], limit=20) if is_prepaid else [],
        "features": {"reserve": bool(tenant["cyclist_reserve"]) and plan.reservations, "waitlist": plan.reservations,
                     "push": True},
        "push_devices": core.db.scalar("SELECT COUNT(*) FROM push_sub WHERE card_id = ?", (c["id"],)) or 0,
        "server_time": iso(core.clock()),
    }


@router.post("/api/v1/public/card/reservations", status_code=201)
def card_reserve(body: CardReservationIn, request: Request):
    core = core_of(request)
    c = _holder(request)
    tenant = core.db.one("SELECT * FROM tenant WHERE id = ?", (c["tenant_id"],))
    if not (tenant["cyclist_reserve"] and get_plan(tenant["plan"]).reservations):
        raise HTTPException(403, "self_reservation_disabled")
    st = core.db.one("SELECT * FROM station WHERE id = ? AND tenant_id = ?", (body.station_id, c["tenant_id"]))
    if st is None:
        raise HTTPException(404, "not_found")
    res_mod = request.app.state.reservations
    res_mod.expire()
    if core.db.one("SELECT 1 FROM reservation WHERE card_id = ? AND status = 'active'", (c["id"],)):
        raise HTTPException(409, "already_reserved_by_card")
    if core.db.one("SELECT 1 FROM parking_session WHERE card_id = ? AND status = 'open'", (c["id"],)):
        raise HTTPException(409, "already_parked")
    status = request.app.state.monitoring.status(st, public=True)
    if status["state"] != "free":  # nur sicher freie Plätze, nie „unbekannt“
        raise HTTPException(409, "not_free")
    try:
        r = res_mod.create(st, body.minutes, card_id=c["id"], label="Karten-App", actor=f"card:{c['id']}", via="card",
                           presence=status["presence"])
    except ReservationError as exc:
        raise HTTPException(409, str(exc)) from None
    return {**res_mod.out(r, public=True), "station_name": st["name"]}


@router.delete("/api/v1/public/card/reservations/{reservation_id}")
def card_unreserve(reservation_id: str, request: Request):
    core = core_of(request)
    c = _holder(request)
    r = core.db.one("SELECT * FROM reservation WHERE id = ? AND card_id = ? AND status = 'active'", (reservation_id[:64], c["id"]))
    if r is None:
        raise HTTPException(404, "not_found")
    entry = core.db.one("SELECT * FROM waitlist WHERE reservation_id = ? AND status = 'offered'", (r["id"],))
    if entry is not None:
        request.app.state.waitlist.leave(entry)
    else:
        request.app.state.reservations.end(r, "cancelled")
    return {"status": "cancelled"}


@router.post("/api/v1/public/card/waitlist", status_code=201)
def card_wait(body: WaitlistIn, request: Request):
    core = core_of(request)
    c = _holder(request)
    tenant = core.db.one("SELECT plan FROM tenant WHERE id = ?", (c["tenant_id"],))
    if not get_plan(tenant["plan"]).reservations:
        raise HTTPException(402, {"code": "plan_feature", "feature": "reservations"})
    site = core.db.one("SELECT * FROM site WHERE id = ? AND tenant_id = ?", (body.site_id, c["tenant_id"]))
    if site is None:
        raise HTTPException(404, "not_found")
    wl = request.app.state.waitlist
    try:
        e = wl.join(site, c)
    except WaitlistError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"status": e["status"], "position": wl.position(e)}


@router.delete("/api/v1/public/card/waitlist")
def card_unwait(request: Request):
    c = _holder(request)
    wl = request.app.state.waitlist
    e = wl.active_entry(c["id"])
    if e is None:
        raise HTTPException(404, "not_found")
    wl.leave(e)
    return {"status": "cancelled"}


@router.post("/api/v1/public/card/push", status_code=201)
def card_push_on(body: PushSubIn, request: Request):
    c = _holder(request)
    try:
        request.app.state.push.subscribe(c["tenant_id"], c["id"], body.endpoint, body.keys.p256dh, body.keys.auth)
    except UrlRejected as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError:
        raise HTTPException(422, "invalid_subscription") from None
    return {"status": "subscribed"}


@router.post("/api/v1/public/card/push/test")
def card_push_test(request: Request):
    core = core_of(request)
    c = _holder(request)
    if not core.mail_limiter.allow(f"pt:{c['id']}"):
        raise HTTPException(429, "rate_limited")
    n = request.app.state.push.send_card(c["id"], {"type": "test", "title": "Smart Bicycle Box",
                                                  "body": "Benachrichtigungen funktionieren.", "url": "/k"})
    return {"devices": n}


@router.post("/api/v1/public/card/push/off")
def card_push_off(body: PushOffIn, request: Request):
    c = _holder(request)
    request.app.state.push.unsubscribe(c["id"], body.endpoint)
    return {"status": "unsubscribed"}
