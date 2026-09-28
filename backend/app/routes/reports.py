"""Berichte (Tag/Woche/Monat als JSON, CSV, PDF) und E-Mail-Benachrichtigungen je Person."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..billing import TZ
from ..core import Ctx, core_of, require
from ..notify import prefs_for
from ..reports import Reports
from ..schemas import NotifyIn

router = APIRouter(tags=["reports"])


def reports(request: Request) -> Reports:
    return request.app.state.reports


def _build(request: Request, ctx: Ctx, period: str, day: str | None) -> dict:
    core = core_of(request)
    r = reports(request)
    if not r.allowed(ctx.tenant):
        raise HTTPException(402, {"code": "plan_feature", "feature": "reports"})
    day = day or datetime.fromtimestamp(core.clock(), TZ).date().isoformat()
    try:
        return r.build(ctx.tenant, period, day)
    except ValueError:
        raise HTTPException(422, "invalid_period") from None


@router.get("/api/v1/reports")
def get_report(request: Request, period: str = Query("week", pattern="^(day|week|month)$"),
               day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
               format: str = Query("json", pattern="^(json|csv|pdf)$"), ctx: Ctx = Depends(require("viewer"))):
    rep = _build(request, ctx, period, day)
    r = reports(request)
    if format == "csv":
        return Response(r.csv(rep), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{r.filename(rep, "csv")}"'})
    if format == "pdf":
        return Response(r.pdf(rep), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{r.filename(rep, "pdf")}"'})
    return rep


@router.post("/api/v1/reports/send")
def send_report(request: Request, period: str = Query("week", pattern="^(day|week|month)$"),
                day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), ctx: Ctx = Depends(require("viewer"))):
    """Bericht sofort an die eigene E-Mail-Adresse senden (PDF im Anhang)."""
    core = core_of(request)
    rep = _build(request, ctx, period, day)
    r = reports(request)
    if not core.mail_limiter.allow(f"report:{ctx.user['id']}"):
        raise HTTPException(429, "rate_limited")
    core.mailer.send(ctx.user["email"], f"{core.s.product_name}: {rep['title']}", r.mail_text(rep),
                     attachments=[(r.filename(rep, "pdf"), r.pdf(rep), "application/pdf")])
    return {"status": "sent", "to": ctx.user["email"]}


# ---------------------------------------------------------------------- Benachrichtigungen
@router.get("/api/v1/me/notifications")
def get_notifications(request: Request, ctx: Ctx = Depends(require("viewer"))):
    return {"prefs": prefs_for(ctx.user), "email": ctx.user["email"], "role": ctx.role}


@router.put("/api/v1/me/notifications")
def put_notifications(body: NotifyIn, request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    core.db.execute("UPDATE user SET notify = ? WHERE id = ?", (json.dumps(body.model_dump()), ctx.user["id"]))
    return {"prefs": body.model_dump()}


@router.post("/api/v1/me/notifications/test")
def test_notification(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    if not core.mail_limiter.allow(f"testmail:{ctx.user['id']}"):
        raise HTTPException(429, "rate_limited")
    core.mailer.send(ctx.user["email"], f"{core.s.product_name}: Test-E-Mail",
                     "Diese Test-E-Mail zeigt: Benachrichtigungen kommen bei Ihnen an.\n\n"
                     "Einstellungen: Portal → Mein Konto → Benachrichtigungen")
    return {"status": "sent", "to": ctx.user["email"]}
