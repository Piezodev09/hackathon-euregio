"""NFC-Ein-/Auschecken, Karten, Parkvorgänge, Parktarife, Monatsabrechnung und Lizenzstatus."""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .. import billing
from ..core import Ctx, core_of, require
from ..parking import Parking, normalize_uid, prepaid, uid_from
from ..plans import get_plan
from ..schemas import CardIn, CardPatch, LearnIn, PaidIn, PaymentModeIn, StationTariffIn, TapIn, TariffIn, TopupIn
from .stations import _station, require_device

router = APIRouter(tags=["parking"])
MONTH = Query(None, pattern=r"^\d{4}-\d{2}$")


def parking(request: Request) -> Parking:
    return request.app.state.parking


def _feature(ctx: Ctx, feature: str) -> None:
    if not get_plan(ctx.tenant["plan"]).has(feature):
        raise HTTPException(402, {"code": "plan_feature", "feature": feature})


def _month(core, month: str | None) -> str:
    m = month or billing.current_month(core.clock())
    try:
        billing.month_bounds(m)
    except ValueError:
        raise HTTPException(422, "invalid_month") from None
    return m


# ---------------------------------------------------------------------- Gateway -> Plattform
@router.post("/api/v1/nfc/tap")
def nfc_tap(body: TapIn, request: Request, dev=Depends(require_device)):
    core = core_of(request)
    if body.station_id != dev["station_id"]:
        raise HTTPException(403, "station_mismatch")
    try:
        uid = normalize_uid(body.uid)
    except ValueError:
        raise HTTPException(422, "invalid_uid") from None
    st = core.db.one("SELECT * FROM station WHERE id = ?", (dev["station_id"],))
    out = parking(request).tap(st, dev["id"], body.sequence, uid, body.age_ms / 1000, body.source, body.reader)
    # Die Antwort enthält keine Kartendaten – nur das Ergebnis für die Anzeige am Stellplatz.
    return {k: v for k, v in out.items() if k in ("result", "amount_cents", "previous", "balance_cents")}


# ---------------------------------------------------------------------- Karten
def _card(core, ctx: Ctx, card_id: str):
    c = core.db.one("SELECT * FROM card WHERE id = ? AND tenant_id = ?", (card_id[:64], ctx.tenant_id))
    if c is None:
        raise HTTPException(404, "not_found")
    return c


def _card_out(core, c) -> dict:
    open_s = core.db.one("SELECT station_id FROM parking_session WHERE card_id = ? AND status = 'open'", (c["id"],))
    st = core.db.scalar("SELECT name FROM station WHERE id = ?", (c["last_station_id"],)) if c["last_station_id"] else None
    return {"id": c["id"], "label": c["label"], "status": c["status"], "created_at": billing.iso(c["created_at"]),
            "last_seen_at": billing.iso(c["last_seen_at"]), "last_station_name": st, "parked": open_s is not None,
            "balance_cents": c["balance_cents"], "app_link": c["link_token_hash"] is not None,
            "app_link_created_at": billing.iso(c["link_created_at"])}


@router.get("/api/v1/cards")
def list_cards(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM card WHERE tenant_id = ? ORDER BY status = 'pending' DESC, label, created_at", (ctx.tenant_id,))
    return {"cards": [_card_out(core, c) for c in rows], "prepaid": prepaid(core, ctx.tenant_id)}


@router.post("/api/v1/cards/{card_id}/topup")
def topup_card(card_id: str, body: TopupIn, request: Request, ctx: Ctx = Depends(require("operator"))):
    """Guthaben aufladen (z. B. Bareinzahlung im Sekretariat) oder korrigieren. Kein Zahlungsanbieter."""
    core = core_of(request)
    _feature(ctx, "parking_billing")
    c = _card(core, ctx, card_id)
    if body.kind == "correction" and ctx.role not in ("admin", "owner"):
        raise HTTPException(403, "forbidden")
    try:
        balance = parking(request).topup(c, body.amount_cents, note=body.note, actor=ctx.actor, kind=body.kind)
    except ValueError:
        raise HTTPException(422, "invalid_amount") from None
    core.audit("card_" + body.kind, tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=c["id"],
               detail={"amount_cents": body.amount_cents, "balance_cents": balance})
    return {"balance_cents": balance, "card": _card_out(core, _card(core, ctx, card_id))}


@router.get("/api/v1/cards/{card_id}/transactions")
def card_transactions(card_id: str, request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    c = _card(core, ctx, card_id)
    return {"card": _card_out(core, c), "transactions": parking(request).transactions(c["id"])}


@router.post("/api/v1/cards", status_code=201)
def create_card(body: CardIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    """Karte direkt über ihre UID anlegen (z. B. vom Kartenaufdruck abgetippt)."""
    core = core_of(request)
    _feature(ctx, "nfc")
    try:
        uid = uid_from(body.uid, body.uid_format)
    except ValueError:
        raise HTTPException(422, "invalid_uid") from None
    c = parking(request).find_or_create_card(ctx.tenant_id, uid, label=body.label, status="active")
    if c["status"] == "pending" or not c["label"]:
        core.db.execute("UPDATE card SET status = 'active', label = ? WHERE id = ?", (body.label, c["id"]))
    core.audit("card_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=c["id"])
    return _card_out(core, _card(core, ctx, c["id"]))


@router.post("/api/v1/cards/learn")
def start_learn(body: LearnIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    """Anlern-Modus: die nächste Karte an einem Leser der Organisation (60 s) wird so benannt und freigegeben."""
    core = core_of(request)
    _feature(ctx, "nfc")
    if not core.db.scalar("SELECT COUNT(*) FROM device WHERE tenant_id = ? AND revoked_at IS NULL", (ctx.tenant_id,)):
        raise HTTPException(409, "no_reader")
    core.audit("card_learn_started", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip)
    return parking(request).start_learn(ctx.tenant_id, body.label, ctx.user["id"])


@router.get("/api/v1/cards/learn")
def learn_status(request: Request, ctx: Ctx = Depends(require("admin"))):
    return parking(request).learn_status(ctx.tenant_id)


@router.delete("/api/v1/cards/learn", status_code=204)
def cancel_learn(request: Request, ctx: Ctx = Depends(require("admin"))):
    parking(request).cancel_learn(ctx.tenant_id)


@router.patch("/api/v1/cards/{card_id}")
def patch_card(card_id: str, body: CardPatch, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    c = _card(core, ctx, card_id)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        core.db.execute(f"UPDATE card SET {k} = ? WHERE id = ?", (v, c["id"]))
    core.audit("card_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=c["id"], detail=changes)
    return _card_out(core, _card(core, ctx, card_id))


@router.delete("/api/v1/cards/{card_id}")
def delete_card(card_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    c = _card(core, ctx, card_id)
    core.db.execute("DELETE FROM card WHERE id = ?", (c["id"],))
    core.audit("card_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=c["id"])
    return {"status": "deleted"}


# ---------------------------------------------------------------------- Parkvorgänge
@router.get("/api/v1/parking/sessions")
def list_sessions(request: Request, station_id: str | None = Query(None, max_length=64), status: str | None = Query(None, pattern="^(open|closed|cancelled)$"),
                  month: str | None = MONTH, limit: int = Query(200, ge=1, le=1000), ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    sql = "SELECT * FROM parking_session WHERE tenant_id = ?"
    params: list = [ctx.tenant_id]
    if station_id:
        sql += " AND station_id = ?"
        params.append(station_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if month:
        a, b = billing.month_bounds(_month(core, month))
        sql += " AND started_at >= ? AND started_at < ?"
        params += [a, b]
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    p = parking(request)
    names = {r["id"]: r["name"] for r in core.db.all("SELECT id, name FROM station WHERE tenant_id = ?", (ctx.tenant_id,))}
    return {"sessions": [{**p.session_out(s), "station_name": names.get(s["station_id"])} for s in core.db.all(sql, tuple(params))]}


def _session(core, ctx: Ctx, sid: str):
    s = core.db.one("SELECT * FROM parking_session WHERE id = ? AND tenant_id = ?", (sid[:64], ctx.tenant_id))
    if s is None:
        raise HTTPException(404, "not_found")
    if s["status"] != "open":
        raise HTTPException(409, "session_not_open")
    return s


@router.post("/api/v1/parking/sessions/{sid}/close")
def close_session(sid: str, request: Request, ctx: Ctx = Depends(require("operator"))):
    core = core_of(request)
    s = _session(core, ctx, sid)
    now = core.clock()
    amount = billing.compute_fee(s["started_at"], now, json.loads(s["tariff"]))
    with core.db.tx() as c:
        c.execute("UPDATE parking_session SET status = 'closed', ended_at = ?, amount_cents = ?, closed_by = ? WHERE id = ?",
                  (now, amount, ctx.actor, s["id"]))
        if amount and prepaid(core, ctx.tenant_id):
            card = c.execute("SELECT * FROM card WHERE id = ?", (s["card_id"],)).fetchone()
            parking(request).book(c, card, "fee", -amount, session_id=s["id"], note="Im Portal beendet", actor=ctx.actor,
                                  source=s["source"])
    core.audit("session_closed", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=s["id"])
    return parking(request).session_out(core.db.one("SELECT * FROM parking_session WHERE id = ?", (s["id"],)))


@router.post("/api/v1/parking/sessions/{sid}/cancel")
def cancel_session(sid: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    s = _session(core, ctx, sid)
    core.db.execute("UPDATE parking_session SET status = 'cancelled', ended_at = ?, amount_cents = 0, closed_by = ? WHERE id = ?",
                    (core.clock(), ctx.actor, s["id"]))
    core.audit("session_cancelled", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=s["id"])
    return {"status": "cancelled"}


# ---------------------------------------------------------------------- Tarife
@router.get("/api/v1/billing/tariff")
def get_tariff(request: Request, ctx: Ctx = Depends(require("viewer"))):
    plan = get_plan(ctx.tenant["plan"])
    return {"tariff": billing.load_tariff(ctx.tenant["tariff"]), "enabled": plan.parking_billing,
            "payment_mode": ctx.tenant["payment_mode"]}


@router.put("/api/v1/billing/payment-mode")
def put_payment_mode(body: PaymentModeIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    """statement = Monatsaufstellung je Karte; prepaid = Guthaben je Karte, Gebühr wird beim Auschecken abgebucht."""
    core = core_of(request)
    _feature(ctx, "parking_billing")
    core.db.execute("UPDATE tenant SET payment_mode = ? WHERE id = ?", (body.mode, ctx.tenant_id))
    core.audit("payment_mode_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, detail={"mode": body.mode})
    return {"payment_mode": body.mode}


@router.put("/api/v1/billing/tariff")
def put_tariff(body: TariffIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    _feature(ctx, "parking_billing")
    t = billing.normalize_tariff(body.model_dump())
    core.db.execute("UPDATE tenant SET tariff = ? WHERE id = ?", (json.dumps(t), ctx.tenant_id))
    core.audit("tariff_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, detail=t)
    return {"tariff": t}


@router.put("/api/v1/stations/{station_id}/tariff")
def put_station_tariff(station_id: str, body: StationTariffIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    st = _station(core, ctx, station_id)
    _feature(ctx, "parking_billing")
    t = None if body.tariff is None else billing.normalize_tariff(body.tariff.model_dump())
    core.db.execute("UPDATE station SET tariff = ? WHERE id = ?", (json.dumps(t) if t else None, st["id"]))
    core.audit("station_tariff_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=st["id"], detail=t)
    return {"tariff": t}


# ---------------------------------------------------------------------- Monatsabrechnung (Parkgebühren)
@router.get("/api/v1/billing/statements")
def statements(request: Request, month: str | None = MONTH, format: str = Query("json", pattern="^(json|csv)$"),
               ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    m = _month(core, month)
    rows = parking(request).statements(ctx.tenant_id, m)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Monat", "Karte", "Parkvorgänge", "Betrag (EUR)", "bezahlt am", "simuliert"])
        for r in rows:
            w.writerow([m, _csv_safe(r["card_label"]), r["sessions"], f"{r['total_cents'] / 100:.2f}".replace(".", ","),
                        r["paid_at"] or "", "ja" if r["simulated"] else "nein"])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="parkgebuehren-{m}.csv"'})
    return {"month": m, "statements": rows, "total_cents": sum(r["total_cents"] for r in rows)}


@router.post("/api/v1/billing/statements/{card_id}/{month}/paid")
def mark_paid(card_id: str, month: str, body: PaidIn, request: Request, ctx: Ctx = Depends(require("operator"))):
    core = core_of(request)
    m = _month(core, month)
    c = _card(core, ctx, card_id)
    if body.paid:
        core.db.execute("INSERT OR REPLACE INTO statement_payment (tenant_id, card_id, month, paid_at, paid_by) VALUES (?,?,?,?,?)",
                        (ctx.tenant_id, c["id"], m, core.clock(), ctx.actor))
    else:
        core.db.execute("DELETE FROM statement_payment WHERE tenant_id = ? AND card_id = ? AND month = ?", (ctx.tenant_id, c["id"], m))
    core.audit("statement_paid" if body.paid else "statement_unpaid", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor,
               ip=ctx.ip, target=f"{c['id']}:{m}")
    return {"status": "ok"}


# ---------------------------------------------------------------------- Lizenz (Kundensicht)
@router.get("/api/v1/org/license")
def my_license(request: Request, month: str | None = MONTH, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    lic = request.app.state.licensing
    m = _month(core, month)
    invoices = core.db.all("SELECT * FROM invoice WHERE tenant_id = ? ORDER BY month DESC", (ctx.tenant_id,))
    return {"license": lic.license(ctx.tenant), "current": lic.preview(ctx.tenant, m), "invoices": [lic.invoice_out(i) for i in invoices]}


def _csv_safe(v: str) -> str:
    # Schutz vor Formel-Injektion beim Öffnen in Tabellenkalkulationen.
    return "'" + v if v and v[0] in "=+-@\t\r" else v

