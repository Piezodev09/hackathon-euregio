"""Gateway: Arduino line parsing, sequence numbers, offline buffer, watchdog."""

from __future__ import annotations

import pytest

from bikeagent.gateway import Gateway, GatewayConfig, ParseError, SequenceCounter, Uplink, parse_line

SLOTS = {"A": "A", "B": "B", "1": "C"}


def cfg(tmp_path, **kw) -> GatewayConfig:
    base = dict(api_url="http://x", station_id="demo-01", slot_map=SLOTS, token="t" * 20, state_dir=tmp_path)
    base.update(kw)
    return GatewayConfig(**base)


def test_parse_valid():
    assert parse_line('{"slot_id":"A","presence":1,"vibration":12,"seq":1042}', SLOTS) == {
        "slot_id": "A",
        "occupied": True,
        "vibration_score": 12,
        "sensor_state": "ok",
    }


def test_parse_maps_slot_ids():
    assert parse_line('{"slot_id":"1","presence":0}', SLOTS)["slot_id"] == "C"


def test_parse_sensor_error_is_never_free():
    m = parse_line('{"slot_id":"A","presence":-1,"vibration":400}', SLOTS)
    assert m["occupied"] is None and m["sensor_state"] == "error" and m["vibration_score"] == 0
    m = parse_line('{"slot_id":"A","presence":0,"state":"error"}', SLOTS)
    assert m["occupied"] is None


def test_parse_info_lines_ignored():
    assert parse_line('{"type":"hello","fw":"0.1"}', SLOTS) is None
    assert parse_line("   ", SLOTS) is None


@pytest.mark.parametrize(
    "line",
    [
        "garbage",
        "[1,2]",
        '{"slot_id":"Z","presence":1}',
        '{"slot_id":"A","presence":2}',
        '{"slot_id":"A","presence":true}',
        '{"slot_id":"A","presence":1,"vibration":2000}',
        '{"slot_id":"A","presence":1,"vibration":"x"}',
        '{"slot_id":"A","presence":1,"state":"weird"}',
        '{"presence":1}',
        '{"slot_id":"A","presence":1,"x":"' + "a" * 600 + '"}',
    ],
)
def test_parse_rejects(line):
    with pytest.raises(ParseError):
        parse_line(line, SLOTS)


def test_sequence_monotonic_across_restart(tmp_path):
    p = tmp_path / "seq"
    a = SequenceCounter(p, persist_every=10)
    vals = [a.next() for _ in range(25)]
    assert vals == sorted(set(vals))
    b = SequenceCounter(p, persist_every=10)  # "restart"
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
    gw.handle_line('{"slot_id":"A","presence":1}')
    gw.watchdog()
    assert len(gw.uplink.buffer) == 1
    clock.t += 16
    gw.watchdog()
    bodies = [q.body for q in gw.uplink.buffer][1:]
    assert {b["slot_id"] for b in bodies} == {"A", "B", "C"}
    assert all(b["sensor_state"] == "error" and b["occupied"] is None for b in bodies)
    gw.watchdog()  # do not repeat immediately
    assert len(gw.uplink.buffer) == 4
    clock.t += 10
    gw.watchdog()
    assert len(gw.uplink.buffer) == 7


def test_gateway_adds_station_and_source(tmp_path):
    c = cfg(tmp_path)
    c.source = "simulated"
    gw = Gateway(c, uplink=Uplink(c, post=FakeApi()))
    gw.handle_line('{"slot_id":"B","presence":0,"vibration":3}')
    body = gw.uplink.buffer[0].body
    assert body["station_id"] == "demo-01" and body["source"] == "simulated" and body["slot_id"] == "B"
    assert isinstance(body["sequence"], int)
