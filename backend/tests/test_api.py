"""Abnahmetests aus Plan Kapitel 14 (ohne Hardware), jetzt im Mandantenbetrieb."""

from __future__ import annotations

import pytest

from conftest import Device


@pytest.fixture
def setup(env):
    owner = env.register()
    sid, token = env.station(owner)
    return env, owner, Device(owner, sid, token)


def status(api, sid):
    r = api.get(f"/api/v1/stations/{sid}/status")
    assert r.status_code == 200, r.text
    return {s["slot_id"]: s for s in r.json()["slots"]}, r.json()


def test_no_data_is_unknown_never_free(setup):
    env, owner, dev = setup
    slots, body = status(owner, dev.sid)
    assert all(s["state"] == "unknown" for s in slots.values())
    assert body["free_count"] == 0 and body["recommendation"] is None


def test_T01_T02_T03_free_occupied_recommendation(setup):
    env, owner, dev = setup
    assert dev.send("A", occupied=True).status_code == 202
    dev.send("B", occupied=False)
    dev.send("C", occupied=False)
    slots, body = status(owner, dev.sid)
    assert slots["A"]["state"] == "occupied" and slots["B"]["state"] == "free"
    assert body["free_count"] == 2 and body["recommendation"] == "B"


def test_T07_sensor_error_and_stale_are_unknown(setup):
    env, owner, dev = setup
    dev.send("A", occupied=False)
    dev.send("A", occupied=None, state="error")
    dev.send("B", occupied=False)
    slots, _ = status(owner, dev.sid)
    assert slots["A"]["unknown_reason"] == "sensor_error"
    env.clock.advance(31)
    slots, body = status(owner, dev.sid)
    assert slots["B"]["state"] == "unknown" and slots["B"]["unknown_reason"] == "stale"
    assert body["free_count"] == 0


def test_duplicate_sequence_is_ignored(setup):
    env, owner, dev = setup
    assert dev.send("A", occupied=True).json()["stored"] is True
    dev.seq -= 1
    assert dev.send("A", occupied=False).json()["duplicate"] is True


def test_T09_device_write_without_or_with_bad_token_rejected_and_logged(setup):
    env, owner, dev = setup
    assert dev.send(token=None).status_code == 401
    assert dev.send(token="bsd_wrong_token_wrong_token").status_code == 401
    assert env.core.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'rejected_device_write'") == 2


def test_revoked_device_token_rejected(setup):
    env, owner, dev = setup
    dev_id = owner.get(f"/api/v1/stations/{dev.sid}/devices").json()["devices"][0]["id"]
    assert owner.delete(f"/api/v1/stations/{dev.sid}/devices/{dev_id}").status_code == 200
    assert dev.send().status_code == 401


def test_device_tokens_are_stored_hashed(setup):
    env, owner, dev = setup
    rows = env.core.db.all("SELECT token_hash, token_prefix FROM device")
    assert all(dev.token not in (r[0] or "") for r in rows)
    assert owner.get(f"/api/v1/stations/{dev.sid}/devices").json()["devices"][0].get("token") is None


def test_validation_rejects_bad_input(setup):
    env, owner, dev = setup
    assert dev.send(vib=5000).status_code == 422
    assert dev.send(slot="Z").status_code == 422
    assert dev.send(occupied=None, state="ok").status_code == 422
    assert dev.send(evil="x").status_code == 422
    assert dev.send(slot="A; DROP TABLE slot").status_code == 422


def _vibrate(dev, clock, n, vib=600, step=0.5, slot="A"):
    created = False
    for _ in range(n):
        clock.advance(step)
        created |= dev.send(slot, occupied=True, vib=vib).json()["alert_created"]
    return created


def test_T05_T06_alert_after_grace_but_not_while_parking(setup):
    env, owner, dev = setup
    dev.send("A", occupied=True)
    assert _vibrate(dev, env.clock, 5) is False  # Schonzeit
    env.clock.advance(20)
    assert _vibrate(dev, env.clock, 3) is True
    slots, _ = status(owner, dev.sid)
    assert slots["A"]["state"] == "occupied"
    assert slots["A"]["alert"]["kind"] == "unusual_movement"


def test_single_bump_no_alert_and_old_buffered_data_ignored(setup):
    env, owner, dev = setup
    dev.send("A", occupied=True)
    env.clock.advance(60)
    assert _vibrate(dev, env.clock, 1) is False
    created = False
    for i in range(5):
        created |= dev.send("A", occupied=True, vib=800, age_ms=40_000 - i * 100).json()["alert_created"]
    assert created is False


def test_cooldown_ack_and_roles(setup):
    env, owner, dev = setup
    dev.send("A", occupied=True)
    env.clock.advance(20)
    assert _vibrate(dev, env.clock, 3)
    assert not _vibrate(dev, env.clock, 3)
    env.set_plan(owner, "school")
    viewer = env.invite(owner, "viewer@example.org", "viewer")
    events = viewer.get("/api/v1/events").json()["events"]
    alert = [e for e in events if e["kind"] == "unusual_movement"][0]
    assert alert["detail"]["ml"] is None  # beim Auslösen Free-Tarif: keine KI
    assert viewer.post(f"/api/v1/events/{alert['id']}/ack").status_code == 403
    operator = env.invite(owner, "op@example.org", "operator")
    r = operator.post(f"/api/v1/events/{alert['id']}/ack")
    assert r.json() == {"acknowledged": True, "already_acknowledged": False}
    slots, _ = status(owner, dev.sid)
    assert slots["A"]["alert"] is None


def test_T13_summary_time_weighted_and_labels_simulated(setup):
    env, owner, dev = setup
    env.clock.t = 1_700_000_000 - (1_700_000_000 % 3600)
    for _ in range(6):
        dev.send("A", occupied=True, source="simulated")
        env.clock.advance(10)
    for _ in range(6):
        dev.send("A", occupied=False, source="simulated")
        env.clock.advance(10)
    body = owner.get(f"/api/v1/stations/{dev.sid}/occupancy?hours=1").json()
    assert body["contains_simulated"] is True and body["contains_live"] is False
    assert body["buckets"][-1]["occupancy"]["A"] == pytest.approx(0.5, abs=0.01)
    assert body["buckets"][-1]["occupancy"]["B"] is None


def test_batch_endpoint(setup):
    env, owner, dev = setup
    ms = [{"station_id": dev.sid, "slot_id": "A", "sequence": 1, "occupied": True, "sensor_state": "ok"},
          {"station_id": dev.sid, "slot_id": "A", "sequence": 1, "occupied": True, "sensor_state": "ok"},
          {"station_id": dev.sid, "slot_id": "Q", "sequence": 2, "occupied": True, "sensor_state": "ok"}]
    r = owner.c.post("/api/v1/measurements/batch", json={"measurements": ms}, headers={"Authorization": f"Bearer {dev.token}"})
    assert r.json() == {"stored": 1, "duplicate": 1, "rejected": 1}


def test_public_display_link(setup):
    env, owner, dev = setup
    dev.send("A", occupied=False)
    r = owner.post(f"/api/v1/stations/{dev.sid}/display-link")
    token = r.json()["token"]
    assert "#" in r.json()["url"]  # Token im Fragment, nicht im Pfad
    anon = env.client()
    body = anon.get("/api/v1/public/display/status", headers={"X-Display-Token": token}).json()
    assert body["free_count"] == 1 and "ai" not in body
    assert anon.get("/api/v1/public/display/status", headers={"X-Display-Token": "bsp_nope_nope_nope"}).status_code == 404
    owner.patch(f"/api/v1/stations/{dev.sid}", {"display_enabled": False})
    assert anon.get("/api/v1/public/display/status", headers={"X-Display-Token": token}).status_code == 404
    # Rotation macht alten Link ungültig
    new = owner.post(f"/api/v1/stations/{dev.sid}/display-link").json()["token"]
    assert anon.get("/api/v1/public/display/status", headers={"X-Display-Token": token}).status_code == 404
    assert anon.get("/api/v1/public/display/status", headers={"X-Display-Token": new}).status_code == 200


def test_ml_model_loaded_and_runs_in_shadow(env, tmp_path):
    joblib = pytest.importorskip("joblib")
    from sklearn.ensemble import IsolationForest

    from app.anomaly import FEATURE_NAMES, MlDetector

    normal = [[0, 0, 0, 0, 60], [0, 1, 120, 120, 90], [1, 1, 320, 320, 200], [0, 2, 80, 60, 300]] * 20
    model = IsolationForest(n_estimators=50, contamination=0.05, random_state=0).fit(normal)
    path = tmp_path / "m.joblib"
    joblib.dump({"model": model, "features": FEATURE_NAMES, "trained_on_simulated_data": True}, path)
    env.core.ml = MlDetector(path)

    owner = env.register()
    env.set_plan(owner, "school")
    sid, token = env.station(owner)
    dev = Device(owner, sid, token)
    _, body = status(owner, sid)
    assert body["ai"]["model_available"] is True and body["ai"]["plan_allows_ml"] is True
    dev.send("A", occupied=True)
    env.clock.advance(20)
    _vibrate(dev, env.clock, 6, vib=900)
    alerts = [e for e in owner.get("/api/v1/events?include_shadow=true").json()["events"] if e["kind"] == "unusual_movement"]
    assert any(e["severity"] == "warning" and e["detector"] == "rule" for e in alerts)
    assert any(e["severity"] == "shadow" and e["detector"] == "ml" for e in alerts)
    # Umschalten auf KI als sichtbares Verfahren
    assert owner.patch(f"/api/v1/stations/{sid}", {"alert_source": "ml"}).status_code == 200
