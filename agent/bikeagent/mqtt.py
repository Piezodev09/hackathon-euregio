"""Local MQTT publisher with Home Assistant MQTT discovery (optional, needs ``paho-mqtt``).

Each station appears in Home Assistant as one device with:
  * ``binary_sensor`` occupancy per space (device_class occupancy; unknown when the sensor has no valid data)
  * ``sensor`` free spaces
  * ``binary_sensor`` warning per space (device_class problem) - open movement warnings from the platform
  * ``binary_sensor`` Arduino connection and ``sensor`` CPU temperature of the gateway
Availability uses an MQTT last will ("offline" when the agent disappears).

Occupancy comes directly from the Arduino, so it keeps working in the LAN without internet; warnings
come from the platform (heartbeat response ``alerts``).

Topics (``<base>`` = ``bikestation/<station id>``)::

    <base>/availability            online | offline      (retained, last will)
    <base>/slot/<key>/state        occupied | free | unknown
    <base>/slot/<key>/alert        ON | OFF
    <base>/free                    number of free spaces
    <base>/gateway                 {"arduino": true, "cpu_temp_c": 48.2, "platform": true}
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Callable

from . import __version__ as VERSION

log = logging.getLogger("mqtt")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_") or "x"


def default_client_factory(client_id: str):
    import paho.mqtt.client as mqtt  # optional dependency

    try:  # paho-mqtt >= 2.0
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:  # paho-mqtt 1.x (Debian bookworm)
        return mqtt.Client(client_id=client_id)


class MqttBridge:
    def __init__(self, conf: dict, state, cfg, client_factory: Callable = default_client_factory):
        self.conf = conf
        self.state = state
        self.cfg = cfg  # GatewayConfig (slot_map, station_id)
        self.prefix = conf.get("discovery_prefix") or "homeassistant"
        self.station_id = state["station_id"]
        self.base = f"bikestation/{self.station_id}"
        self.uid = _slug(self.station_id)
        self.slots: dict[str, str] = {}  # slot key -> occupied | free | unknown
        self.alerts: set[str] = set()
        self.connected = False
        self._lock = threading.Lock()
        self.client = client_factory(f"bikeagent-{_slug(state.get('device_id', self.station_id))}")
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        if conf.get("username"):
            self.client.username_pw_set(conf["username"], conf.get("password") or None)
        if conf.get("tls"):
            self.client.tls_set()
        self.client.will_set(f"{self.base}/availability", "offline", qos=1, retain=True)

    # ---- connection
    def start(self) -> None:
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client.connect_async(self.conf["host"], int(self.conf.get("port") or 1883), keepalive=60)
        self.client.loop_start()
        log.info("MQTT: connecting to %s:%s", self.conf["host"], self.conf.get("port") or 1883)

    def stop(self) -> None:
        try:
            self._publish(f"{self.base}/availability", "offline", retain=True)
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:  # shutting down anyway
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        rc = getattr(reason_code, "value", reason_code)
        if rc not in (0, None):
            log.warning("MQTT connection refused (%s)", reason_code)
            return
        self.connected = True
        log.info("MQTT connected")
        self._publish(f"{self.base}/availability", "online", retain=True)
        self.publish_discovery()
        self.publish_all()

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        log.warning("MQTT disconnected - reconnecting automatically")

    def _publish(self, topic: str, payload, retain: bool = False) -> None:
        if not isinstance(payload, str):
            payload = json.dumps(payload, separators=(",", ":"))
        self.client.publish(topic, payload, qos=1, retain=retain)

    # ---- Home Assistant discovery
    def _device(self) -> dict:
        return {"identifiers": [f"bikestation_{self.uid}"], "name": self.state.get("station_name") or "Bike station",
                "manufacturer": "Smart Bike Station", "model": "Bike station agent", "sw_version": VERSION}

    def discovery_messages(self) -> list[tuple[str, dict]]:
        dev = self._device()
        avail = {"availability_topic": f"{self.base}/availability", "payload_available": "online",
                 "payload_not_available": "offline"}
        msgs = []
        for key in self.cfg.slot_map.values():
            k = _slug(key)
            msgs.append((f"{self.prefix}/binary_sensor/bikestation_{self.uid}/space_{k}/config", {
                "name": f"Space {key}", "unique_id": f"bikestation_{self.uid}_space_{k}", "device_class": "occupancy",
                "state_topic": f"{self.base}/slot/{key}/state", "payload_on": "occupied", "payload_off": "free",
                # "unknown" -> Home Assistant shows the state as unknown, never as free
                "value_template": "{{ value if value in ('occupied', 'free') else None }}", "device": dev, **avail}))
            msgs.append((f"{self.prefix}/binary_sensor/bikestation_{self.uid}/space_{k}_alert/config", {
                "name": f"Space {key} unusual movement", "unique_id": f"bikestation_{self.uid}_space_{k}_alert",
                "device_class": "problem", "state_topic": f"{self.base}/slot/{key}/alert", "payload_on": "ON",
                "payload_off": "OFF", "icon": "mdi:bike-fast", "device": dev, **avail}))
        msgs.append((f"{self.prefix}/sensor/bikestation_{self.uid}/free/config", {
            "name": "Free spaces", "unique_id": f"bikestation_{self.uid}_free", "state_topic": f"{self.base}/free",
            "unit_of_measurement": "spaces", "state_class": "measurement", "icon": "mdi:bicycle", "device": dev, **avail}))
        msgs.append((f"{self.prefix}/binary_sensor/bikestation_{self.uid}/arduino/config", {
            "name": "Arduino connected", "unique_id": f"bikestation_{self.uid}_arduino", "device_class": "connectivity",
            "state_topic": f"{self.base}/gateway", "value_template": "{{ 'ON' if value_json.arduino else 'OFF' }}",
            "entity_category": "diagnostic", "device": dev, **avail}))
        msgs.append((f"{self.prefix}/sensor/bikestation_{self.uid}/cpu_temp/config", {
            "name": "Gateway CPU temperature", "unique_id": f"bikestation_{self.uid}_cpu_temp", "device_class": "temperature",
            "unit_of_measurement": "°C", "state_topic": f"{self.base}/gateway",
            "value_template": "{{ value_json.cpu_temp_c }}", "entity_category": "diagnostic", "device": dev, **avail}))
        return msgs

    def publish_discovery(self) -> None:
        for topic, payload in self.discovery_messages():
            self._publish(topic, payload, retain=True)

    # ---- states
    def publish_all(self) -> None:
        with self._lock:
            slots = dict(self.slots)
        for key in self.cfg.slot_map.values():
            self._publish(f"{self.base}/slot/{key}/state", slots.get(key, "unknown"), retain=True)
            self._publish(f"{self.base}/slot/{key}/alert", "ON" if key in self.alerts else "OFF", retain=True)
        self._publish_free()

    def _publish_free(self) -> None:
        with self._lock:
            free = sum(1 for key in self.cfg.slot_map.values() if self.slots.get(key) == "free")
        self._publish(f"{self.base}/free", str(free), retain=True)

    def on_measurement(self, m: dict) -> None:
        """Gateway listener: every validated Arduino reading (and every watchdog "unknown")."""
        key = m["slot_id"]
        value = "unknown" if m["sensor_state"] != "ok" or m["occupied"] is None else ("occupied" if m["occupied"] else "free")
        with self._lock:
            changed = self.slots.get(key) != value
            self.slots[key] = value
        if changed:
            self._publish(f"{self.base}/slot/{key}/state", value, retain=True)
            self._publish_free()

    def publish_alerts(self, keys: list[str]) -> None:
        new = set(keys)
        for key in self.cfg.slot_map.values():
            if (key in new) != (key in self.alerts):
                self._publish(f"{self.base}/slot/{key}/alert", "ON" if key in new else "OFF", retain=True)
        self.alerts = new

    def publish_health(self, arduino: bool, cpu_temp_c: float | None, platform: bool | None) -> None:
        self._publish(f"{self.base}/gateway", {"arduino": bool(arduino), "cpu_temp_c": cpu_temp_c, "platform": platform})
