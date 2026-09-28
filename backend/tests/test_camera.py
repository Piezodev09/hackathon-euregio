"""Kamera: Freigabe, Upload vom Gateway, Anzeige nur für Admins mit Audit, Aufbewahrung, Mandantentrennung."""

from __future__ import annotations

from pathlib import Path

from conftest import Device

JPEG = (Path(__file__).resolve().parents[2] / "pi-gateway" / "sim-camera.jpg").read_bytes()


def upload(api, token, data=JPEG, ctype="image/jpeg", **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return api.c.post(f"/api/v1/agent/snapshot?{qs}", content=data,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})


def school(env):
    owner = env.register()
    env.set_plan(owner, "school")
    sid, token = env.station(owner)
    return owner, sid, token


def test_camera_off_by_default_and_needs_approval(env):
    owner, sid, token = school(env)
    assert owner.get(f"/api/v1/stations/{sid}").json()["camera_enabled"] is False
    assert upload(owner, token).status_code == 403
    assert owner.put(f"/api/v1/stations/{sid}/camera", {"enabled": True}).status_code == 422
    r = owner.put(f"/api/v1/stations/{sid}/camera", {"enabled": True, "retention_h": 2, "approved_by": "Schulleitung 28.09."})
    assert r.status_code == 200
    assert owner.get(f"/api/v1/stations/{sid}/status").json()["camera_active"] is True


def test_free_plan_has_no_camera(env):
    owner = env.register()
    sid, _ = env.station(owner)
    assert owner.put(f"/api/v1/stations/{sid}/camera", {"enabled": True, "approved_by": "x"}).status_code == 402


def test_upload_view_audit_retention_and_disable(env):
    owner, sid, token = school(env)
    owner.put(f"/api/v1/stations/{sid}/camera", {"enabled": True, "retention_h": 1, "approved_by": "SL"})
    assert upload(owner, token, data=b"GIF89a....").status_code == 422
    assert upload(owner, token, ctype="image/png").status_code == 415
    assert upload(owner, token, data=b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024)).status_code == 413
    r = upload(owner, token, reason="manual")
    assert r.status_code == 201, r.text
    snaps = owner.get(f"/api/v1/stations/{sid}/snapshots").json()["snapshots"]
    assert len(snaps) == 1
    img = owner.get(f"/api/v1/snapshots/{snaps[0]['id']}")
    assert img.status_code == 200 and img.content == JPEG and img.headers["cache-control"].startswith("no-store")
    assert env.core.db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'camera_snapshot_viewed'") == 1
    viewer = env.invite(owner, "v@example.org", "viewer")
    assert viewer.get(f"/api/v1/snapshots/{snaps[0]['id']}").status_code == 403
    # Mandantentrennung
    other = env.register(org="Fremd")
    assert other.get(f"/api/v1/snapshots/{snaps[0]['id']}").status_code in (403, 404)
    # Aufbewahrung
    files = list(env.app.state.snapshots.dir.glob("*.jpg"))
    assert len(files) == 1 and oct(files[0].stat().st_mode)[-3:] == "600"
    env.clock.advance(3601)
    assert env.app.state.snapshots.purge() == 1
    assert not list(env.app.state.snapshots.dir.glob("*.jpg"))
    # Ausschalten löscht sofort
    env.clock.advance(-3601)
    upload(owner, token)
    assert owner.put(f"/api/v1/stations/{sid}/camera", {"enabled": False}).json()["deleted"] == 1


def test_alert_requests_capture_only_when_enabled(env):
    owner, sid, token = school(env)
    dev = Device(owner, sid, token)

    def batch(vib):
        dev.seq += 1
        env.clock.advance(0.5)
        return owner.c.post("/api/v1/measurements/batch", json={"measurements": [{"station_id": sid, "sequence": dev.seq,
                            "occupied": True, "vibration_score": vib, "sensor_state": "ok"}]}, headers={"Authorization": f"Bearer {token}"}).json()

    batch(0)
    env.clock.advance(20)
    outs = [batch(900) for _ in range(4)]
    assert not any(o.get("capture") for o in outs)  # Kamera aus -> nie
    owner.put(f"/api/v1/stations/{sid}/camera", {"enabled": True, "approved_by": "SL"})
    env.clock.advance(40)
    outs = [batch(900) for _ in range(4)]
    cap = [o for o in outs if o.get("capture")]
    assert len(cap) == 1 and cap[0]["event_id"].startswith("evt")
    r = upload(owner, token, reason="alert", event_id=cap[0]["event_id"])
    assert r.status_code == 201
    assert owner.get(f"/api/v1/stations/{sid}/snapshots").json()["snapshots"][0]["event_id"] == cap[0]["event_id"]


def test_snapshot_request_needs_managed_gateway(env):
    owner, sid, token = school(env)
    owner.put(f"/api/v1/stations/{sid}/camera", {"enabled": True, "approved_by": "SL"})
    assert owner.post(f"/api/v1/stations/{sid}/camera/snapshot").status_code == 409


def test_deleting_station_removes_images_from_disk(env):
    owner, sid, token = school(env)
    owner.put(f"/api/v1/stations/{sid}/camera", {"enabled": True, "approved_by": "SL"})
    assert upload(owner, token).status_code == 201
    assert len(list(env.app.state.snapshots.dir.glob("*.jpg"))) == 1
    assert owner.delete(f"/api/v1/stations/{sid}").status_code == 200
    assert not list(env.app.state.snapshots.dir.glob("*.jpg"))
