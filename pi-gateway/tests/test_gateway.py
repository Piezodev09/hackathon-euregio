from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway import Gateway, GatewayConfig, ParseError, SequenceCounter, Uplink, parse_line  # noqa: E402


def cfg(tmp_path, **kw) -> GatewayConfig:
    base = dict(api_url="http://x", station_id="demo-01", token="t" * 20, state_dir=tmp_path)
    base.update(kw)
    return GatewayConfig(**base)


def test_parse_valid():
    assert parse_line('{"presence":1,"vibration":12,"seq":1042}') == {
        "occupied": True,
        "vibration_score": 12,
        "sensor_state": "ok",
    }


def test_parse_accepts_legacy_slot_a_only():
    assert parse_line('{"slot_id":"A","presence":0}')["occupied"] is False
    with pytest.raises(ParseError):
        parse_line('{"slot_id":"B","presence":0}')


def test_parse_sensor_error_is_never_free():
    m = parse_line('{"presence":-1,"vibration":400}')
    assert m["occupied"] is None and m["sensor_state"] == "error" and m["vibration_score"] == 0
    m = parse_line('{"presence":0,"state":"error"}')
    assert m["occupied"] is None


def test_parse_info_lines_ignored():
    assert parse_line('{"type":"hello","fw":"0.1"}') is None
    assert parse_line("   ") is None


@pytest.mark.parametrize(
    "line",
    [
        "garbage",
        "[1,2]",
        '{"slot_id":"Z","presence":1}',
        '{"presence":2}',
        '{"presence":true}',
        '{"presence":1,"vibration":2000}',
        '{"presence":1,"vibration":"x"}',
        '{"presence":1,"state":"weird"}',
        '{"vibration":1}',
        '{"presence":1,"x":"' + "a" * 600 + '"}',
    ],
)
def test_parse_rejects(line):
    with pytest.raises(ParseError):
        parse_line(line)


def test_sequence_monotonic_across_restart(tmp_path):
    p = tmp_path / "seq"
    a = SequenceCounter(p, persist_every=10)
    vals = [a.next() for _ in range(25)]
    assert vals == sorted(set(vals))
    b = SequenceCounter(p, persist_every=10)  # "Neustart"
    assert b.next() > vals[-1]


class FakeApi:
    def __init__(self):
        self.calls = []
        self.status = 202
        self.fail = False

    def __call__(self, path, payload):
        if self.fail:
            raise OSError("network down")
        self.calls.append((path, payload))
        return self.status


def test_uplink_buffers_while_offline_and_replays(tmp_path):
    api = FakeApi()
    up = Uplink(cfg(tmp_path), post=api)
    changes = []
    up.on_status_change = changes.append
    api.fail = True
    up.enqueue({"slot_id": "A", "sequence": 1})
    up.enqueue({"slot_id": "A", "sequence": 2})
    assert up.flush_once() is False
    assert len(up.buffer) == 2
    api.fail = False
    assert up.flush_once() is True
    assert len(up.buffer) == 0
    sent = api.calls[0][1]["measurements"]
    assert [m["sequence"] for m in sent] == [1, 2]
    assert all("age_ms" in m for m in sent)
    assert changes == [False, True]


def test_uplink_buffer_is_bounded(tmp_path):
    up = Uplink(cfg(tmp_path, buffer_max=3), post=FakeApi())
    for i in range(5):
        up.enqueue({"sequence": i})
    assert [q.body["sequence"] for q in up.buffer] == [2, 3, 4]


def test_uplink_keeps_data_on_auth_error_drops_on_422(tmp_path):
    api = FakeApi()
    up = Uplink(cfg(tmp_path), post=api)
    up.enqueue({"sequence": 1})
    api.status = 401
    assert up.flush_once() is False and len(up.buffer) == 1
    api.status = 422
    assert up.flush_once() is True and len(up.buffer) == 0


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_gateway_watchdog_reports_errors_when_arduino_silent(tmp_path):
    api = FakeApi()
    clock = Clock()
    gw = Gateway(cfg(tmp_path), uplink=Uplink(cfg(tmp_path), post=api), clock=clock)
    gw.handle_line('{"presence":1}')
    gw.watchdog()
    assert len(gw.uplink.buffer) == 1
    clock.t += 16
    gw.watchdog()
    bodies = [q.body for q in gw.uplink.buffer][1:]
    assert len(bodies) == 1 and "slot_id" not in bodies[0]
    assert bodies[0]["sensor_state"] == "error" and bodies[0]["occupied"] is None
    gw.watchdog()  # nicht sofort wiederholen
    assert len(gw.uplink.buffer) == 2
    clock.t += 10
    gw.watchdog()
    assert len(gw.uplink.buffer) == 3


def test_gateway_adds_station_and_source(tmp_path):
    c = cfg(tmp_path)
    c.source = "simulated"
    gw = Gateway(c, uplink=Uplink(c, post=FakeApi()))
    gw.handle_line('{"presence":0,"vibration":3}')
    body = gw.uplink.buffer[0].body
    assert body["station_id"] == "demo-01" and body["source"] == "simulated" and "slot_id" not in body
    assert isinstance(body["sequence"], int)


def test_parse_nfc_line():
    assert parse_line('{"type":"nfc","uid":"04a1b2c3d4"}') == {"kind": "nfc", "uid": "04A1B2C3D4"}
    for bad in ('{"type":"nfc","uid":"xyz"}', '{"type":"nfc","uid":"04A1"}', '{"type":"nfc"}'):
        with pytest.raises(ParseError):
            parse_line(bad)


class TapApi:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_gateway_sends_nfc_tap_immediately_and_reports_back(tmp_path):
    import urllib.error

    api = TapApi([urllib.error.URLError("down"), (503, {}), (200, {"result": "checked_in"})])
    c = cfg(tmp_path)
    gw = Gateway(c, uplink=Uplink(c, post=api))
    sent = []
    gw.write_back = sent.append
    done = []
    gw.run_async = lambda f, *a: done.append(f(*a, sleep=lambda s: None))
    gw.handle_line('{"type":"nfc","uid":"04A1B2C3D4"}')
    assert done == ["checked_in"] and sent == ["NFC checked_in\n"]
    assert [p for p, _ in api.calls] == ["/api/v1/nfc/tap"] * 3
    body = api.calls[-1][1]
    assert body["uid"] == "04A1B2C3D4" and body["station_id"] == "demo-01" and "age_ms" in body
    assert len(gw.uplink.buffer) == 0  # nicht im Messwert-Puffer


def test_gateway_nfc_rejected_is_not_retried(tmp_path):
    api = TapApi([(403, {})])
    c = cfg(tmp_path)
    gw = Gateway(c, uplink=Uplink(c, post=api))
    out = []
    gw.run_async = lambda f, *a: out.append(f(*a, sleep=lambda s: None))
    gw.tap("04A1B2C3D4")
    assert out == ["rejected"] and len(api.calls) == 1


def test_uplink_passes_response_hints(tmp_path):
    c = cfg(tmp_path)
    up = Uplink(c, post=lambda p, b: (202, {"stored": 1, "capture": True}))
    hints = []
    up.on_response = hints.append
    up.enqueue({"sequence": 1})
    assert up.flush_once() and hints == [{"stored": 1, "capture": True}]
