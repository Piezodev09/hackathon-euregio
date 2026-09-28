"""Automatisierte Varianten der Abnahmetests aus Plan Kapitel 14 (soweit ohne Hardware prüfbar)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

DEVICE = "device-token-for-tests-0001"
ADMIN = "admin-token-for-tests-00001"


class Clock:
    def __init__(self, t: float = 1_700_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BIKE_DEVICE_TOKENS", DEVICE)
    monkeypatch.setenv("BIKE_ADMIN_TOKENS", ADMIN)
    monkeypatch.setenv("BIKE_DB_PATH", str(tmp_path / "test.db"))
    settings = load_settings()
    # Kein Modell im Test -> KI "nicht verfügbar", Regel erzeugt die Warnung.
    object.__setattr__(settings, "model_path", tmp_path / "missing.joblib")
    clock = Clock()
    app = create_app(settings, clock=clock)
    with TestClient(app) as client:
        yield client, clock


class Sender:
    def __init__(self, client):
        self.client = client
        self.seq = 0

    def send(self, slot="A", occupied=True, vib=0, state="ok", token=DEVICE, **extra):
        self.seq += 1
        body = {
            "station_id": "demo-01",
            "slot_id": slot,
            "sequence": self.seq,
            "occupied": occupied,
            "vibration_score": vib,
            "sensor_state": state,
            **extra,
        }
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post("/api/v1/measurements", json=body, headers=headers)


def status(client):
    r = client.get("/api/v1/stations/demo-01/status")
    assert r.status_code == 200
    return {s["slot_id"]: s for s in r.json()["slots"]}, r.json()


def test_no_data_is_unknown_never_free(env):
    client, _ = env
    slots, body = status(client)
    assert all(s["state"] == "unknown" for s in slots.values())
    assert body["free_count"] == 0
    assert body["recommendation"] is None


def test_T01_T02_free_and_occupied(env):
    client, _ = env
    s = Sender(client)
    assert s.send("A", occupied=True).status_code == 202
    assert s.send("B", occupied=False).status_code == 202
    slots, body = status(client)
    assert slots["A"]["state"] == "occupied"
    assert slots["B"]["state"] == "free"
    assert slots["C"]["state"] == "unknown"
    assert body["free_count"] == 1


def test_T03_recommendation_is_first_free_by_position(env):
    client, _ = env
    s = Sender(client)
    s.send("A", occupied=True)
    s.send("B", occupied=False)
    s.send("C", occupied=False)
    _, body = status(client)
    assert body["recommendation"] == "B"


def test_T07_sensor_error_is_unknown(env):
    client, _ = env
    s = Sender(client)
    s.send("A", occupied=False)
    s.send("A", occupied=None, state="error")
    slots, body = status(client)
    assert slots["A"]["state"] == "unknown"
    assert slots["A"]["unknown_reason"] == "sensor_error"
    assert body["recommendation"] is None


def test_stale_data_becomes_unknown(env):
    client, clock = env
    s = Sender(client)
    s.send("B", occupied=False)
    clock.advance(31)
    slots, body = status(client)
    assert slots["B"]["state"] == "unknown"
    assert slots["B"]["unknown_reason"] == "stale"
    assert body["free_count"] == 0


def test_duplicate_sequence_is_ignored(env):
    client, _ = env
    s = Sender(client)
    assert s.send("A", occupied=True).json()["stored"] is True
    s.seq -= 1
    r = s.send("A", occupied=False)
    assert r.json() == {"stored": False, "duplicate": True, "alert_created": False}
    slots, _ = status(client)
    assert slots["A"]["state"] == "occupied"


def test_T09_write_without_token_rejected_and_logged(env):
    client, _ = env
    s = Sender(client)
    assert s.send(token=None).status_code == 401
    assert s.send(token="wrong-token-wrong-token").status_code == 401
    rows = client.app.state.db.query("SELECT action FROM audit_log")
    assert [r[0] for r in rows].count("rejected_write") == 2
    slots, _ = status(client)
    assert slots["A"]["state"] == "unknown"


def test_T10_read_view_cannot_do_admin(env):
    client, _ = env
    assert client.get("/api/v1/events").status_code == 401
    assert client.post("/api/v1/events/1/ack").status_code == 401
    # Geräte-Token ist kein Admin-Token.
    r = client.post("/api/v1/events/1/ack", headers={"Authorization": f"Bearer {DEVICE}"})
    assert r.status_code == 403


def test_validation_rejects_bad_input(env):
    client, _ = env
    s = Sender(client)
    assert s.send(vib=5000).status_code == 422
    assert s.send(slot="Z").status_code == 422
    assert s.send(occupied=None, state="ok").status_code == 422
    assert s.send(evil="x").status_code == 422
    assert s.send(slot="A; DROP TABLE slot").status_code == 422


def _vibrate(s, clock, n, vib=600, step=0.5, slot="A"):
    created = False
    for _ in range(n):
        clock.advance(step)
        created |= s.send(slot, occupied=True, vib=vib).json()["alert_created"]
    return created


def test_T05_unusual_movement_creates_alert_after_grace(env):
    client, clock = env
    s = Sender(client)
    s.send("A", occupied=True)
    clock.advance(20)  # Schonzeit (15 s) vorbei
    assert _vibrate(s, clock, 3) is True
    slots, _ = status(client)
    assert slots["A"]["state"] == "occupied"  # Alarm ist kein Belegungszustand
    assert slots["A"]["alert"]["kind"] == "unusual_movement"
    assert slots["A"]["alert"]["detector"] == "rule"


def test_T06_parking_within_grace_period_no_alert(env):
    client, clock = env
    s = Sender(client)
    s.send("A", occupied=False)
    clock.advance(5)
    s.send("A", occupied=True)  # gerade eingestellt
    assert _vibrate(s, clock, 5) is False
    slots, _ = status(client)
    assert slots["A"]["alert"] is None


def test_single_bump_no_alert(env):
    client, clock = env
    s = Sender(client)
    s.send("A", occupied=True)
    clock.advance(30)
    assert _vibrate(s, clock, 1) is False


def test_cooldown_and_ack(env):
    client, clock = env
    s = Sender(client)
    s.send("A", occupied=True)
    clock.advance(20)
    assert _vibrate(s, clock, 3)
    assert not _vibrate(s, clock, 3)  # Cooldown
    admin = {"Authorization": f"Bearer {ADMIN}"}
    events = client.get("/api/v1/events", headers=admin).json()["events"]
    alerts = [e for e in events if e["kind"] == "unusual_movement"]
    assert len(alerts) == 1
    assert alerts[0]["detail"]["ml"] is None  # KI nicht verfügbar
    r = client.post(f"/api/v1/events/{alerts[0]['id']}/ack", headers=admin)
    assert r.json() == {"acknowledged": True, "already_acknowledged": False}
    slots, _ = status(client)
    assert slots["A"]["alert"] is None


def test_old_buffered_data_does_not_trigger_alert(env):
    client, clock = env
    s = Sender(client)
    s.send("A", occupied=True)
    clock.advance(60)
    created = False
    for i in range(5):
        created |= s.send("A", occupied=True, vib=800, age_ms=40_000 - i * 100).json()["alert_created"]
    assert created is False


def test_sensor_fault_event(env):
    client, _ = env
    s = Sender(client)
    s.send("A", occupied=True)
    s.send("A", occupied=None, state="error")
    s.send("A", occupied=None, state="error")
    events = client.get("/api/v1/events", headers={"Authorization": f"Bearer {ADMIN}"}).json()["events"]
    assert [e["kind"] for e in events] == ["sensor_fault"]


def test_T13_summary_time_weighted_and_labels_simulated(env):
    client, clock = env
    clock.t = 1_700_000_000 - (1_700_000_000 % 3600)  # Stundenbeginn
    s = Sender(client)
    for i in range(6):  # 60 s belegt, 60 s frei, Heartbeat alle 10 s
        s.send("A", occupied=True, source="simulated")
        clock.advance(10)
    for i in range(6):
        s.send("A", occupied=False, source="simulated")
        clock.advance(10)
    body = client.get("/api/v1/occupancy/summary?hours=1").json()
    assert body["contains_simulated"] is True
    assert body["contains_live"] is False
    assert body["buckets"][-1]["occupancy"]["A"] == pytest.approx(0.5, abs=0.01)
    assert body["buckets"][-1]["occupancy"]["B"] is None


def test_batch_endpoint(env):
    client, _ = env
    body = {
        "measurements": [
            {"station_id": "demo-01", "slot_id": "A", "sequence": 1, "occupied": True, "sensor_state": "ok"},
            {"station_id": "demo-01", "slot_id": "A", "sequence": 1, "occupied": True, "sensor_state": "ok"},
            {"station_id": "demo-01", "slot_id": "Q", "sequence": 2, "occupied": True, "sensor_state": "ok"},
        ]
    }
    r = client.post("/api/v1/measurements/batch", json=body, headers={"Authorization": f"Bearer {DEVICE}"})
    assert r.json() == {"stored": 1, "duplicate": 1, "rejected": 1}


def test_health_and_security_headers(env):
    client, _ = env
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["database"] is True
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/docs").status_code == 404


def test_dashboard_served(env):
    client, _ = env
    r = client.get("/")
    assert r.status_code == 200
    assert "Smarte Radstation" in r.text


def test_weak_tokens_refused(monkeypatch):
    monkeypatch.setenv("BIKE_ADMIN_TOKENS", "admin")
    with pytest.raises(ValueError):
        load_settings()


def test_ml_model_loaded_and_runs_in_shadow(tmp_path, monkeypatch):
    joblib = pytest.importorskip("joblib")
    from sklearn.ensemble import IsolationForest

    from app.anomaly import FEATURE_NAMES

    normal = [[0, 0, 0, 0, 60], [0, 1, 120, 120, 90], [1, 1, 320, 320, 200], [0, 2, 80, 60, 300]] * 20
    model = IsolationForest(n_estimators=50, contamination=0.05, random_state=0).fit(normal)
    path = tmp_path / "m.joblib"
    joblib.dump({"model": model, "features": FEATURE_NAMES, "trained_on_simulated_data": True}, path)

    monkeypatch.setenv("BIKE_DEVICE_TOKENS", DEVICE)
    monkeypatch.setenv("BIKE_ADMIN_TOKENS", ADMIN)
    monkeypatch.setenv("BIKE_DB_PATH", str(tmp_path / "ml.db"))
    settings = load_settings()
    object.__setattr__(settings, "model_path", path)
    clock = Clock()
    with TestClient(create_app(settings, clock=clock)) as client:
        _, body = status(client)
        assert body["ai"]["model_available"] is True
        s = Sender(client)
        s.send("A", occupied=True)
        clock.advance(20)
        _vibrate(s, clock, 6, vib=900)
        events = client.get("/api/v1/events?include_shadow=true", headers={"Authorization": f"Bearer {ADMIN}"}).json()
        alerts = [e for e in events["events"] if e["kind"] == "unusual_movement"]
        assert any(e["severity"] == "warning" and e["detector"] == "rule" for e in alerts)
        assert any(e["severity"] == "shadow" and e["detector"] == "ml" for e in alerts)
        assert all(isinstance(e["detail"]["ml"], bool) for e in alerts)
