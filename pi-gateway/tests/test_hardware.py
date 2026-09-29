"""Hardware-Erkennung und mehrere Stellplätze an einem Pi (ohne echte Geräte: gefälschte /dev- und /proc-Daten)."""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent as A  # noqa: E402
import hardware as H  # noqa: E402

PROC = """I: Bus=0003 Vendor=046d Product=c31c Version=0110
N: Name="Logitech USB Keyboard"
P: Phys=usb-0000:01:00.0-1.2/input0
H: Handlers=sysrq kbd leds event0

I: Bus=0003 Vendor=ffff Product=0035 Version=0110
N: Name="Sycreader RFID Technology Co., Ltd SYC ID&IC USB Reader"
P: Phys=usb-0000:01:00.0-1.3/input0
H: Handlers=sysrq kbd leds event3

I: Bus=0003 Vendor=08ff Product=0009 Version=0110
N: Name="HID 08ff:0009"
P: Phys=usb-0000:01:00.0-1.4/input0
H: Handlers=kbd event4

I: Bus=0003 Vendor=1234 Product=0001 Version=0110
N: Name="Generic Mouse"
H: Handlers=mouse0 event5
"""


def test_scan_serial_uses_stable_names(tmp_path):
    dev = tmp_path / "dev"
    by_id = dev / "serial" / "by-id"
    by_id.mkdir(parents=True)
    (dev / "ttyACM0").touch()
    (dev / "ttyUSB0").touch()
    (dev / "ttyUSB1").touch()
    (by_id / "usb-Arduino__www.arduino.cc__0043_7563-if00").symlink_to(dev / "ttyACM0")
    (by_id / "usb-1a86_USB_Serial-if00-port0").symlink_to(dev / "ttyUSB0")
    ports = H.scan_serial(dev)
    kinds = {p["kind"] for p in ports}
    assert kinds == {"arduino", "ch340", "usb-serial"}
    assert [p["real"] for p in ports] == [str(dev / "ttyUSB0"), str(dev / "ttyACM0"), str(dev / "ttyUSB1")]


def test_assign_fixed_first_then_stable_order():
    stalls = ["a", "b", "c"]
    assert H.assign(stalls, ["p1", "p2"], {}) == {"a": "p1", "b": "p2", "c": None}
    assert H.assign(stalls, ["p1", "p2"], {"b": "p1"}) == {"a": "p2", "b": "p1", "c": None}
    # fest zugeordnetes, aber fehlendes Gerät: Stellplatz bekommt nichts anderes (sonst würde er falsche Daten zeigen)
    assert H.assign(stalls, ["p1", "p2"], {"a": "weg"}) == {"a": None, "b": "p1", "c": "p2"}
    assert H.assign(stalls, ["p1", "p2", "p3"], {}, preferred=["p3"])["a"] == "p3"


def test_uid_formats_match_server():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.parking import uid_from as server_uid_from
    for text, fmt in (("0012345678", "dec"), ("0012345678", "dec_rev"), ("04aa0001", "hex"), ("3281318004", "dec_rev")):
        assert H.uid_from(text, fmt) == server_uid_from(text, fmt)
    assert H.uid_from("0012345678") == H.uid_from("0012345678", "dec_rev")  # auto: Ziffern -> dec_rev
    assert H.uid_from("04AA0001") == "04AA0001"
    assert H.uid_from("12AB5") is None and H.uid_from("") is None


def test_scan_hid_readers_never_takes_the_keyboard(tmp_path):
    proc = tmp_path / "devices"
    proc.write_text(PROC)
    found = H.scan_hid_readers(proc)
    assert [r["event"] for r in found] == ["/dev/input/event3", "/dev/input/event4"]
    assert all(r["kind"] == "hid" and r["id"].startswith("hid:") for r in found)
    assert "Logitech" not in json.dumps(found)
    assert H.scan_hid_readers(tmp_path / "fehlt") == []


def test_hid_reader_feed_decodes_keys():
    got = []
    r = H.HidReader({"id": "hid:x", "name": "R"}, lambda rid, uid: got.append((rid, uid)))
    for ch in "0012345678":
        code = {"0": 11, **{str(i): i + 1 for i in range(1, 10)}}[ch]
        r.feed(code, 1)
        r.feed(code, 0)  # Loslassen wird ignoriert
    assert r.feed(H.KEY_ENTER, 1) == "4E61BC00"
    assert got == [("hid:x", "4E61BC00")]
    assert struct.calcsize(H.EVENT_FMT) == H.EVENT_SIZE


def test_pcsc_reader_reports_card_once():
    got = []
    cards = [([0x04, 0xAA, 0x00, 0x01], 0x90, 0x00)]

    def connect():
        if not cards:
            raise RuntimeError("keine Karte")
        return lambda apdu: cards[0] if apdu == H.GET_UID else ([], 0x6A, 0x81)

    r = H.PcscReader({"id": "pcsc:acr", "name": "ACR122U"}, lambda rid, uid: got.append(uid), connect=connect)
    assert r.poll_once() == "04AA0001"
    assert r.poll_once() is None  # liegt noch auf
    cards.clear()
    r.poll_once()
    cards.append(([0x04, 0xAA, 0x00, 0x01], 0x90, 0x00))
    assert r.poll_once() == "04AA0001" and got == ["04AA0001", "04AA0001"]


def two_stall_state(tmp_path) -> A.State:
    s = A.State(tmp_path)
    s.data = {"api_url": "http://127.0.0.1:1", "source": "simulator", "stalls": [
        {"station_id": "st_a", "station_name": "Platz A", "device_id": "dev_a", "token": "bsd_a", "token_issued_at": 0, "config_version": 1},
        {"station_id": "st_b", "station_name": "Platz B", "device_id": "dev_b", "token": "bsd_b", "token_issued_at": 0, "config_version": 1},
    ]}
    s.save()
    return s


def test_legacy_state_migrates_and_mirrors(tmp_path):
    s = A.State(tmp_path)
    s.data = {"api_url": "https://x", "device_id": "dev_1", "token": "bsd_alt", "token_issued_at": 5, "station_id": "st_1",
              "station_name": "Hof", "config_version": 3, "serial_port": "/dev/ttyACM0"}
    s.save()
    s2 = A.State(tmp_path).load()
    st = s2["stalls"][0]
    assert st["station_id"] == "st_1" and st["token"] == "bsd_alt" and st["legacy"] and st["assign_port"] == "/dev/ttyACM0"
    s2.data["stalls"][0]["token"] = "bsd_neu"
    s2.save()
    raw = json.loads(s2.path.read_text())
    assert raw["token"] == "bsd_neu" and raw["station_id"] == "st_1"  # Rollback auf 1.3 bleibt möglich


def test_enroll_adds_stalls_to_existing_pi(tmp_path, monkeypatch):
    responses = [
        {"api_url": "https://radstation.example.org", "gateway_id": "gw_1", "heartbeat_s": 60, "device_id": "dev_a", "token": "bsd_a",
         "station_id": "st_a", "station_name": "A", "config_version": 1,
         "stalls": [{"station_id": "st_a", "station_name": "A", "device_id": "dev_a", "token": "bsd_a", "config_version": 1}]},
        {"api_url": "https://radstation.example.org", "gateway_id": "gw_2", "device_id": "dev_b", "token": "bsd_b",
         "station_id": "st_b", "station_name": "B", "config_version": 1,
         "stalls": [{"station_id": "st_b", "station_name": "B", "device_id": "dev_b", "token": "bsd_b", "config_version": 1},
                    {"station_id": "st_c", "station_name": "C", "device_id": "dev_c", "token": "bsd_c", "config_version": 1}]},
    ]

    class FakeApi:
        def __init__(self, *a, **kw):
            pass

        def request(self, method, path, body=None, **kw):
            return 200, responses.pop(0)

    monkeypatch.setattr(A, "Api", FakeApi)
    s = A.State(tmp_path)
    A.enroll(s, "https://radstation.example.org", "ABCDE-FGHJK", "serial", "auto", None, False)
    data = A.enroll(s, "https://radstation.example.org", "LMNPQ-RSTUV", "serial", "auto", None, False)
    assert [st["station_id"] for st in data["stalls"]] == ["st_a", "st_b", "st_c"]
    assert A.State(tmp_path).load()["station_id"] == "st_a"


def test_agent_with_two_stalls_routes_readers_and_commands(tmp_path):
    ag = A.Agent(two_stall_state(tmp_path))
    a, b = ag.stalls
    sent = []
    for st in ag.stalls:
        st.gw.run_async = lambda f, *args: None
        st.gw.tap = lambda uid, reader=None, st=st: sent.append((st.station_id, uid, reader))
    a.reader, b.reader = "hid:1", "pcsc:acr"
    ag.on_reader_uid("pcsc:acr", "04AA0001")
    ag.on_reader_uid("hid:1", "04BB0002")
    ag.on_reader_uid("hid:unbekannt", "04CC0003")  # keinem Stellplatz zugeordnet -> verworfen
    assert sent == [("st_b", "04AA0001", "pcsc:acr"), ("st_a", "04BB0002", "hid:1")]
    # Zuordnung aus dem Portal wird gespeichert, Identifizieren schreibt an den Arduino des Stellplatzes
    written = []
    b.gw.write_back = written.append
    ag.api = type("X", (), {"request": lambda *a, **k: (200, {})})()
    ag.apply({"config_version": 1, "commands": ["identify"], "assign": {"port": "/dev/serial/by-id/x", "reader": None}}, b)
    assert written == ["IDENT\n"]
    saved = json.loads((tmp_path / "agent.json").read_text())
    assert saved["stalls"][1]["assign_port"] == "/dev/serial/by-id/x"
    hw = b.hw()
    assert hw["stalls"] == 2 and hw["camera"] and "port" in hw
    # PN532 am Arduino: Leser-Kennung folgt dem Port
    a.reader, a.port, a.gw.has_pn532 = None, "/dev/serial/by-id/usb-Arduino", True
    assert a.reader_id() == "pn532@/dev/serial/by-id/usb-Arduino"


def test_hello_line_is_recorded():
    import gateway as G
    ag_gw = G.Gateway(G.GatewayConfig(api_url="http://127.0.0.1:1", station_id="s", token="t", state_dir=Path(__import__("tempfile").mkdtemp())))
    ag_gw.handle_line('{"type":"hello","fw":"0.4.0","nfc":true}')
    assert ag_gw.hello == {"fw": "0.4.0", "name": "bike-stall", "nfc": True} and ag_gw.has_pn532


def test_local_overview_lists_all_stalls(tmp_path):
    ag = A.Agent(two_stall_state(tmp_path))
    disp = A.LocalDisplay(ag)
    disp.api.request = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    ag.stalls[1].gw.handle_line('{"presence":1,"vibration":0,"seq":1}')
    ov = disp.overview()
    assert [s["display_name"] for s in ov["stalls"]] == ["Platz A", "Platz B"]
    assert [s["state"] for s in ov["stalls"]] == ["unknown", "occupied"] and all(s["offline"] for s in ov["stalls"])
    assert disp.status("st_b")["state"] == "occupied" and disp.status("fehlt")["station_id"] == "st_a"
