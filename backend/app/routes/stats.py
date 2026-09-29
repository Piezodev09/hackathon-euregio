"""Statistiken (Zeitraum 7/30/90 Tage) und Tageskennzahlen für die Übersicht."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..core import Ctx, core_of, require

router = APIRouter(tags=["stats"])


@router.get("/api/v1/stats")
def get_stats(request: Request, days: int = Query(30), station_id: str | None = Query(None, max_length=64),
              ctx: Ctx = Depends(require("viewer"))):
    if days not in (7, 30, 90):
        raise HTTPException(422, "invalid_days")
    core = core_of(request)
    if station_id and not core.db.one("SELECT 1 FROM station WHERE id = ? AND tenant_id = ?", (station_id, ctx.tenant_id)):
        raise HTTPException(404, "not_found")
    return request.app.state.stats.build(ctx.tenant, days, station_id)


@router.get("/api/v1/stats/today")
def get_today(request: Request, ctx: Ctx = Depends(require("viewer"))):
    return request.app.state.stats.today(ctx.tenant)
