"""MQTT bridge: Home Assistant discovery payloads, occupancy states from the Arduino, alerts from the platform."""

from __future__ import annotations

import json

from bikeagent import agent as A
from bikeagent.gateway import GatewayConfig
from bikeagent.mqtt import MqttBridge, default_client_factory


class FakeClient:
    def __init__(self):
        self.published: list[tuple[str, str, bool]] = []
        self.will = None
        self.auth = None
        self.on_connect = self.on_disconnect = None

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, retain)

    def username_pw_set(self, user, pw):
        self.auth = (user, pw)

    def tls_set(self):
        pass

    def last(self, topic):
        return next((p for t, p, _ in reversed(self.published) if t == topic), None)


class FakeState(dict):
    pass


def bridge(slots=("A", "B", "C"), **conf):
    client = FakeClient()
    state = FakeState(station_id="st_Ab-1", station_name="Schoolyard", device_id="dev_x")
    cfg = GatewayConfig(api_url="http://x", station_id="st_Ab-1", slot_map={k: k for k in slots}, token="t" * 20)
    b = MqttBridge({"host": "ha.local", "username": "mqtt", "password": "pw", **conf}, state, cfg, client_factory=lambda cid: client)
    return b, client


def test_discovery_describes_one_device_per_station():
    b, client = bridge()
    msgs = dict(b.discovery_messages())
    assert len(msgs) == 3 * 2 + 3
    occ = msgs["homeassistant/binary_sensor/bikestation_st_ab_1/space_a/config"]
    assert occ["device_class"] == "occupancy" and occ["state_topic"] == "bikestation/st_Ab-1/slot/A/state"
    assert occ["availability_topic"] == "bikestation/st_Ab-1/availability" and "None" in occ["value_template"]
    alert = msgs["homeassistant/binary_sensor/bikestation_st_ab_1/space_a_alert/config"]
    assert alert["device_class"] == "problem"
    assert msgs["homeassistant/sensor/bikestation_st_ab_1/free/config"]["unit_of_measurement"] == "spaces"
    uids = [m["unique_id"] for m in msgs.values()]
    assert len(uids) == len(set(uids))
    assert {json.dumps(m["device"], sort_keys=True) for m in msgs.values()} == {json.dumps(occ["device"], sort_keys=True)}
    assert client.will == ("bikestation/st_Ab-1/availability", "offline", True) and client.auth == ("mqtt", "pw")


def test_connect_publishes_availability_discovery_and_states():
    b, client = bridge(discovery_prefix="ha")
    b.client.on_connect(client, None, {}, 0)
    assert b.connected
    assert client.last("bikestation/st_Ab-1/availability") == "online"
    assert any(t.startswith("ha/binary_sensor/") and r for t, _, r in client.published)
    assert client.last("bikestation/st_Ab-1/slot/B/state") == "unknown"  # nothing known yet - never "free"
    assert client.last("bikestation/st_Ab-1/free") == "0"


def test_measurements_update_states_and_free_count():
    b, client = bridge()
    b.on_measurement({"slot_id": "A", "occupied": True, "vibration_score": 0, "sensor_state": "ok"})
    b.on_measurement({"slot_id": "B", "occupied": False, "vibration_score": 0, "sensor_state": "ok"})
    b.on_measurement({"slot_id": "C", "occupied": False, "vibration_score": 0, "sensor_state": "ok"})
    assert client.last("bikestation/st_Ab-1/slot/A/state") == "occupied"
    assert client.last("bikestation/st_Ab-1/free") == "2"
    n = len(client.published)
    b.on_measurement({"slot_id": "C", "occupied": False, "vibration_score": 0, "sensor_state": "ok"})
    assert len(client.published) == n  # unchanged -> nothing published
    b.on_measurement({"slot_id": "C", "occupied": None, "vibration_score": 0, "sensor_state": "error"})
    assert client.last("bikestation/st_Ab-1/slot/C/state") == "unknown"
    assert client.last("bikestation/st_Ab-1/free") == "1"


def test_alerts_and_health():
    b, client = bridge()
    b.publish_alerts(["B"])
    assert client.last("bikestation/st_Ab-1/slot/B/alert") == "ON"
    assert client.last("bikestation/st_Ab-1/slot/A/alert") is None
    b.publish_alerts([])
    assert client.last("bikestation/st_Ab-1/slot/B/alert") == "OFF"
    b.publish_health(True, 51.5, False)
    assert json.loads(client.last("bikestation/st_Ab-1/gateway")) == {"arduino": True, "cpu_temp_c": 51.5, "platform": False}


def test_agent_forwards_alerts_and_config_to_mqtt(tmp_path):
    s = A.State(tmp_path)
    s.data = {"api_url": "http://127.0.0.1:1", "device_id": "dev_1", "token": "bsd_x", "token_issued_at": 0,
              "station_id": "st_1", "slot_map": {"A": "A"}, "config_version": 1, "source": "simulator"}
    s.save()
    ag = A.Agent(s, clock=lambda: 1000.0)
    calls = []

    class Spy:
        connected = True

        def publish_alerts(self, keys):
            calls.append(("alerts", keys))

        def publish_discovery(self):
            calls.append(("discovery",))

    ag.mqtt = Spy()
    ag.apply({"config_version": 2, "slot_map": {"A": "A", "B": "B"}, "commands": [], "alerts": ["B"]})
    assert calls == [("discovery",), ("alerts", ["B"])]


def test_cli_configures_mqtt_and_hides_password(tmp_path, capsys):
    s = A.State(tmp_path)
    s.data = {"api_url": "http://127.0.0.1:1", "device_id": "dev_1", "token": "bsd_secret_token", "station_id": "st_1",
              "slot_map": {"A": "A"}, "config_version": 1}
    s.save()
    assert A.main(["--state-dir", str(tmp_path), "mqtt", "--mqtt-host", "ha.local", "--mqtt-username", "u",
                   "--mqtt-password", "pw"]) == 0
    assert json.loads(s.path.read_text())["mqtt"]["host"] == "ha.local"
    capsys.readouterr()
    assert A.main(["--state-dir", str(tmp_path), "status"]) == 0
    out = capsys.readouterr().out
    assert '"password": "***"' in out and "bsd_secret_token" not in out
    assert A.main(["--state-dir", str(tmp_path), "mqtt", "--disable"]) == 0
    assert "mqtt" not in json.loads(s.path.read_text())


def test_real_paho_client_can_be_created():
    import pytest

    pytest.importorskip("paho.mqtt.client")
    client = default_client_factory("bikeagent-test")
    assert hasattr(client, "publish") and hasattr(client, "will_set")
