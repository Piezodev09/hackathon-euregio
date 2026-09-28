"""Public endpoints behind the landing page: demo requests (leads), legal pages, the live demo station."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import AfterValidator, Field

from ..core import client_ip, core_of, limit
from ..qr import qr_data_uri
from ..schemas import Email, Locale, Name, Strict
from ..security import new_id
from .stations import display_station

router = APIRouter(tags=["public"])
lead_limit = Depends(limit("lead_limiter", "lead:"))
LEGAL_DIR = Path(__file__).resolve().parents[3] / "web" / "legal"
DEMO_SETTING = "landing_demo_display_token"
_CONTROL_EXCEPT_NEWLINE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _message(v: str) -> str:
    v = v.strip()
    if _CONTROL_EXCEPT_NEWLINE.search(v):
        raise ValueError("control_characters")
    return v


class LeadIn(Strict):
    name: Name
    organisation: Name
    email: Email
    message: Annotated[str, Field(max_length=2000), AfterValidator(_message)] = ""
    consent: Literal[True]
    # Honeypot: invisible for people, bots fill it in. Filled -> silently dropped.
    website: Annotated[str, Field(max_length=200)] = ""
    locale: Locale = "en"


@router.post("/api/v1/leads", status_code=202, dependencies=[lead_limit])
def create_lead(body: LeadIn, request: Request):
    """Demo request from the landing page. Always the same answer (bots learn nothing)."""
    core = core_of(request)
    if body.website.strip():
        core.audit("lead_spam_dropped", ip=client_ip(request))
        return {"status": "received"}
    lid = new_id("lead")
    core.db.execute(
        "INSERT INTO lead (id, name, organisation, email, message, locale, created_at) VALUES (?,?,?,?,?,?,?)",
        (lid, body.name, body.organisation, body.email, body.message, body.locale, core.clock()))
    core.audit("lead_received", target=lid)
    if core.s.contact_email:
        core.mailer.send(core.s.contact_email, f"{core.s.product_name}: new demo request",
                         f"A new demo request from {body.organisation} is waiting in the platform view (Leads).")
    return {"status": "received"}


# ---------------------------------------------------------------------- live demo on the landing page
def demo_token(core) -> str:
    return core.s.landing_demo_display_token or (
        core.db.scalar("SELECT value FROM setting WHERE key = ?", (DEMO_SETTING,)) or "")


def demo_info(core) -> dict | None:
    """Display link + QR code of the demo station, if one is configured and publicly enabled."""
    token = demo_token(core)
    if not token or display_station(core, token) is None:
        return None
    url = f"{core.s.base_url}/display#{token}"
    return {"token": token, "display_url": url, "qr": qr_data_uri(url, scale=4)}


# ---------------------------------------------------------------------- legal pages
def _legal_page(request: Request, name: str) -> HTMLResponse:
    core = core_of(request)
    s = core.s
    missing = [label for label, value in (("operator name", s.operator_name), ("address", s.operator_address),
                                          ("contact e-mail", s.contact_email)) if not value]
    notice = ""
    if missing:
        notice = ('<div class="legal-missing" role="alert"><strong>Template – not ready for public use.</strong> '
                  f"The operator still has to fill in: {html.escape(', '.join(missing))} "
                  "(<code>[legal]</code> in <code>server/config.toml</code> or <code>BIKE_OPERATOR_NAME</code>, "
                  "<code>BIKE_OPERATOR_ADDRESS</code>, <code>BIKE_CONTACT_EMAIL</code>).</div>")
    email = html.escape(s.contact_email or "[contact e-mail]")
    values = {
        "product_name": html.escape(s.product_name),
        "operator_name": html.escape(s.operator_name or "[operator name]"),
        "operator_address": "<br>".join(html.escape(line) for line in
                                    (s.operator_address or "[postal address]").replace("\\n", "\n").splitlines()),
        "contact_email": email,
        "contact_link": f'<a href="mailto:{email}">{email}</a>' if s.contact_email else email,
        "missing_notice": notice,
        "base_url": html.escape(s.base_url),
    }
    page = (LEGAL_DIR / f"{name}.html").read_text(encoding="utf-8")
    for key, value in values.items():
        page = page.replace("{{" + key + "}}", value)
    return HTMLResponse(page)


@router.get("/legal/imprint", include_in_schema=False)
def imprint(request: Request):
    return _legal_page(request, "imprint")


@router.get("/legal/privacy", include_in_schema=False)
def privacy(request: Request):
    return _legal_page(request, "privacy")

