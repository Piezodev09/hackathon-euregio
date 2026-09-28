"""NFC-Ein-/Auschecken, Parkgebühren, Monatsabrechnung, Lizenz und Rechnungen."""

from __future__ import annotations

from datetime import datetime

import pytest

from app import billing
from conftest import PASSWORD, Device

UID = "04A1B2C3D4"


def ts(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=billing.TZ).timestamp()


# ---------------------------------------------------------------------- Gebühren (reine Funktion)
@pytest.mark.parametrize("start,end,tariff,expected", [
    ("2026-09-28 08:00", "2026-09-28 08:10", {"mode": "per_day", "price_cents": 50, "free_minutes": 15}, 0),
    ("2026-09-28 08:00", "2026-09-28 16:00", {"mode": "per_day", "price_cents": 50, "free_minutes": 15}, 50),
    ("2026-09-28 20:00", "2026-09-30 07:00", {"mode": "per_day", "price_cents": 50, "free_minutes": 0}, 150),
    ("2026-09-28 08:00", "2026-09-28 10:01", {"mode": "per_hour", "price_cents": 20, "free_minutes": 0}, 60),
    ("2026-09-28 08:00", "2026-09-28 10:00", {"mode": "per_hour", "price_cents": 20, "free_minutes": 60}, 20),
    ("2026-09-28 06:00", "2026-09-28 23:00", {"mode": "per_hour", "price_cents": 20, "free_minutes": 0, "daily_cap_cents": 100}, 100),
    ("2026-09-28 08:00", "2026-09-29 08:00", {"mode": "flat", "price_cents": 99, "free_minutes": 0}, 99),
    ("2026-09-28 08:00", "2026-09-29 08:00", {"mode": "free", "price_cents": 99}, 0),
])
def test_compute_fee(start, end, tariff, expected):
    assert billing.compute_fee(ts(start), ts(end), tariff) == expected


def test_month_helpers():
    a, b = billing.month_bounds("2026-12")
    assert billing.day_str(a) == "2026-12-01" and billing.day_str(b) == "2027-01-01"
    assert len(billing.days_of_month("2026-02")) == 28
    with pytest.raises(ValueError):
        billing.month_bounds("2026-13")


# ---------------------------------------------------------------------- NFC-Ablauf
@pytest.fixture
def school(env):
    owner = env.register()
    env.set_plan(owner, "school")
    sid, token = env.station(owner)
    return env, owner, Device(owner, sid, token)


def tap(dev: Device, uid=UID, **extra):
    dev.seq += 1
    body = {"station_id": dev.sid, "sequence": dev.seq, "uid": uid, **extra}
    return dev.api.c.post("/api/v1/nfc/tap", json=body, headers={"Authorization": f"Bearer {dev.token}"})


def test_unknown_card_is_learned_then_check_in_out_with_fee(school):
    env, owner, dev = school
    r = tap(dev)
    assert r.status_code == 200 and r.json()["result"] == "unknown_card"
    cards = owner.get("/api/v1/cards").json()["cards"]
    assert len(cards) == 1 and cards[0]["status"] == "pending"
    # Roh-UID nirgends gespeichert
    assert all(UID not in str(tuple(row)) for row in env.core.db.all("SELECT * FROM card"))
    assert owner.patch(f"/api/v1/cards/{cards[0]['id']}", {"label": "Karte 7b", "status": "active"}).status_code == 200

    assert owner.put("/api/v1/billing/tariff", {"mode": "per_hour", "price_cents": 50, "free_minutes": 15}).status_code == 200
    assert tap(dev).json()["result"] == "checked_in"
    st = owner.get(f"/api/v1/stations/{dev.sid}/status").json()
    assert st["session"]["card_label"] == "Karte 7b" and st["session"]["running"] and st["last_tap"]["result"] == "checked_in"
    env.clock.advance(3600)  # 60 min - 15 Freiminuten = 1 angefangene Stunde
    r = tap(dev).json()
    assert r == {"result": "checked_out", "amount_cents": 50}
    assert owner.get(f"/api/v1/stations/{dev.sid}/status").json()["session"] is None
    stm = owner.get("/api/v1/billing/statements").json()
    assert stm["total_cents"] == 50 and stm["statements"][0]["card_label"] == "Karte 7b"
    csv = owner.get("/api/v1/billing/statements?format=csv").text
    assert "Karte 7b" in csv and "0,50" in csv


def test_other_card_duplicates_blocked_and_expired(school):
    env, owner, dev = school
    a = owner.post("/api/v1/cards", {"uid": "04:11:22:33", "label": "A"}).json()
    owner.post("/api/v1/cards", {"uid": "04:44:55:66", "label": "B"})
    assert tap(dev, "04112233").json()["result"] == "checked_in"
    assert tap(dev, "04445566").json()["result"] == "occupied_by_other"
    dev.seq -= 1
    assert tap(dev, "04445566").json()["result"] == "duplicate"
    assert tap(dev, "04445566", age_ms=120_000).json()["result"] == "expired"
    owner.patch(f"/api/v1/cards/{a['id']}", {"status": "blocked"})
    assert tap(dev, "04112233").json()["result"] == "blocked"
    assert tap(dev, "nothex!!").status_code == 422


def test_manual_close_cancel_and_roles(school):
    env, owner, dev = school
    owner.post("/api/v1/cards", {"uid": UID, "label": "X"})
    tap(dev)
    sid = owner.get("/api/v1/parking/sessions?status=open").json()["sessions"][0]["id"]
    viewer = env.invite(owner, "v@example.org", "viewer")
    assert viewer.post(f"/api/v1/parking/sessions/{sid}/close").status_code == 403
    op = env.invite(owner, "o@example.org", "operator")
    assert op.post(f"/api/v1/parking/sessions/{sid}/close").json()["status"] == "closed"
    assert op.post(f"/api/v1/parking/sessions/{sid}/close").status_code == 409


def test_nfc_tenant_isolation(env):
    a = env.register(org="A")
    env.set_plan(a, "school")
    b = env.register(org="B")
    env.set_plan(b, "school")
    sid_a, tok_a = env.station(a)
    sid_b, _ = env.station(b)
    dev = Device(a, sid_a, tok_a)
    assert tap(dev).json()["result"] == "unknown_card"
    assert b.get("/api/v1/cards").json()["cards"] == []
    card = a.get("/api/v1/cards").json()["cards"][0]
    assert b.patch(f"/api/v1/cards/{card['id']}", {"label": "x"}).status_code == 404
    # Gleiche UID bei B = andere Karte (HMAC je Organisation)
    b.post("/api/v1/cards", {"uid": UID, "label": "B"})
    hashes = {r[0] for r in env.core.db.all("SELECT uid_hmac FROM card")}
    assert len(hashes) == 2
    dev.seq += 1
    r = a.c.post("/api/v1/nfc/tap", json={"station_id": sid_b, "sequence": dev.seq, "uid": UID},
                 headers={"Authorization": f"Bearer {tok_a}"})
    assert r.status_code == 403


def test_free_plan_nfc_without_fees(env):
    owner = env.register()
    sid, token = env.station(owner)
    dev = Device(owner, sid, token)
    owner.post("/api/v1/cards", {"uid": UID, "label": "X"})
    assert tap(dev).json()["result"] == "checked_in"
    env.clock.advance(3000)  # < Sitzungs-Leerlaufzeit
    assert tap(dev).json() == {"result": "checked_out", "amount_cents": 0}
    assert owner.put("/api/v1/billing/tariff", {"mode": "per_day", "price_cents": 50}).status_code == 402


def test_maintenance_blocks_taps_and_is_in_status(school):
    env, owner, dev = school
    owner.patch(f"/api/v1/stations/{dev.sid}", {"maintenance": True})
    assert tap(dev).json()["result"] == "maintenance"
    assert owner.get(f"/api/v1/stations/{dev.sid}/status").json()["maintenance"] is True


# ---------------------------------------------------------------------- Lizenz & Rechnungen
def platform_admin(env):
    from app.security import hash_password, new_id

    now = env.clock()
    env.core.db.execute(
        "INSERT INTO user (id, tenant_id, email, name, role, password_hash, email_verified_at, is_platform_admin, created_at, "
        "password_changed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (new_id("usr"), None, "ops@example.org", "Ops", "platform", hash_password(PASSWORD, env.core.s.scrypt_n), now, 1, now, now))
    object.__setattr__(env.core.s, "platform_admin_mfa_required", False)  # 2FA-Einrichtung ist nicht Teil dieses Tests
    api = env.client()
    assert api.post("/api/v1/auth/login", {"email": "ops@example.org", "password": PASSWORD}).status_code == 200
    return api


def test_license_invoice_per_stall_day(env):
    owner = env.register(org="Schule X")
    env.set_plan(owner, "school")
    env.station(owner, name="A")
    env.station(owner, name="B")
    lic = owner.get("/api/v1/org/license").json()
    assert lic["license"]["trial"] is True and lic["license"]["price_per_stall_day_cents"] == 20
    month = lic["current"]["month"]
    tid = owner.me().json()["tenant"]["id"]
    env.core.db.execute("INSERT INTO usage_day (tenant_id, day, stalls) VALUES (?,?,?)", (tid, f"{month}-01", 5))
    p = owner.get("/api/v1/org/license").json()["current"]
    assert p["stall_days"] >= 5 and p["total_cents"] == 900 + p["stall_days"] * 20
    ops = platform_admin(env)
    r = ops.get(f"/api/v1/platform/invoices?month={month}")
    assert r.status_code == 200, r.text
    row = [x for x in r.json()["rows"] if x["tenant_id"] == tid][0]
    assert row["total_cents"] == p["total_cents"] and row["invoice"] is None
    inv = ops.post("/api/v1/platform/invoices", {"tenant_id": tid, "month": month}).json()
    assert inv["status"] == "open" and inv["total_cents"] == p["total_cents"]
    assert ops.patch(f"/api/v1/platform/invoices/{inv['id']}", {"status": "paid"}).json()["paid_at"]
    lic2 = ops.put(f"/api/v1/platform/tenants/{tid}/license", {"valid_until": "2027-07-31", "price_per_stall_day_cents": 12,
                                                               "notes": "Schulvertrag"}).json()
    assert lic2["price_per_stall_day_cents"] == 12 and lic2["custom"] and not lic2["trial"]
    assert "Schule X" in ops.get(f"/api/v1/platform/invoices?month={month}&format=csv").text
    assert owner.get("/api/v1/platform/invoices").status_code == 403


def test_license_invoice_issue_and_custom_price(env):
    from app.licensing import Licensing

    owner = env.register(org="Schule Y")
    env.set_plan(owner, "school")
    env.station(owner)
    tid = owner.me().json()["tenant"]["id"]
    lic = Licensing(env.core)
    t = env.core.db.one("SELECT * FROM tenant WHERE id = ?", (tid,))
    month = billing.current_month(env.clock())
    env.core.db.execute("INSERT INTO license (tenant_id, valid_from, price_per_stall_day_cents, base_month_cents) VALUES (?,?,?,?)",
                        (tid, env.clock(), 10, 0))
    p = lic.preview(t, month)
    assert p["license"]["custom"] and p["total_cents"] == p["stall_days"] * 10
    inv = lic.issue(t, month)
    assert inv["number"].startswith("SBB-") and inv["total_cents"] == p["total_cents"]
    assert lic.issue(t, month)["id"] == inv["id"]  # festgeschrieben, nicht doppelt
    assert owner.get("/api/v1/org/license").json()["invoices"][0]["number"] == inv["number"]
    assert lic.record_usage() >= 1
