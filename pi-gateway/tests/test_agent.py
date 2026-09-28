from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent as A  # noqa: E402


def make_tgz(files: dict[str, bytes], extra=None) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        if extra:
            extra(tar)
    return gzip.compress(raw.getvalue())


def test_state_file_is_private_and_atomic(tmp_path):
    s = A.State(tmp_path / "st")
    s.data = {"token": "bsd_geheim", "api_url": "https://x"}
    s.save()
    assert (s.path.stat().st_mode & 0o777) == 0o600
    os.chmod(s.path, 0o644)
    assert A.State(tmp_path / "st").load()["token"] == "bsd_geheim"
    assert (s.path.stat().st_mode & 0o777) == 0o600


def test_api_requires_https_except_localhost():
    with pytest.raises(A.AgentError):
        A.Api("http://radstation.example.org")
    A.Api("http://127.0.0.1:8000")
    A.Api("http://radstation.example.org", allow_http=True)
    api = A.Api("https://radstation.example.org")
    with pytest.raises(A.AgentError):
        api.request("GET", "https://evil.example/agent.tar.gz")


@pytest.mark.parametrize("bad", ["../evil.py", "/etc/passwd", "sub/dir.py"])
def test_safe_extract_rejects_path_tricks(tmp_path, bad):
    with pytest.raises(A.AgentError):
        A.safe_extract(make_tgz({bad: b"x"}), tmp_path / "out")


def test_safe_extract_rejects_symlinks(tmp_path):
    def add_link(tar):
        info = tarfile.TarInfo("agent.py")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/shadow"
        tar.addfile(info)
    with pytest.raises(A.AgentError):
        A.safe_extract(make_tgz({"VERSION": b"1"}, add_link), tmp_path / "out")


def layout(tmp_path, version="1.0.0"):
    prefix = tmp_path / "opt"
    rel = prefix / "releases" / version
    rel.mkdir(parents=True)
    (rel / "agent.py").write_text("# alt")
    (rel / "VERSION").write_text(version)
    (prefix / "current").symlink_to(rel)
    return prefix, rel / "agent.py"


class FakeApi:
    def __init__(self, data: bytes, status=200):
        self.data, self.status = data, status

    def request(self, method, path, body=None, token=None, raw=False, max_bytes=0):
        return self.status, self.data


def test_update_verifies_hash_and_switches(tmp_path):
    prefix, agent_file = layout(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pkg = make_tgz({"VERSION": b"1.1.0", "agent.py": b"# neu", "gateway.py": b"", "simulator.py": b""})
    with pytest.raises(A.AgentError):
        A.apply_update(FakeApi(pkg), "t", {"version": "1.1.0", "sha256": "0" * 64, "url": "x"}, state_dir, agent_file)
    assert (prefix / "current").resolve().name == "1.0.0"
    ok = A.apply_update(FakeApi(pkg), "t", {"version": "1.1.0", "sha256": hashlib.sha256(pkg).hexdigest(), "url": "x"},
                        state_dir, agent_file)
    assert ok and (prefix / "current").resolve().name == "1.1.0"
    assert (prefix / "current" / "agent.py").read_text() == "# neu"
    assert json.loads((state_dir / "update-pending.json").read_text())["previous"].endswith("1.0.0")


def test_update_rejects_version_mismatch(tmp_path):
    prefix, agent_file = layout(tmp_path)
    (tmp_path / "state").mkdir()
    pkg = make_tgz({"VERSION": b"6.6.6", "agent.py": b""})
    with pytest.raises(A.AgentError):
        A.apply_update(FakeApi(pkg), "t", {"version": "1.1.0", "sha256": hashlib.sha256(pkg).hexdigest(), "url": "x"},
                       tmp_path / "state", agent_file)


def test_automatic_rollback_after_failed_starts(tmp_path):
    prefix, old_agent = layout(tmp_path, "1.0.0")
    new = prefix / "releases" / "1.1.0"
    new.mkdir()
    (new / "agent.py").write_text("")
    A.switch_current(prefix, new)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "update-pending.json").write_text(json.dumps({"previous": str(old_agent.parent), "version": "1.1.0", "starts": 0}))
    A.check_rollback(state_dir, new / "agent.py")
    A.check_rollback(state_dir, new / "agent.py")
    with pytest.raises(SystemExit):
        A.check_rollback(state_dir, new / "agent.py")
    assert (prefix / "current").resolve().name == "1.0.0"
    assert not (state_dir / "update-pending.json").exists()


def test_confirm_update_removes_marker(tmp_path):
    (tmp_path / "update-pending.json").write_text("{}")
    A.confirm_update(tmp_path)
    assert not (tmp_path / "update-pending.json").exists()


def enrolled_state(tmp_path) -> A.State:
    s = A.State(tmp_path)
    s.data = {"api_url": "http://127.0.0.1:1", "device_id": "dev_1", "token": "bsd_alt", "token_issued_at": 0,
              "station_id": "st_1", "config_version": 1, "source": "simulator"}
    s.save()
    return s


class RecordingApi:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, body=None, token=None, raw=False, max_bytes=0):
        self.calls.append((path, token))
        return self.responses.get(path, (200, {}))


def test_agent_applies_config_commands_and_rotates(tmp_path):
    s = enrolled_state(tmp_path)
    ag = A.Agent(s, clock=lambda: 1000.0)
    ag.api = RecordingApi({"/api/v1/agent/rotate-token": (200, {"token": "bsd_neu"})})
    ag.apply({"config_version": 2, "commands": ["rotate_token", "restart", "evil"]})
    saved = json.loads(s.path.read_text())
    assert saved["config_version"] == 2 and saved["token"] == "bsd_neu"
    assert ag.cfg.token == "bsd_neu" and ag.stop.is_set() and ag.exit_code == A.EXIT_RESTART


def test_agent_rotates_old_token_automatically(tmp_path):
    s = enrolled_state(tmp_path)
    ag = A.Agent(s, clock=lambda: A.TOKEN_MAX_AGE_S + 10)
    ag.api = RecordingApi({"/api/v1/agent/rotate-token": (200, {"token": "bsd_frisch"})})
    ag.apply({"config_version": 1, "commands": []})
    assert ag.cfg.token == "bsd_frisch"


def test_simulator_source_marks_data_simulated(tmp_path):
    ag = A.Agent(enrolled_state(tmp_path))
    assert ag.cfg.source == "simulated"


def test_simulator_single_stall_lines():
    import io

    from simulator import Simulator

    from gateway import parse_line

    out = io.StringIO()
    sim = Simulator(heartbeat_s=10, out=out)
    sim.emit()
    sim.command("p")
    sim.command("e")
    sim.command("n 04aabbccdd")
    lines = [parse_line(x) for x in out.getvalue().splitlines()]
    assert [m.get("occupied") for m in lines[:3]] == [False, True, None]
    assert lines[2]["sensor_state"] == "error"
    assert lines[3] == {"kind": "nfc", "uid": "04AABBCCDD"}


def test_camera_detection_and_simulator_image(monkeypatch):
    import camera

    cam = camera.Camera(simulated=True)
    assert cam.kind == "simulator" and cam.capture().startswith(b"\xff\xd8\xff")
    monkeypatch.setattr(camera.shutil, "which", lambda n: None)
    monkeypatch.setattr(camera.os.path, "exists", lambda p: False)
    assert camera.detect() == "none" and camera.Camera().capture() is None
    monkeypatch.setattr(camera.shutil, "which", lambda n: "/usr/bin/rpicam-still" if n == "rpicam-still" else None)
    assert camera.detect() == "rpicam-still"
    assert camera._cmd("fswebcam", "/tmp/x.jpg", "/dev/video0")[0] == "fswebcam"


def test_agent_uploads_snapshot_on_alert_hint(tmp_path):
    s = enrolled_state(tmp_path)
    ag = A.Agent(s)
    calls = []

    class Up:
        def upload(self, path, data, token, content_type="image/jpeg"):
            calls.append((path, len(data), token))
            return 201, {}

    ag.api = Up()
    assert ag.capture_and_upload("alert", "evt_1") == 201
    assert calls[0][0] == "/api/v1/agent/snapshot?reason=alert&event_id=evt_1" and calls[0][2] == "bsd_alt"
    assert ag.capture_and_upload("manual") == 0  # höchstens ein Bild alle 10 s


def test_doctor_reports_unpaired_and_unreachable(tmp_path):
    lines = []
    assert A.doctor(A.State(tmp_path / "leer"), out=lines.append) >= 1
    assert any("Nicht gekoppelt" in x for x in lines)
    lines.clear()
    s = enrolled_state(tmp_path)  # api_url zeigt auf 127.0.0.1:1 -> nicht erreichbar
    errors = A.doctor(s, out=lines.append)
    assert errors >= 1 and any("nicht erreichbar" in x for x in lines) and any("Simulator" in x for x in lines)


def test_doctor_checks_token_and_clock(tmp_path, monkeypatch):
    now = time.time()

    class FakeApi(RecordingApi):
        def __init__(self, *a, **kw):
            super().__init__({"/health": (200, {"status": "ok"}),
                              "/api/v1/agent/whoami": (200, {"station_name": "Schulhof", "server_time": now - 60})})

    monkeypatch.setattr(A, "Api", FakeApi)
    lines = []
    assert A.doctor(enrolled_state(tmp_path), out=lines.append) == 1
    assert any("Geräte-Token gültig" in x and "Schulhof" in x for x in lines)
    assert any("✗ Uhrzeit weicht +6" in x for x in lines) and any("timedatectl" in x for x in lines)


def test_doctor_explains_tls_errors(tmp_path, monkeypatch):
    import ssl
    import urllib.error

    class TlsFail:
        def __init__(self, *a, **kw):
            pass

        def request(self, *a, **kw):
            raise urllib.error.URLError(ssl.SSLCertVerificationError(1, "self-signed certificate"))

    monkeypatch.setattr(A, "Api", TlsFail)
    lines = []
    assert A.doctor(enrolled_state(tmp_path), out=lines.append) == 1
    assert any("TLS-Fehler" in x for x in lines) and any("IP fehlt im Zertifikat" in x for x in lines)


def test_local_display_offline_fallback_and_server_status(tmp_path):
    agent = A.Agent(enrolled_state(tmp_path))
    disp = A.LocalDisplay(agent)
    disp.api.request = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    s = disp.status()
    assert s["offline"] is True and s["state"] == "unknown" and s["unknown_reason"] == "no_data"
    agent.gw.handle_line('{"presence":0,"vibration":0,"seq":1}')
    disp._fetched_at = 0
    s = disp.status()
    assert s["offline"] is True and s["state"] == "free" and s["simulated_data"] is True
    agent.gw.last_local["at"] -= 31  # älter als 30 s -> unbekannt, nie „frei“
    disp._fetched_at = 0
    assert disp.status()["state"] == "unknown" and disp.status()["unknown_reason"] == "stale"
    agent.gw.handle_line('{"presence":-1,"vibration":0,"seq":2,"state":"error"}')
    disp._fetched_at = 0
    assert disp.status()["unknown_reason"] == "sensor_error"
    # Plattform erreichbar: deren Status (inkl. Reservierung) wird durchgereicht
    disp.api.request = lambda *a, **k: (200, {"state": "reserved", "display_name": "Schulhof", "reservation": {"until": "x"}})
    disp._fetched_at = 0
    s = disp.status()
    assert s["offline"] is False and s["state"] == "reserved"


def test_local_display_http(tmp_path):
    import threading
    import urllib.request

    agent = A.Agent(enrolled_state(tmp_path))
    disp = A.LocalDisplay(agent)
    disp.api.request = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    stop = threading.Event()
    port = 18000 + os.getpid() % 1000
    threading.Thread(target=disp.serve, args=(stop, port), daemon=True).start()
    time.sleep(0.3)
    try:
        base = f"http://127.0.0.1:{port}"
        body = json.loads(urllib.request.urlopen(base + "/local/status", timeout=3).read())
        assert body["offline"] is True and body["state"] == "unknown"
        page = urllib.request.urlopen(base + "/local", timeout=3).read()
        assert b"display.js" in page
        assert urllib.request.urlopen(base + "/static/js/display.js", timeout=3).status == 200
        try:
            urllib.request.urlopen(base + "/static/../agent.py", timeout=3)
            raise AssertionError("darf nicht ausgeliefert werden")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        stop.set()
