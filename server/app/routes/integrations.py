"""Integrations of an organisation: read-only API keys, outgoing webhooks, setup hints for Home Assistant/MQTT."""

from __future__ import annotations

import json
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator

from ..core import API_KEY_PREFIX, Ctx, core_of, require
from ..schemas import Name, Strict
from ..security import hash_token, new_id, new_token
from ..service import iso
from ..webhooks import EVENT_TYPES, KINDS, WebhookError, validate_target

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])
MAX_API_KEYS = 20
MAX_WEBHOOKS = 10
EventType = Literal["alert", "sensor_fault", "gateway_offline", "gateway_online"]


class ApiKeyIn(Strict):
    name: Name


class WebhookIn(Strict):
    name: Name
    url: Annotated[str, Field(min_length=8, max_length=1000)]
    kind: Literal["generic", "slack", "teams", "discord"] = "generic"
    events: list[EventType] = Field(default_factory=lambda: ["alert", "gateway_offline"], min_length=1, max_length=4)

    @field_validator("events")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class WebhookPatch(Strict):
    name: Name | None = None
    url: Annotated[str, Field(min_length=8, max_length=1000)] | None = None
    events: list[EventType] | None = Field(default=None, min_length=1, max_length=4)
    enabled: bool | None = None


def _check_target(core, url: str) -> None:
    try:
        validate_target(url, core.s.webhook_allow_private)
    except WebhookError as exc:
        raise HTTPException(422, {"code": "webhook_target", "reason": exc.args[0]}) from None


# ---------------------------------------------------------------------- API keys
def _key_out(r) -> dict:
    return {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "created_at": iso(r["created_at"]),
            "created_by": r["created_by"], "last_used_at": iso(r["last_used_at"]), "revoked_at": iso(r["revoked_at"])}


@router.get("/api-keys")
def list_api_keys(request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM api_key WHERE tenant_id = ? ORDER BY revoked_at IS NOT NULL, created_at DESC",
                       (ctx.tenant_id,))
    return {"api_keys": [_key_out(r) for r in rows]}


@router.post("/api-keys", status_code=201)
def create_api_key(body: ApiKeyIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    active = core.db.scalar("SELECT COUNT(*) FROM api_key WHERE tenant_id = ? AND revoked_at IS NULL", (ctx.tenant_id,))
    if active >= MAX_API_KEYS:
        raise HTTPException(409, {"code": "plan_limit", "limit": "api_keys", "value": MAX_API_KEYS})
    key = new_token(API_KEY_PREFIX)
    kid = new_id("key")
    core.db.execute("INSERT INTO api_key (id, tenant_id, name, prefix, key_hash, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
                    (kid, ctx.tenant_id, body.name, key[:12], hash_token(key), core.clock(), ctx.actor))
    core.audit("api_key_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=kid)
    # Shown exactly once, stored only as a hash.
    return {**_key_out(core.db.one("SELECT * FROM api_key WHERE id = ?", (kid,))), "key": key}


@router.delete("/api-keys/{key_id}")
def revoke_api_key(key_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    cur = core.db.execute("UPDATE api_key SET revoked_at = ? WHERE id = ? AND tenant_id = ? AND revoked_at IS NULL",
                          (core.clock(), key_id[:64], ctx.tenant_id))
    if cur.rowcount == 0:
        raise HTTPException(404, "not_found")
    core.audit("api_key_revoked", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=key_id[:64])
    return {"status": "revoked"}


# ---------------------------------------------------------------------- webhooks
def mask_url(url: str) -> str:
    """Chat webhook URLs carry their credential in the path/query: show scheme, host and the start of the path only."""
    u = urlsplit(url)
    path = u.path or "/"
    return f"{u.scheme}://{u.netloc}{path if len(path) <= 16 else path[:16] + '…'}{'?…' if u.query else ''}"


def _hook_out(r) -> dict:
    # The full URL is never returned (it may contain a token); the data export keeps it for portability.
    return {"id": r["id"], "name": r["name"], "url": mask_url(r["url"]), "kind": r["kind"], "events": json.loads(r["events"]),
            "enabled": bool(r["enabled"]), "created_at": iso(r["created_at"]), "created_by": r["created_by"],
            "last_status": r["last_status"], "last_attempt_at": iso(r["last_attempt_at"]), "last_error": r["last_error"]}


def _hook(core, ctx: Ctx, hook_id: str):
    r = core.db.one("SELECT * FROM webhook WHERE id = ? AND tenant_id = ?", (hook_id[:64], ctx.tenant_id))
    if r is None:
        raise HTTPException(404, "not_found")
    return r


@router.get("/webhooks")
def list_webhooks(request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    rows = core.db.all("SELECT * FROM webhook WHERE tenant_id = ? ORDER BY created_at", (ctx.tenant_id,))
    return {"webhooks": [_hook_out(r) for r in rows], "kinds": KINDS, "event_types": EVENT_TYPES,
            "allow_private": core.s.webhook_allow_private}


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookIn, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    if core.db.scalar("SELECT COUNT(*) FROM webhook WHERE tenant_id = ?", (ctx.tenant_id,)) >= MAX_WEBHOOKS:
        raise HTTPException(409, {"code": "plan_limit", "limit": "webhooks", "value": MAX_WEBHOOKS})
    _check_target(core, body.url)
    wid = new_id("wh")
    secret = new_token("whsec_")
    core.db.execute(
        "INSERT INTO webhook (id, tenant_id, name, url, kind, events, secret_enc, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (wid, ctx.tenant_id, body.name, body.url.strip(), body.kind, json.dumps(body.events),
         core.box.encrypt(secret, f"webhook:{wid}"), core.clock(), ctx.actor))
    core.audit("webhook_created", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=wid,
               detail={"kind": body.kind, "events": body.events})
    # The signing secret is shown once (receivers verify X-BikeStation-Signature with it).
    return {**_hook_out(core.db.one("SELECT * FROM webhook WHERE id = ?", (wid,))), "secret": secret}


@router.patch("/webhooks/{hook_id}")
def patch_webhook(hook_id: str, body: WebhookPatch, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    w = _hook(core, ctx, hook_id)
    changes = body.model_dump(exclude_none=True)
    if "url" in changes:
        _check_target(core, changes["url"])
        changes["url"] = changes["url"].strip()
    if "events" in changes:
        changes["events"] = json.dumps(sorted(set(changes["events"])))
    if "enabled" in changes:
        changes["enabled"] = int(changes["enabled"])
    for k, v in changes.items():
        core.db.execute(f"UPDATE webhook SET {k} = ? WHERE id = ?", (v, w["id"]))
    core.audit("webhook_updated", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=w["id"],
               detail={k: v for k, v in changes.items() if k != "url"})
    return _hook_out(core.db.one("SELECT * FROM webhook WHERE id = ?", (w["id"],)))


@router.delete("/webhooks/{hook_id}")
def delete_webhook(hook_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    w = _hook(core, ctx, hook_id)
    core.db.execute("DELETE FROM webhook WHERE id = ?", (w["id"],))
    core.audit("webhook_deleted", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=w["id"])
    return {"status": "deleted"}


@router.post("/webhooks/{hook_id}/test")
def test_webhook(hook_id: str, request: Request, ctx: Ctx = Depends(require("admin"))):
    core = core_of(request)
    w = _hook(core, ctx, hook_id)
    st = core.db.one("SELECT id, name FROM station WHERE tenant_id = ? ORDER BY created_at LIMIT 1", (ctx.tenant_id,))
    event = {"id": new_id("test"), "type": "test", "tenant_id": ctx.tenant_id, "occurred_at": iso(core.clock()),
             "station": {"id": st["id"] if st else None, "name": st["name"] if st else ctx.tenant["name"]},
             "slot": None, "severity": "info", "detector": None, "simulated": False, "device": None}
    ok, result = request.app.state.webhooks.deliver_now(w, event)
    core.audit("webhook_tested", tenant_id=ctx.tenant_id, user_id=ctx.user["id"], actor=ctx.actor, ip=ctx.ip, target=w["id"],
               detail={"ok": ok, "result": result})
    return {"ok": ok, "result": result}


# ---------------------------------------------------------------------- setup hints
@router.get("/info")
def integration_info(request: Request, ctx: Ctx = Depends(require("viewer"))):
    core = core_of(request)
    return {"api_base": f"{core.s.base_url}/api/v1", "addon_repository": core.s.addon_repository,
            "platform_url": core.s.base_url, "ca_fingerprint": request.app.state.ca_fingerprint,
            "webhook_allow_private": core.s.webhook_allow_private}
