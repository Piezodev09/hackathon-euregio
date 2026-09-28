"""Öffentliche Stellplatz-Ansicht (QR-Code): Status, Preise, Problem melden – ohne Personendaten."""

from __future__ import annotations

from conftest import Device


def setup(env, plan="school"):
    owner = env.register()
    env.set_plan(owner, plan)
    sid, token = env.station(owner)
    return owner, sid, Device(owner, sid, token)


def test_stall_link_status_and_rotation(env):
    owner, sid, dev = setup(env)
    anon = env.client()
    assert anon.get("/api/v1/public/stall/status", headers={"X-Stall-Token": "bss_nope_nope_nope"}).status_code == 404
    link = owner.post(f"/api/v1/stations/{sid}/stall-link").json()
    assert "/s#bss_" in link["url"]
    dev.send(occupied=False)
    owner.post("/api/v1/cards", {"uid": "04AABBCCDD", "label": "Karte Geheim"})
    dev.seq += 1
    owner.c.post("/api/v1/nfc/tap", json={"station_id": sid, "sequence": dev.seq, "uid": "04AABBCCDD"},
                 headers={"Authorization": f"Bearer {dev.token}"})
    body = anon.get("/api/v1/public/stall/status", headers={"X-Stall-Token": link["token"]}).json()
    assert body["state"] == "free" and body["nfc"] is True and body["tariff"]["mode"] == "per_day"
    assert body["session"]["running"] and "card_label" not in body["session"] and "card_id" not in body["session"]
    assert "Karte Geheim" not in str(body) and "ai" not in body
    new = owner.post(f"/api/v1/stations/{sid}/stall-link").json()["token"]
    assert anon.get("/api/v1/public/stall/status", headers={"X-Stall-Token": link["token"]}).status_code == 404
    owner.patch(f"/api/v1/stations/{sid}", {"stall_view_enabled": False})
    assert anon.get("/api/v1/public/stall/status", headers={"X-Stall-Token": new}).status_code == 404


def test_report_problem_creates_event_and_is_rate_limited(env):
    owner, sid, dev = setup(env)
    tok = owner.post(f"/api/v1/stations/{sid}/stall-link").json()["token"]
    anon = env.client()
    h = {"X-Stall-Token": tok}
    assert anon.c.post("/api/v1/public/stall/report", json={"category": "damaged", "text": "Schiene locker"}, headers=h).status_code == 202
    assert anon.c.post("/api/v1/public/stall/report", json={"category": "nope"}, headers=h).status_code == 422
    assert anon.c.post("/api/v1/public/stall/report", json={"category": "other", "text": "a\x00b"}, headers=h).status_code == 422
    ev = [e for e in owner.get("/api/v1/events").json()["events"] if e["kind"] == "user_report"]
    assert ev and ev[0]["detail"] == {"category": "damaged", "text": "Schiene locker"}
    codes = [anon.c.post("/api/v1/public/stall/report", json={"category": "other"}, headers=h).status_code for _ in range(4)]
    assert 429 in codes


def test_stall_view_needs_plan_feature(env):
    owner = env.register()
    env.set_plan(owner, "free")
    sid, _ = env.station(owner)
    # Free enthält die Stellplatz-Ansicht
    assert owner.post(f"/api/v1/stations/{sid}/stall-link").status_code == 200
    assert env.client().get("/s").status_code == 200
