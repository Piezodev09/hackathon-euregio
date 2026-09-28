"""Agent: state file, HTTPS policy, safe package extraction, self-update, rollback, remote commands."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from bikeagent import agent as A


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
    s.data = {"token": "bsd_secret", "api_url": "https://x"}
    s.save()
    assert (s.path.stat().st_mode & 0o777) == 0o600
    os.chmod(s.path, 0o644)
    assert A.State(tmp_path / "st").load()["token"] == "bsd_secret"
    assert (s.path.stat().st_mode & 0o777) == 0o600


def test_api_requires_https_except_localhost():
    with pytest.raises(A.AgentError):
        A.Api("http://bikes.example.org")
    A.Api("http://127.0.0.1:8000")
    A.Api("http://bikes.example.org", allow_http=True)
    api = A.Api("https://bikes.example.org")
    with pytest.raises(A.AgentError):
        api.request("GET", "https://evil.example/agent.tar.gz")


@pytest.mark.parametrize("bad", ["../evil.py", "/etc/passwd", "sub/dir.py", "bikeagent/../../x.py", "bikeagent/a/b.py"])
def test_safe_extract_rejects_path_tricks(tmp_path, bad):
    with pytest.raises(A.AgentError):
        A.safe_extract(make_tgz({bad: b"x"}), tmp_path / "out")


def test_safe_extract_rejects_symlinks(tmp_path):
    def add_link(tar):
        info = tarfile.TarInfo("bikeagent/agent.py")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/shadow"
        tar.addfile(info)
    with pytest.raises(A.AgentError):
        A.safe_extract(make_tgz({"VERSION": b"1"}, add_link), tmp_path / "out")


def layout(tmp_path, version="1.0.0"):
    prefix = tmp_path / "opt"
    rel = prefix / "releases" / version
    (rel / "bikeagent").mkdir(parents=True)
    (rel / "bikeagent" / "agent.py").write_text("# old")
    (rel / "VERSION").write_text(version)
    (prefix / "current").symlink_to(rel)
    return prefix, rel / "bikeagent" / "agent.py"


class FakeApi:
    def __init__(self, data: bytes, status=200):
        self.data, self.status = data, status

    def request(self, method, path, body=None, token=None, raw=False, max_bytes=0):
        return self.status, self.data


def test_update_verifies_hash_and_switches(tmp_path):
    prefix, agent_file = layout(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pkg = make_tgz({"VERSION": b"1.1.0", "bikeagent/agent.py": b"# new", "bikeagent/gateway.py": b"", "bikeagent/__init__.py": b""})
    with pytest.raises(A.AgentError):
        A.apply_update(FakeApi(pkg), "t", {"version": "1.1.0", "sha256": "0" * 64, "url": "x"}, state_dir, agent_file)
    assert (prefix / "current").resolve().name == "1.0.0"
    ok = A.apply_update(FakeApi(pkg), "t", {"version": "1.1.0", "sha256": hashlib.sha256(pkg).hexdigest(), "url": "x"},
                        state_dir, agent_file)
    assert ok and (prefix / "current").resolve().name == "1.1.0"
    assert (prefix / "current" / "bikeagent" / "agent.py").read_text() == "# new"
    assert json.loads((state_dir / "update-pending.json").read_text())["previous"].endswith("1.0.0")


def test_update_rejects_version_mismatch(tmp_path):
    prefix, agent_file = layout(tmp_path)
    (tmp_path / "state").mkdir()
    pkg = make_tgz({"VERSION": b"6.6.6", "bikeagent/agent.py": b""})
    with pytest.raises(A.AgentError):
        A.apply_update(FakeApi(pkg), "t", {"version": "1.1.0", "sha256": hashlib.sha256(pkg).hexdigest(), "url": "x"},
                       tmp_path / "state", agent_file)


def test_automatic_rollback_after_failed_starts(tmp_path):
    prefix, old_agent = layout(tmp_path, "1.0.0")
    new = prefix / "releases" / "1.1.0"
    (new / "bikeagent").mkdir(parents=True)
    (new / "bikeagent" / "agent.py").write_text("")
    A.switch_current(prefix, new)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "update-pending.json").write_text(json.dumps({"previous": str(old_agent.parent.parent), "version": "1.1.0", "starts": 0}))
    A.check_rollback(state_dir, new / "bikeagent" / "agent.py")
    A.check_rollback(state_dir, new / "bikeagent" / "agent.py")
    with pytest.raises(SystemExit):
        A.check_rollback(state_dir, new / "bikeagent" / "agent.py")
    assert (prefix / "current").resolve().name == "1.0.0"
    assert not (state_dir / "update-pending.json").exists()


def test_confirm_update_removes_marker(tmp_path):
    (tmp_path / "update-pending.json").write_text("{}")
    A.confirm_update(tmp_path)
    assert not (tmp_path / "update-pending.json").exists()


def enrolled_state(tmp_path) -> A.State:
    s = A.State(tmp_path)
    s.data = {"api_url": "http://127.0.0.1:1", "device_id": "dev_1", "token": "bsd_old", "token_issued_at": 0,
              "station_id": "st_1", "slot_map": {"A": "A"}, "config_version": 1, "source": "simulator"}
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
    ag.api = RecordingApi({"/api/v1/agent/rotate-token": (200, {"token": "bsd_new"})})
    ag.apply({"config_version": 2, "slot_map": {"A": "A", "B": "B"}, "commands": ["rotate_token", "restart", "evil"]})
    assert ag.cfg.slot_map == {"A": "A", "B": "B"}  # same dict that parse_line uses
    assert ag.gw.cfg.slot_map is ag.cfg.slot_map
    saved = json.loads(s.path.read_text())
    assert saved["config_version"] == 2 and saved["token"] == "bsd_new"
    assert ag.cfg.token == "bsd_new" and ag.stop.is_set() and ag.exit_code == A.EXIT_RESTART


def test_agent_rotates_old_token_automatically(tmp_path):
    s = enrolled_state(tmp_path)
    ag = A.Agent(s, clock=lambda: A.TOKEN_MAX_AGE_S + 10)
    ag.api = RecordingApi({"/api/v1/agent/rotate-token": (200, {"token": "bsd_fresh"})})
    ag.apply({"config_version": 1, "slot_map": {"A": "A"}, "commands": []})
    assert ag.cfg.token == "bsd_fresh"


def test_simulator_source_marks_data_simulated(tmp_path):
    ag = A.Agent(enrolled_state(tmp_path))
    assert ag.cfg.source == "simulated"
