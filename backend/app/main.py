"""HTTP-API der Smarten Radstation (Plan 7.2) und Auslieferung des Dashboards."""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .db import Database
from .ratelimit import RateLimiter
from .schemas import MeasurementBatchIn, MeasurementIn
from .service import StationService, UnknownSlotError

log = logging.getLogger("bike_station")
DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "dashboard"
MAX_BODY_BYTES = 64 * 1024


def _client(request: Request, settings: Settings) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _token_ok(token: str | None, allowed: frozenset[str]) -> bool:
    if not token:
        return False
    # Vergleich in konstanter Zeit; über alle Tokens iterieren.
    ok = False
    for t in allowed:
        ok |= hmac.compare_digest(token.encode(), t.encode())
    return ok


def create_app(settings: Settings | None = None, clock: Callable[[], float] = time.time) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    db.seed(settings)
    service = StationService(settings, db, clock=clock)
    write_limiter = RateLimiter(settings.write_rate_per_s, settings.write_burst)
    read_limiter = RateLimiter(settings.read_rate_per_s, settings.read_burst)

    if not settings.device_tokens:
        log.warning("BIKE_DEVICE_TOKENS ist leer – es können keine Messungen angenommen werden.")
    if not settings.admin_tokens:
        log.warning("BIKE_ADMIN_TOKENS ist leer – Admin-Funktionen sind deaktiviert.")

    async def purge_loop():
        while True:
            try:
                n = db.purge_old(clock() - settings.measurements_max_age_h * 3600)
                if n:
                    log.info("%d alte Messwerte gelöscht", n)
            except Exception:
                log.exception("Löschlauf fehlgeschlagen")
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(purge_loop())
        yield
        task.cancel()
        db.close()

    app = FastAPI(
        title="Smarte Radstation API",
        version="0.1.0",
        lifespan=lifespan,
        # Keine interaktive Doku nach außen (Plan 10.1: keine Debug-Ausgaben nach außen).
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.service = service
    app.state.db = db

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Anfrage zu groß"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Nur Feld und Fehlertyp zurückgeben, keine Eingabewerte spiegeln.
        errors = [{"loc": list(e.get("loc", ())), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse({"detail": "ungültige Eingabe", "errors": errors}, status_code=422)

    # ---------------------------------------------------------- Abhängigkeiten
    def read_limit(request: Request):
        if not read_limiter.allow(_client(request, settings)):
            raise HTTPException(429, "zu viele Anfragen")

    def require_device(request: Request):
        client = _client(request, settings)
        if not write_limiter.allow(f"w:{client}"):
            raise HTTPException(429, "zu viele Anfragen")
        if not _token_ok(_bearer(request), settings.device_tokens):
            db.audit(clock(), "rejected_write", client, request.url.path)
            log.warning("Abgewiesener Schreibversuch ohne gültiges Geräte-Token von %s", client)
            raise HTTPException(401, "nicht autorisiert", headers={"WWW-Authenticate": "Bearer"})
        return client

    def require_admin(request: Request):
        client = _client(request, settings)
        if not write_limiter.allow(f"a:{client}"):
            raise HTTPException(429, "zu viele Anfragen")
        token = _bearer(request)
        if not _token_ok(token, settings.admin_tokens):
            db.audit(clock(), "rejected_admin", client, request.url.path)
            log.warning("Abgewiesener Admin-Zugriff von %s", client)
            if token is None:
                raise HTTPException(401, "nicht autorisiert", headers={"WWW-Authenticate": "Bearer"})
            raise HTTPException(403, "keine Berechtigung")
        return client

    # ---------------------------------------------------------- Endpunkte
    @app.post("/api/v1/measurements", status_code=202)
    def post_measurement(m: MeasurementIn, client: str = Depends(require_device)):
        try:
            r = service.ingest(m)
        except UnknownSlotError as exc:
            raise HTTPException(422, str(exc))
        return {"stored": r.stored, "duplicate": r.duplicate, "alert_created": r.alert_created}

    @app.post("/api/v1/measurements/batch", status_code=202)
    def post_batch(batch: MeasurementBatchIn, client: str = Depends(require_device)):
        stored = duplicate = rejected = 0
        for m in batch.measurements:
            try:
                r = service.ingest(m)
            except UnknownSlotError:
                rejected += 1
                continue
            stored += r.stored
            duplicate += r.duplicate
        return {"stored": stored, "duplicate": duplicate, "rejected": rejected}

    @app.get("/api/v1/stations/{station_id}/status", dependencies=[Depends(read_limit)])
    def get_status(station_id: str):
        if station_id != settings.station_id:
            raise HTTPException(404, "Station nicht gefunden")
        return service.status()

    @app.get("/api/v1/occupancy/summary", dependencies=[Depends(read_limit)])
    def get_summary(hours: int = Query(24, ge=1, le=168)):
        return service.occupancy_summary(hours)

    @app.get("/api/v1/events")
    def get_events(
        limit: int = Query(100, ge=1, le=500),
        include_shadow: bool = False,
        client: str = Depends(require_admin),
    ):
        return {"events": service.events(limit, include_shadow)}

    @app.post("/api/v1/events/{event_id}/ack")
    def ack_event(event_id: int, client: str = Depends(require_admin)):
        r = service.acknowledge(event_id, client)
        if r is None:
            raise HTTPException(404, "Ereignis nicht gefunden")
        return {"acknowledged": True, "already_acknowledged": not r}

    @app.get("/health")
    def health():
        db_ok = db.ping()
        body = {"status": "ok" if db_ok else "degraded", "database": db_ok, "ai_model_available": service.ml.available}
        return JSONResponse(body, status_code=200 if db_ok else 503)

    # ---------------------------------------------------------- Dashboard
    if DASHBOARD_DIR.exists():
        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(DASHBOARD_DIR / "index.html")

        app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")

    return app


def _app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
