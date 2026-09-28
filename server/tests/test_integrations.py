"""Integrations: read-only API keys, webhooks (signature, retries, SSRF protection, chat formats),
gateway offline/online events and the alert list for the agent (MQTT / Home Assistant)."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.webhooks import WebhookError, build_body, check_url, resolve, validate_target, verify_signature

from conftest import Device
from test_agent import enroll, hb, setup_station
from test_api import _vibrate

LAN = {"BIKE_WEBHOOK_ALLOW_PRIVATE": "1"}


# ---------------------------------------------------------------------- local receiver
class Receiver:
    """Tiny HTTP server that records webhook deliveries; ``statuses`` are answered in order."""

    def __init__(self, statuses=(200,)):
        self.requests: list[tuple[dict, bytes]] = []
        self.statuses = list(statuses)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                outer.requests.append(({k.lower(): v for k, v in self.headers.items()}, body))
                status = outer.statuses.pop(0) if len(outer.statuses) > 1 else outer.statuses[0]
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


def fake_resolver(ip):
    def resolver(host, port, type=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    return resolver


# ---------------------------------------------------------------------- API keys
def test_api_key_reads_only_its_tenant(env):
    owner = env.register()
    sid, token = env.station(owner)
    Device(owner, sid, token).send("A", occupied=True)
    r = owner.post("/api/v1/integrations/api-keys", {"name": "Dashboard"})
    assert r.status_code == 201
    key = r.json()["key"]
    assert key.startswith("bsk_") and r.json()["prefix"] == key[:12]
    assert env.core.db.scalar("SELECT COUNT(*) FROM api_key WHERE key_hash = ?", (key,)) == 0  # only the hash
    assert "key" not in owner.get("/api/v1/integrations/api-keys").json()["api_keys"][0]
    c = env.client().c
    h = {"Authorization": f"Bearer {key}"}
    assert c.get("/api/v1/stations", headers=h).json()["stations"][0]["id"] == sid
    assert c.get(f"/api/v1/stations/{sid}/status", headers=h).json()["slots"][0]["state"] == "occupied"
    assert c.get(f"/api/v1/stations/{sid}/occupancy/week", headers=h).status_code == 200
    assert c.get("/api/v1/events", headers=h).status_code == 200
    # read only: nothing else works with the key
    assert c.post("/api/v1/stations", json={"name": "X"}, headers=h).status_code == 401
    assert c.get(f"/api/v1/stations/{sid}/devices", headers=h).status_code == 401
    assert c.get("/api/v1/org/users", headers=h).status_code == 401
    # another tenant's key sees nothing of this station
    other = env.register(org="Other")
    other_key = other.post("/api/v1/integrations/api-keys", {"name": "x"}).json()["key"]
    assert c.get(f"/api/v1/stations/{sid}/status", headers={"Authorization": f"Bearer {other_key}"}).status_code == 404
    assert env.core.db.scalar("SELECT last_used_at FROM api_key WHERE prefix = ?", (key[:12],)) is not None
    # revoke
    kid = owner.get("/api/v1/integrations/api-keys").json()["api_keys"][0]["id"]
    assert owner.delete(f"/api/v1/integrations/api-keys/{kid}").status_code == 200
    assert c.get("/api/v1/stations", headers=h).status_code == 401
    assert env.core.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'rejected_api_key'") == 1


def test_api_keys_need_admin_and_valid_key(env):
    owner = env.register()
    env.set_plan(owner, "school")
    op = env.invite(owner, "op@example.org", "operator")
    assert op.post("/api/v1/integrations/api-keys", {"name": "x"}).status_code == 403
    assert op.get("/api/v1/integrations/webhooks").status_code == 403
    r = env.client().c.get("/api/v1/stations", headers={"Authorization": "Bearer bsk_" + "x" * 40})
    assert r.status_code == 401 and r.json()["detail"] == "invalid_api_key"


def test_api_key_of_suspended_tenant(env):
    owner = env.register()
    key = owner.post("/api/v1/integrations/api-keys", {"name": "x"}).json()["key"]
    env.core.db.execute("UPDATE tenant SET status = 'suspended'")
    assert env.client().c.get("/api/v1/stations", headers={"Authorization": f"Bearer {key}"}).status_code == 403


# ---------------------------------------------------------------------- SSRF protection
@pytest.mark.parametrize("ip,allow,ok", [
    ("93.184.216.34", False, True),
    ("10.0.0.5", False, False), ("192.168.1.10", False, False), ("127.0.0.1", False, False), ("::1", False, False),
    ("10.0.0.5", True, True), ("127.0.0.1", True, True),
    ("169.254.169.254", True, False), ("0.0.0.0", True, False), ("224.0.0.1", True, False), ("fe80::1", True, False),
])
def test_ssrf_address_rules(ip, allow, ok):
    if ok:
        assert resolve("hook.example", 443, allow, fake_resolver(ip)) == ip
    else:
        with pytest.raises(WebhookError, match="private_address"):
            resolve("hook.example", 443, allow, fake_resolver(ip))


def test_url_rules():
    with pytest.raises(WebhookError, match="https_required"):
        check_url("http://hook.example/x", False)
    for bad in ("ftp://x/y", "https://user:pw@hook.example/", "javascript:alert(1)", "https:///nohost"):
        with pytest.raises(WebhookError, match="invalid_url"):
            check_url(bad, True)
    # plain http only to private addresses, even when private targets are allowed
    with pytest.raises(WebhookError, match="https_required"):
        validate_target("http://hook.example/x", True, fake_resolver("93.184.216.34"))
    validate_target("http://homeassistant.local:8123/api/webhook/abc", True, fake_resolver("192.168.1.20"))


def test_webhook_targets_checked_on_create(env):
    owner = env.register()
    for url, reason in (("http://127.0.0.1:9/x", "https_required"), ("https://127.0.0.1:9/x", "private_address")):
        r = owner.post("/api/v1/integrations/webhooks", {"name": "x", "url": url, "kind": "generic", "events": ["alert"]})
        assert r.status_code == 422 and r.json()["detail"]["reason"] == reason


# ---------------------------------------------------------------------- delivery
def _alert(env, owner):
    sid, token = env.station(owner)
    dev = Device(owner, sid, token)
    dev.send("A", occupied=True)
    env.clock.advance(20)
    assert _vibrate(dev, env.clock, 3) is True
    return sid, dev


def test_alert_is_delivered_signed(make_env, receiver):
    env = make_env(**LAN)
    owner = env.register()
    r = owner.post("/api/v1/integrations/webhooks", {"name": "Home Assistant", "url": receiver.url, "kind": "generic",
                                                     "events": ["alert", "sensor_fault"]})
    assert r.status_code == 201
    secret = r.json()["secret"]
    assert secret.startswith("whsec_") and "secret" not in owner.get("/api/v1/integrations/webhooks").json()["webhooks"][0]
    assert env.core.db.scalar("SELECT secret_enc FROM webhook") != secret.encode()
    _alert(env, owner)
    assert env.app.state.webhooks.wait_idle()
    alerts = [(h, b) for h, b in receiver.requests if h["x-bikestation-event"] == "alert"]
    assert len(alerts) == 1
    headers, body = alerts[0]
    ev = json.loads(body)
    assert ev["type"] == "alert" and ev["slot"] == "A" and ev["station"]["name"] == "Station 1" and "tenant_id" not in ev
    assert "suspicion, not proof" in ev["text"]
    assert verify_signature(secret, body, headers["x-bikestation-signature"], env.clock())
    assert not verify_signature("whsec_wrong", body, headers["x-bikestation-signature"], env.clock())
    assert not verify_signature(secret, body, headers["x-bikestation-signature"], env.clock() + 3600)  # replay window
    assert owner.get("/api/v1/integrations/webhooks").json()["webhooks"][0]["last_status"] == "ok"


def test_webhook_retries_and_gives_up(make_env):
    env = make_env(**LAN)
    env.app.state.webhooks.backoff_s = (0.05, 0.05)
    owner = env.register()
    flaky = Receiver(statuses=(500, 503, 200))
    dead = Receiver(statuses=(500,))
    try:
        for name, rec in (("flaky", flaky), ("dead", dead)):
            owner.post("/api/v1/integrations/webhooks", {"name": name, "url": rec.url, "events": ["alert"]})
        _alert(env, owner)
        assert env.app.state.webhooks.wait_idle()
        assert len(flaky.requests) == 3 and len(dead.requests) == 3
        hooks = {w["name"]: w for w in owner.get("/api/v1/integrations/webhooks").json()["webhooks"]}
        assert hooks["flaky"]["last_status"] == "ok"
        assert hooks["dead"]["last_status"] == "error" and hooks["dead"]["last_error"] == "HTTP 500"
    finally:
        flaky.close()
        dead.close()


def test_disabled_or_unsubscribed_webhooks_stay_quiet(make_env, receiver):
    env = make_env(**LAN)
    owner = env.register()
    wid = owner.post("/api/v1/integrations/webhooks", {"name": "x", "url": receiver.url, "events": ["gateway_offline"]}).json()["id"]
    _, dev = _alert(env, owner)
    assert env.app.state.webhooks.wait_idle() and receiver.requests == []
    assert owner.patch(f"/api/v1/integrations/webhooks/{wid}", {"events": ["alert"], "enabled": False}).status_code == 200
    env.clock.advance(60)
    assert _vibrate(dev, env.clock, 3) is True
    assert env.app.state.webhooks.wait_idle() and receiver.requests == []


def test_test_button_and_tenant_isolation(make_env, receiver):
    env = make_env(**LAN)
    owner = env.register()
    wid = owner.post("/api/v1/integrations/webhooks", {"name": "Teams", "url": receiver.url, "kind": "teams",
                                                        "events": ["alert"]}).json()["id"]
    r = owner.post(f"/api/v1/integrations/webhooks/{wid}/test")
    assert r.json() == {"ok": True, "result": "HTTP 200"}
    assert json.loads(receiver.requests[0][1]) == {"text": "Test message from Smart Bike Station (School A). Your webhook works."}
    other = env.register(org="Other")
    assert other.patch(f"/api/v1/integrations/webhooks/{wid}", {"enabled": False}).status_code == 404
    assert other.delete(f"/api/v1/integrations/webhooks/{wid}").status_code == 404
    assert owner.delete(f"/api/v1/integrations/webhooks/{wid}").status_code == 200


def test_chat_formats():
    ev = {"type": "alert", "station": {"id": "st_1", "name": "Yard"}, "slot": "B", "simulated": True, "id": "evt_1"}
    assert json.loads(build_body("slack", ev))["text"].startswith("⚠ Unusual movement at Yard – space B")
    assert "[simulated]" in json.loads(build_body("teams", ev))["text"]
    assert set(json.loads(build_body("discord", ev))) == {"content"}
    generic = json.loads(build_body("generic", ev))
    assert generic["type"] == "alert" and generic["slot"] == "B" and "text" in generic


# ---------------------------------------------------------------------- gateway offline / online
def test_gateway_offline_raises_one_event_and_online_info(make_env, receiver):
    env = make_env(**LAN)
    owner, sid = setup_station(env)
    owner.post("/api/v1/integrations/webhooks", {"name": "x", "url": receiver.url,
                                                "events": ["gateway_offline", "gateway_online"]})
    _, r = enroll(env, owner, sid)
    token = r.json()["token"]
    assert hb(env, token).status_code == 200
    mon = env.app.state.monitoring
    env.clock.advance(120)
    assert mon.check_gateways() == {"offline": 0, "online": 0}
    env.clock.advance(120)
    assert mon.check_gateways()["offline"] == 1
    assert mon.check_gateways()["offline"] == 0  # exactly one event per outage
    events = owner.get("/api/v1/events?open_only=true").json()["events"]
    assert [(e["kind"], e["slot_id"], e["device"]) for e in events] == [("gateway_offline", None, "raspi-yard")]
    # the offline warning can be acknowledged like any warning
    assert owner.post(f"/api/v1/events/{events[0]['id']}/ack").status_code == 200
    assert hb(env, token).status_code == 200  # back again
    evs = owner.get("/api/v1/events").json()["events"]
    assert [e["kind"] for e in evs] == ["gateway_online", "gateway_offline"]
    assert evs[0]["severity"] == "info"
    assert env.core.db.scalar("SELECT offline_notified FROM device") == 0
    assert env.app.state.webhooks.wait_idle()
    assert [h["x-bikestation-event"] for h, _ in receiver.requests] == ["gateway_offline", "gateway_online"]
    assert "Gateway offline at Yard" in json.loads(receiver.requests[0][1])["text"]
    # second outage, not acknowledged: closed automatically when the gateway returns
    env.clock.advance(200)
    assert mon.check_gateways()["offline"] == 1
    assert len(owner.get("/api/v1/events?open_only=true").json()["events"]) == 1
    assert hb(env, token).status_code == 200
    assert owner.get("/api/v1/events?open_only=true").json()["events"] == []
    latest_offline = [e for e in owner.get("/api/v1/events").json()["events"] if e["kind"] == "gateway_offline"][0]
    assert latest_offline["acknowledged_by"] == "system" and latest_offline["acknowledged_at"]


def test_revoked_or_unpaired_devices_never_go_offline(env):
    owner, sid = setup_station(env)
    sid2, _ = env.station(owner)  # manual token, never sends a heartbeat
    _, r = enroll(env, owner, sid)
    hb(env, r.json()["token"])
    did = env.core.db.scalar("SELECT id FROM device WHERE enrolled_at IS NOT NULL")
    owner.delete(f"/api/v1/stations/{sid}/devices/{did}")
    env.clock.advance(1000)
    assert env.app.state.monitoring.check_gateways()["offline"] == 0
    del sid2


# ---------------------------------------------------------------------- agent: alerts and self-update flag
def test_heartbeat_lists_open_alerts_and_respects_self_update(env):
    owner, sid = setup_station(env)
    _, r = enroll(env, owner, sid)
    token = r.json()["token"]
    assert hb(env, token).json()["alerts"] == []
    dev = Device(owner, sid, token)
    dev.send("A", occupied=True)
    env.clock.advance(20)
    assert _vibrate(dev, env.clock, 3) is True
    assert hb(env, token).json()["alerts"] == ["A"]
    eid = owner.get("/api/v1/events?open_only=true").json()["events"][0]["id"]
    owner.post(f"/api/v1/events/{eid}/ack")
    assert hb(env, token).json()["alerts"] == []
    # container / add-on agents are never offered self-updates
    assert hb(env, token, agent_version="0.1.0").json()["update"] is not None
    assert hb(env, token, agent_version="0.1.0", self_update=False).json()["update"] is None


def test_export_contains_integrations_without_secrets(make_env, receiver):
    env = make_env(**LAN)
    owner = env.register()
    owner.post("/api/v1/integrations/api-keys", {"name": "Dashboard"})
    owner.post("/api/v1/integrations/webhooks", {"name": "x", "url": receiver.url, "events": ["alert"]})
    data = owner.get("/api/v1/org/export").json()
    assert data["api_keys"][0]["name"] == "Dashboard" and "key_hash" not in data["api_keys"][0]
    assert data["webhooks"][0]["url"] == receiver.url and "secret_enc" not in data["webhooks"][0]


def test_webhook_urls_are_masked_in_responses():
    from app.routes.integrations import mask_url

    slack = "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXXXXXXXXXX"
    assert mask_url(slack) == "https://hooks.slack.com/services/T000/B…"
    assert mask_url("http://192.168.1.20:8123/api/webhook/abc") == "http://192.168.1.20:8123/api/webhook/abc"
    assert mask_url("https://example.org/in?token=secret") == "https://example.org/in?…"

