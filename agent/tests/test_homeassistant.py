"""Home Assistant add-on: start-up logic (options → pairing → MQTT from the Supervisor) and the add-on files."""

from __future__ import annotations

import json
import re
import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from bikeagent import agent as A
from bikeagent import homeassistant as H

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "integrations" / "home-assistant" / "bike-station-agent"
yaml = pytest.importorskip("yaml")


# ---------------------------------------------------------------------- helpers
class Supervisor:
    """Minimal fake of the Supervisor services API."""

    def __init__(self, status=200, data=None):
        self.status, self.data, self.auth = status, data, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.auth.append(self.headers.get("Authorization"))
                body = json.dumps({"result": "ok" if outer.status == 200 else "error", "data": outer.data or {}}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()


MQTT_SERVICE = {"host": "core-mosquitto", "port": 1883, "ssl": False, "protocol": "3.1.1",
                "username": "addons", "password": "s3cret", "addon": "core_mosquitto"}


def options(**kw) -> dict:
    base = {"platform_url": "https://192.168.1.50", "pairing_code": "ABCDE-12345", "ca_fingerprint": "",
            "source": "simulator", "serial_port": "auto", "device_name": "Home Assistant", "mqtt": True}
    return {**base, **kw}


@pytest.fixture
def fake_enroll(monkeypatch):
    calls = []

    def enroll(state, url, code, source, serial_port, ca_file, allow_http, name="", mqtt=None):
        calls.append({"url": url, "code": code, "source": source, "ca_file": ca_file, "allow_http": allow_http, "name": name})
        state.data = {"api_url": url, "device_id": f"dev_{len(calls)}", "token": "t" * 20, "station_id": "st_1",
                      "station_name": "Schoolyard", "slot_map": {"1": "A1"}, "config_version": 1, "source": source,
                      "serial_port": serial_port}
        state.save()
        return state.data

    monkeypatch.setattr(H, "enroll", enroll)
    return calls


@pytest.fixture
def supervisor():
    s = Supervisor(data=MQTT_SERVICE)
    yield s
    s.close()


# ---------------------------------------------------------------------- start-up logic
def test_not_paired_without_code_gives_clear_error(tmp_path, fake_enroll):
    with pytest.raises(A.AgentError, match="platform_url.*pairing_code"):
        H.prepare(A.State(tmp_path), options(pairing_code=""), token=None)
    assert fake_enroll == [] and not (tmp_path / "agent.json").exists()


def test_first_start_pairs_and_takes_mqtt_from_supervisor(tmp_path, fake_enroll, supervisor):
    st = H.prepare(A.State(tmp_path), options(), token="sup-token", supervisor=supervisor.url)
    assert fake_enroll == [{"url": "https://192.168.1.50", "code": "ABCDE-12345", "source": "simulator", "ca_file": None,
                            "allow_http": False, "name": "Home Assistant"}]
    assert supervisor.auth == ["Bearer sup-token"]
    saved = json.loads((tmp_path / "agent.json").read_text())
    assert saved["mqtt"] == {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "s3cret",
                             "tls": False, "discovery_prefix": "homeassistant"}
    assert saved["pairing_code_sha"] == H._code_hash("abcde-12345")  # case-insensitive
    assert st["station_id"] == "st_1"
    assert (tmp_path / "agent.json").stat().st_mode & 0o077 == 0


def test_restart_keeps_pairing_and_new_code_pairs_again(tmp_path, fake_enroll, supervisor):
    H.prepare(A.State(tmp_path), options(), token=None)
    H.prepare(A.State(tmp_path), options(), token=None)          # same (already used) code: no second pairing
    assert len(fake_enroll) == 1
    H.prepare(A.State(tmp_path), options(pairing_code=""), token=None)  # code removed after pairing: fine
    assert len(fake_enroll) == 1
    st = H.prepare(A.State(tmp_path), options(pairing_code="ZZZZZ-99999"), token=None)
    assert len(fake_enroll) == 2 and st["device_id"] == "dev_2"


def test_options_are_applied_on_every_start(tmp_path, fake_enroll, monkeypatch):
    H.prepare(A.State(tmp_path), options(), token=None)
    st = H.prepare(A.State(tmp_path), options(source="serial", serial_port="/dev/ttyUSB3"), token=None)
    assert st["source"] == "serial" and st["serial_port"] == "/dev/ttyUSB3"
    monkeypatch.setattr(H, "detect_serial_port", lambda: "/dev/serial/by-id/usb-Arduino_Uno-if00")
    st = H.prepare(A.State(tmp_path), options(source="serial", serial_port="auto"), token=None)
    assert st["serial_port"] == "/dev/serial/by-id/usb-Arduino_Uno-if00"


def test_ca_fingerprint_pins_the_platform_ca(tmp_path, fake_enroll, monkeypatch):
    pinned = []

    def fetch(url, fp, dest):
        pinned.append((url, fp))
        dest.write_text("PEM")
        return dest

    monkeypatch.setattr(H, "fetch_pinned_ca", fetch)
    H.prepare(A.State(tmp_path), options(ca_fingerprint="SHA-256 81:7F"), token=None)
    assert pinned == [("https://192.168.1.50", "SHA-256 81:7F")]
    assert fake_enroll[0]["ca_file"] == str(tmp_path / "ca.crt")


def test_mqtt_off_or_missing_broker_removes_mqtt(tmp_path, fake_enroll, supervisor, caplog):
    H.prepare(A.State(tmp_path), options(), token="x", supervisor=supervisor.url)
    st = H.prepare(A.State(tmp_path), options(mqtt=False), token="x", supervisor=supervisor.url)
    assert "mqtt" not in st.data
    missing = Supervisor(status=400)
    try:
        st = H.prepare(A.State(tmp_path), options(), token="x", supervisor=missing.url)
    finally:
        missing.close()
    assert "mqtt" not in st.data
    assert "Mosquitto" in caplog.text
    assert H.supervisor_mqtt(None) is None  # outside Home Assistant: no token, no request


def test_load_options_sanitises(tmp_path):
    p = tmp_path / "options.json"
    p.write_text(json.dumps({"platform_url": " https://h/ ", "source": "evil", "serial_port": "", "device_name": "x" * 200}))
    o = H.load_options(p)
    assert o["platform_url"] == "https://h" and o["source"] == "serial" and o["serial_port"] == "auto"
    assert len(o["device_name"]) == 80 and o["mqtt"] is True
    with pytest.raises(A.AgentError):
        H.load_options(tmp_path / "missing.json")


# ---------------------------------------------------------------------- shared agent helpers
@pytest.mark.parametrize("value", ["SHA-256 81:7F:D8", "sha256 Fingerprint=81:7F:D8", "81:7f:d8", "817FD8", "SHA256:81:7F:D8"])
def test_fingerprint_formats(value):
    assert A.normalize_fingerprint(value) == "817fd8"


def test_serial_detection_prefers_stable_arduino_name(tmp_path):
    (tmp_path / "serial" / "by-id").mkdir(parents=True)
    assert A.detect_serial_port(tmp_path) == "/dev/ttyACM0"
    (tmp_path / "ttyUSB0").touch()
    assert A.detect_serial_port(tmp_path) == str(tmp_path / "ttyUSB0")
    (tmp_path / "serial" / "by-id" / "usb-Arduino__www.arduino.cc__0043-if00").touch()
    assert A.detect_serial_port(tmp_path).endswith("usb-Arduino__www.arduino.cc__0043-if00")


# ---------------------------------------------------------------------- add-on files
def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_repository_file_at_git_root():
    repo = _yaml(ROOT / "repository.yaml")  # Home Assistant only looks at the root of the Git repository
    assert repo["name"] and repo["url"].startswith("https://github.com/")


def test_addon_config_is_complete():
    cfg = _yaml(ADDON / "config.yaml")
    for key in ("name", "version", "slug", "description", "arch", "startup", "boot", "options", "schema"):
        assert key in cfg, key
    assert re.fullmatch(r"[a-z0-9_]+", cfg["slug"])
    assert cfg["version"] == (ROOT / "agent" / "VERSION").read_text().strip()
    assert cfg["uart"] is True and cfg["usb"] is True
    assert "mqtt:want" in cfg["services"]
    assert set(cfg["options"]) == set(cfg["schema"])
    assert "image" not in cfg  # built locally from this folder
    assert set(_yaml(ADDON / "build.yaml")["build_from"]) == set(cfg["arch"])
    # platform_url must be https (or still empty before the first configuration)
    rx = re.fullmatch(r"match\((.*)\)", cfg["schema"]["platform_url"]).group(1)
    assert re.match(rx, "https://192.168.1.50") and re.match(rx, "")
    assert not re.match(rx, "http://192.168.1.50")
    assert cfg["schema"]["pairing_code"] == "password?"  # masked in the UI


@pytest.mark.parametrize("lang", ["en", "de", "nl"])
def test_addon_translations_cover_all_options(lang):
    cfg = _yaml(ADDON / "config.yaml")
    tr = _yaml(ADDON / "translations" / f"{lang}.yaml")["configuration"]
    assert set(tr) == set(cfg["options"])
    assert all(v.get("name") and v.get("description") for v in tr.values())


def test_addon_code_is_in_sync_with_agent():
    r = subprocess.run(["bash", str(ROOT / "scripts" / "sync-ha-addon.sh"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_addon_image_files():
    def png_size(p: Path) -> tuple[int, int]:
        head = p.read_bytes()[:24]
        assert head[:8] == b"\x89PNG\r\n\x1a\n"
        return struct.unpack(">II", head[16:24])

    assert png_size(ADDON / "icon.png") == (128, 128)
    assert png_size(ADDON / "logo.png") == (250, 100)
    docker = (ADDON / "Dockerfile").read_text()
    assert "ARG BUILD_FROM" in docker and "BIKE_AGENT_SELF_UPDATE=0" in docker and "BIKE_AGENT_STATE=/data" in docker
    assert subprocess.run(["sh", "-n", str(ADDON / "run.sh")]).returncode == 0
    assert "bikeagent.homeassistant" in (ADDON / "run.sh").read_text()
    assert (ADDON / "DOCS.md").stat().st_size > 1000
