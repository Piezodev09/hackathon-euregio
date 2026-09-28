"""App-Fabrik der SaaS-Plattform: Middlewares (Sicherheit), Router, Weboberfläche."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings, load_settings
from .core import Core
from .agent_bundle import AgentBundle
from .tlsinfo import TlsInfo
from .licensing import Licensing
from .parking import Parking
from .routes import agent, auth, camera, org, parking, platform, stations
from .snapshots import Snapshots
from .service import Monitoring

log = logging.getLogger("bike_station")
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
MAX_BODY_BYTES = 64 * 1024
# Ausnahmen mit größerem Limit (Kamerabild vom Gateway)
PATH_BODY_LIMITS = {"/api/v1/agent/snapshot": 2 * 1024 * 1024}
DEVICE_PATHS = ("/api/v1/measurements", "/api/v1/agent/enroll", "/api/v1/agent/heartbeat", "/api/v1/agent/rotate-token")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
    "object-src 'none'; upgrade-insecure-requests"
)
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), "
    "payment=(), usb=(), interest-cohort=(), browsing-topics=()"
)


class BodyLimitMiddleware:
    """Begrenzt die Anfragegröße – auch bei chunked Transfer ohne Content-Length."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        max_bytes = PATH_BODY_LIMITS.get(scope.get("path", ""), self.max_bytes)
        for k, v in scope.get("headers", []):
            if k == b"content-length" and v.isdigit() and int(v) > max_bytes:
                return await JSONResponse({"detail": "payload_too_large"}, 413)(scope, receive, send)
        received = 0

        async def limited() -> Message:
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > max_bytes:
                    raise HTTPException(413, "payload_too_large")
            return msg

        await self.app(scope, limited, send)


def create_app(settings: Settings | None = None, clock: Callable[[], float] = time.time) -> FastAPI:
    settings = settings or load_settings()
    core = Core(settings, clock=clock)
    monitoring = Monitoring(core)
    licensing = Licensing(core)
    snapshots = Snapshots(core)

    async def maintenance_loop():
        while True:
            try:
                licensing.record_usage()
                d = monitoring.purge()
                d["snapshots"] = snapshots.purge()
                if any(d.values()):
                    log.info("Aufbewahrung: %s gelöscht", d)
            except Exception:
                log.exception("Wartungslauf fehlgeschlagen")
            await asyncio.sleep(600)  # alle 10 min: Aufbewahrung, Kamerabilder, Nutzungstage

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(maintenance_loop())
        yield
        task.cancel()
        core.db.close()

    app = FastAPI(title=f"{settings.product_name} API", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)  # keine öffentliche API-Doku/Debug-Ausgabe
    app.state.core = core
    app.state.monitoring = monitoring
    app.state.licensing = licensing
    app.state.snapshots = snapshots
    app.state.parking = Parking(core)
    monitoring.parking = app.state.parking
    app.state.tls = TlsInfo.load(settings.tls_cert_file) if settings.base_url.startswith("https://") else None
    app.state.agent_bundle = AgentBundle.build(settings.base_url, pin=app.state.tls.pin if app.state.tls else "")
    log.info("Agent-Paket %s bereit (sha256 %s)", app.state.agent_bundle.version, app.state.agent_bundle.sha256[:12])

    # ------------------------------------------------------------------ Middlewares
    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        request_id = secrets.token_hex(8)
        path = request.url.path
        # Origin-Prüfung für zustandsändernde Browser-Anfragen (CSRF-Schutz zusätzlich zum Token).
        if request.method in UNSAFE and path.startswith("/api/") and not path.startswith(DEVICE_PATHS):
            origin = request.headers.get("origin")
            site = request.headers.get("sec-fetch-site")
            if (origin and origin != settings.origin) or site == "cross-site":
                return JSONResponse({"detail": "origin_rejected"}, 403)
        try:
            response = await call_next(request)
        except HTTPException as exc:  # z. B. Body-Limit
            response = JSONResponse({"detail": exc.detail}, exc.status_code)
        h = response.headers
        h["X-Request-ID"] = request_id
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = PERMISSIONS_POLICY
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        h["X-Permitted-Cross-Domain-Policies"] = "none"
        h["Content-Security-Policy"] = CSP if settings.secure_cookies else CSP.replace("; upgrade-insecure-requests", "")
        if settings.secure_cookies:
            h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        if path.startswith("/api/") or path in ("/", "/app", "/display") or path.endswith(".html"):
            h["Cache-Control"] = "no-store"
        if "server" in h:
            del h["server"]
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Nur Feld und Fehlertyp, keine Eingabewerte spiegeln (keine Passwörter in Antworten/Logs).
        errors = [{"loc": [str(x) for x in e.get("loc", ())], "type": e.get("type")} for e in exc.errors()]
        return JSONResponse({"detail": "invalid_input", "errors": errors}, 422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("Unbehandelter Fehler bei %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "internal_error"}, 500)

    # ------------------------------------------------------------------ Router
    for r in (auth.router, org.router, stations.router, agent.router, platform.router, parking.router, camera.router):
        app.include_router(r)

    @app.get("/health", include_in_schema=False)
    def health():
        ok = core.db.ping()
        return JSONResponse({"status": "ok" if ok else "degraded"}, 200 if ok else 503)

    @app.get("/api/v1/meta")
    def meta():
        from .plans import PLANS

        return {"product_name": settings.product_name, "signup_enabled": settings.signup_enabled,
                "plans": [p.to_dict() for p in PLANS.values()], "password_min_length": settings.password_min_length}

    # ------------------------------------------------------------------ Weboberfläche
    if WEB_DIR.exists():
        pages = {"/": "index.html", "/app": "app.html", "/display": "display.html", "/s": "stall.html"}
        for route, file in pages.items():
            def page(file=file):
                return FileResponse(WEB_DIR / file)
            app.add_api_route(route, page, methods=["GET"], include_in_schema=False)

        @app.get("/robots.txt", include_in_schema=False)
        def robots():
            return PlainTextResponse("User-agent: *\nAllow: /$\nDisallow: /app\nDisallow: /display\nDisallow: /api/\n")

        @app.get("/.well-known/security.txt", include_in_schema=False)
        def security_txt():
            return PlainTextResponse(
                f"Contact: mailto:security@example.org\nPreferred-Languages: de, nl, en\n"
                f"Canonical: {settings.base_url}/.well-known/security.txt\n"
                "Expires: 2027-12-31T23:59:59Z\n")

        app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    return app


def _app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
