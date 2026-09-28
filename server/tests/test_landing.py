"""Landing page backend: demo mode with history, live demo feeder, demo requests (leads), legal pages,
kiosk QR code and the weekly occupancy pattern."""

from __future__ import annotations

from app.demo import DemoFeeder, create_demo, remove_demo
from app.service import LEAD_RETENTION_S

from conftest import PASSWORD
from test_auth import _enable_mfa
from test_selfhost import setup_admin

LEAD = {"name": "Lea Lead", "organisation": "Town of Heerlen", "email": "lea@example.org", "message": "Hello!\nCall us.",
        "consent": True}


# ---------------------------------------------------------------------- demo mode
def test_demo_creates_labelled_history_and_landing_demo(env):
    r = create_demo(env.core, password=PASSWORD, days=2)
    db = env.core.db
    assert db.scalar("SELECT COUNT(*) FROM station WHERE tenant_id = ?", (r.tenant_id,)) == 2
    assert r.measurements > 10_000 and r.events >= 3
    assert db.scalar("SELECT COUNT(*) FROM measurement WHERE source != 'simulated'") == 0
    assert db.scalar("SELECT COUNT(*) FROM event WHERE source != 'simulated'") == 0
    meta = env.client().get("/api/v1/meta").json()
    assert meta["demo"]["display_url"] == r.display_url and meta["demo"]["qr"].startswith("data:image/svg+xml")
    token = meta["demo"]["token"]
    status = env.client().get("/api/v1/public/display/status", headers={"X-Display-Token": token}).json()
    assert status["total"] == 6 and status["simulated_data"] is True
    # the owner can sign in and sees the weekly pattern
    owner = env.client()
    assert owner.post("/api/v1/auth/login", {"email": r.email, "password": PASSWORD}).status_code == 200
    week = owner.get(f"/api/v1/stations/{r.station_id}/occupancy/week?days=2").json()
    assert week["contains_simulated"] and len(week["matrix"]) == 7 and all(len(row) == 24 for row in week["matrix"])
    assert week["peak"]["share"] > 0.5 and week["average"] is not None
    # reset replaces the demo, remove deletes it completely
    r2 = create_demo(env.core, password=PASSWORD, days=1, reset=True)
    assert r2.tenant_id != r.tenant_id and db.scalar("SELECT COUNT(*) FROM tenant WHERE id = ?", (r.tenant_id,)) == 0
    assert remove_demo(env.core) is True
    assert env.client().get("/api/v1/meta").json()["demo"] is None
    assert db.scalar("SELECT COUNT(*) FROM measurement") == 0


def test_demo_refuses_to_overwrite_without_reset(env):
    create_demo(env.core, password=PASSWORD, days=1)
    try:
        create_demo(env.core, password=PASSWORD, days=1)
    except ValueError as exc:
        assert "reset" in str(exc)
    else:
        raise AssertionError("second demo without --reset must fail")


def test_weekly_pattern_is_tenant_isolated(env):
    r = create_demo(env.core, password=PASSWORD, days=1)
    other = env.register(org="Other")
    assert other.get(f"/api/v1/stations/{r.station_id}/occupancy/week").status_code == 404


# ---------------------------------------------------------------------- live demo feeder
def test_feeder_keeps_demo_live_and_yields_to_agents(env):
    r = create_demo(env.core, password=PASSWORD, days=1)
    feeder = DemoFeeder(env.core, env.app.state.monitoring, seed=1)
    env.clock.advance(120)  # the history is now stale ...
    token = env.client().get("/api/v1/meta").json()["demo"]["token"]
    stale = env.client().get("/api/v1/public/display/status", headers={"X-Display-Token": token}).json()
    assert stale["known_count"] == 0
    assert feeder.step() == 10  # 6 + 4 spaces
    live = env.client().get("/api/v1/public/display/status", headers={"X-Display-Token": token}).json()
    assert live["known_count"] == 6 and live["simulated_data"]
    # a paired, active agent takes precedence for its station
    env.core.db.execute("INSERT INTO device (id, tenant_id, station_id, name, token_prefix, token_hash, created_at, last_seen_at) "
                        "VALUES ('dev_x', ?, ?, 'Pi', 'bsd_x', 'h', ?, ?)", (r.tenant_id, r.station_id, env.clock(), env.clock()))
    assert feeder.step() == 4
    env.core.set_setting("demo_live", None)
    assert feeder.step() == 0


def test_feeder_shaking_raises_a_simulated_warning(env):
    create_demo(env.core, password=PASSWORD, days=1)
    feeder = DemoFeeder(env.core, env.app.state.monitoring, seed=3)
    feeder.SHAKE_EVERY_S = feeder.interval_s  # shake every round
    for sl in env.core.db.all("SELECT id FROM slot"):
        feeder.state[sl["id"]] = True  # all spaces occupied for a while
    before = env.core.db.scalar("SELECT COUNT(*) FROM event WHERE kind = 'unusual_movement' AND severity = 'warning'")
    for _ in range(3):
        env.clock.advance(10)
        feeder.step()
    after = env.core.db.scalar("SELECT COUNT(*) FROM event WHERE kind = 'unusual_movement' AND severity = 'warning'")
    assert after > before
    assert env.core.db.scalar("SELECT source FROM event ORDER BY occurred_at DESC LIMIT 1") == "simulated"


# ---------------------------------------------------------------------- demo requests
def test_lead_is_stored_and_listed_for_the_platform_admin(env):
    anon = env.client()
    r = anon.post("/api/v1/leads", LEAD)
    assert r.status_code == 202 and r.json() == {"status": "received"}
    row = env.core.db.one("SELECT * FROM lead")
    assert row["organisation"] == "Town of Heerlen" and row["message"] == "Hello!\nCall us."
    # the audit log does not copy personal data
    assert env.core.db.scalar("SELECT target FROM audit_log WHERE action = 'lead_received'") == row["id"]
    owner = env.register()
    assert owner.get("/api/v1/platform/leads").status_code == 403
    ops = setup_admin(env, "ops2@example.org")
    _enable_mfa(env, ops)
    leads = ops.get("/api/v1/platform/leads").json()["leads"]
    assert [x["email"] for x in leads] == ["lea@example.org"]
    assert ops.get("/api/v1/platform/stats").json()["open_leads"] == 1
    assert ops.patch(f"/api/v1/platform/leads/{row['id']}", {"handled": True}).status_code == 200
    assert ops.get("/api/v1/platform/leads").json()["leads"][0]["handled_by"] == "ops2@example.org"
    assert ops.delete(f"/api/v1/platform/leads/{row['id']}").status_code == 200
    assert env.core.db.scalar("SELECT COUNT(*) FROM lead") == 0


def test_lead_honeypot_and_validation(env):
    anon = env.client()
    assert anon.post("/api/v1/leads", {**LEAD, "website": "http://spam.example"}).json() == {"status": "received"}
    assert env.core.db.scalar("SELECT COUNT(*) FROM lead") == 0
    assert env.core.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'lead_spam_dropped'") == 1
    limiter = env.core.lead_limiter
    limiter._buckets.clear()  # the limit counts every request, also invalid ones
    assert anon.post("/api/v1/leads", {**LEAD, "consent": False}).status_code == 422
    assert anon.post("/api/v1/leads", {**LEAD, "email": "not-an-email"}).status_code == 422
    limiter._buckets.clear()
    assert anon.post("/api/v1/leads", {**LEAD, "message": "x" * 2001}).status_code == 422
    assert anon.post("/api/v1/leads", {**LEAD, "message": "bell\x07"}).status_code == 422
    assert anon.post("/api/v1/leads", {**LEAD, "extra": 1}).status_code == 422


def test_lead_rate_limit_and_retention(env):
    anon = env.client()
    codes = [anon.post("/api/v1/leads", {**LEAD, "email": f"l{i}@example.org"}).status_code for i in range(4)]
    assert codes[:3] == [202, 202, 202] and codes[3] == 429
    env.clock.advance(LEAD_RETENTION_S + 10)
    assert env.app.state.monitoring.purge()["leads"] == 3


def test_lead_rejected_from_other_origin(env):
    r = env.client().post("/api/v1/leads", LEAD, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# ---------------------------------------------------------------------- legal pages, kiosk QR, landing page
def test_legal_pages_show_a_notice_until_filled(env):
    r = env.client().get("/legal/imprint")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Template – not ready for public use" in r.text and "{{" not in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "not ready" in env.client().get("/legal/privacy").text


def test_legal_pages_are_filled_and_escaped(make_env):
    env = make_env(BIKE_OPERATOR_NAME="<script>alert(1)</script> GmbH", BIKE_OPERATOR_ADDRESS="Main St 1\\n52062 Aachen",
                   BIKE_CONTACT_EMAIL="info@example.org")
    page = env.client().get("/legal/imprint").text
    assert "not ready" not in page and "<script>alert" not in page and "&lt;script&gt;" in page
    assert "Main St 1<br>52062 Aachen" in page and 'href="mailto:info@example.org"' in page


def test_kiosk_qr_for_valid_display_links_only(env):
    owner = env.register()
    env.set_plan(owner, "school")
    sid, _ = env.station(owner)
    token = owner.post(f"/api/v1/stations/{sid}/display-link").json()["token"]
    r = env.client().get("/api/v1/public/display/qr", headers={"X-Display-Token": token})
    assert r.status_code == 200 and r.json()["url"].endswith(token) and r.json()["qr"].startswith("data:image/svg+xml")
    assert env.client().get("/api/v1/public/display/qr", headers={"X-Display-Token": "bsp_wrong"}).status_code == 404
    owner.patch(f"/api/v1/stations/{sid}", {"display_enabled": False})
    assert env.client().get("/api/v1/public/display/qr", headers={"X-Display-Token": token}).status_code == 404


def test_landing_page_is_served_with_seo_tags(env):
    r = env.client().get("/")
    assert r.status_code == 200
    html = r.text
    for needle in ('<meta name="description"', 'property="og:image"', '"@type": "SoftwareApplication"', 'id="lead-form"',
                   'href="/legal/imprint"', 'name="website"', 'content="http://testserver/static/img/og.png"',
                   'property="og:url" content="http://testserver/"'):
        assert needle in html, needle
    assert env.client().get("/static/img/og.png").status_code == 200
