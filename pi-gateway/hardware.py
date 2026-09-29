"""Hardware-Erkennung am Raspberry Pi – ohne Zusatzpakete (nur PC/SC braucht pyscard, falls vorhanden).

  * Serielle Geräte (Arduino Uno/Nano, CH340, FTDI, CP210x) über /dev/serial/by-id: stabile Namen, auch nach
    Umstecken. Fallback: /dev/ttyACM*, /dev/ttyUSB*.
  * NFC-/RFID-Leser im Tastaturmodus (USB-HID): werden exklusiv geöffnet (EVIOCGRAB), damit die Nummer nicht in
    einem anderen Programm landet. Erkannt werden nur Geräte, deren Name eindeutig nach Kartenleser aussieht, oder die
    in der Konfiguration stehen – eine normale Tastatur wird nie übernommen.
  * PC/SC-Leser (z. B. ACR122U) über pcscd + pyscard: UID per APDU FF CA 00 00 00.
  * Zuordnung Ports/Leser -> Stellplätze: feste Zuordnung aus dem Portal zuerst, der Rest in stabiler Reihenfolge.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger("hardware")

# ---------------------------------------------------------------------- serielle Geräte
_KINDS = (("arduino", "arduino"), ("2341", "arduino"), ("2a03", "arduino"), ("1a86", "ch340"), ("ch340", "ch340"),
          ("ftdi", "ftdi"), ("0403", "ftdi"), ("silicon_labs", "cp210x"), ("cp210", "cp210x"), ("10c4", "cp210x"))


def serial_kind(name: str) -> str:
    low = name.lower()
    return next((k for key, k in _KINDS if key in low), "usb-serial")


def scan_serial(dev: Path = Path("/dev")) -> list[dict]:
    """Serielle USB-Geräte, sortiert. path = stabiler Name (by-id), real = aktuelles Gerät (/dev/ttyACM0)."""
    out, seen = [], set()
    by_id = dev / "serial" / "by-id"
    if by_id.is_dir():
        for p in sorted(by_id.iterdir()):
            try:
                real = str(p.resolve())
            except OSError:
                continue
            seen.add(real)
            out.append({"path": str(p), "real": real, "kind": serial_kind(p.name)})
    for pattern in ("ttyACM*", "ttyUSB*"):
        for p in sorted(dev.glob(pattern)):
            if str(p) not in seen:
                out.append({"path": str(p), "real": str(p), "kind": "acm" if "ACM" in p.name else "usb-serial"})
    return out


def assign(stalls: list[str], items: list[str], fixed: dict[str, str | None], preferred: Iterable[str] = ()) -> dict[str, str | None]:
    """Stellplätze (in Reihenfolge) -> Hardware. Feste Zuordnung zuerst (wenn vorhanden), dann bevorzugte
    (z. B. Arduino mit unserer Firmware), dann der Rest – jede Hardware höchstens einmal."""
    result: dict[str, str | None] = {s: None for s in stalls}
    free = [i for i in items]
    for s in stalls:
        want = fixed.get(s)
        if want and want in free:
            result[s] = want
            free.remove(want)
    pref = [i for i in preferred if i in free]
    order = pref + [i for i in free if i not in pref]
    for s in stalls:
        if result[s] is None and not fixed.get(s) and order:
            result[s] = order.pop(0)
    return result


# ---------------------------------------------------------------------- UIDs
def uid_from(value: str, fmt: str = "auto") -> str | None:
    """Text eines Tastatur-Lesers -> Hex-UID wie am PN532. None, wenn es keine UID ist.

    fmt: "hex" | "dec" (Bytes in Lese-Reihenfolge) | "dec_rev" (Bytes umgekehrt, häufig bei 10 Ziffern) | "auto"
    ("auto" = Hex, wenn A–F vorkommt oder die Länge 8/14/20 Zeichen ist (4/7/10 Byte), sonst dec_rev – gleiche Regel
    wie im Portal beim Anlegen einer Karte)."""
    v = re.sub(r"[\s:\-]", "", value or "").upper()
    if not v or len(v) > 20:
        return None
    if fmt == "auto":
        fmt = "hex" if re.search(r"[A-F]", v) or len(v) in (8, 14, 20) else "dec_rev"
    if fmt == "hex":
        return v if re.fullmatch(r"[0-9A-F]{8,20}", v) and len(v) % 2 == 0 else None
    if not v.isdigit():
        return None
    n = int(v)
    length = 4 if n < 2**32 else 7 if n < 2**56 else 10
    if n >= 2 ** (8 * length):
        return None
    raw = n.to_bytes(length, "big")
    return (raw[::-1] if fmt == "dec_rev" else raw).hex().upper()


# ---------------------------------------------------------------------- USB-Leser im Tastaturmodus
READER_NAME_RE = re.compile(r"rfid|nfc|card.?reader|ic.?reader|id.?reader|mifare|em4100|sycreader|125khz|13\.56", re.I)
# verbreitete günstige Leser, die sich nur als Tastatur ohne sprechenden Namen melden
KNOWN_HID_IDS = {"ffff:0035", "08ff:0009", "413d:2107"}
EVENT_FMT = "llHHi"  # struct input_event (64-bit: timeval 16 Byte)
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EV_KEY = 1
EVIOCGRAB = 0x40044590
KEYS = {2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
        79: "1", 80: "2", 81: "3", 75: "4", 76: "5", 77: "6", 71: "7", 72: "8", 73: "9", 82: "0",
        30: "A", 48: "B", 46: "C", 32: "D", 18: "E", 33: "F"}
KEY_ENTER, KEY_KPENTER = 28, 96


def scan_hid_readers(proc: Path = Path("/proc/bus/input/devices"), extra_names: Iterable[str] = ()) -> list[dict]:
    """Tastatur-Geräte, deren Name nach Kartenleser aussieht (oder in extra_names steht)."""
    try:
        text = proc.read_text(errors="replace")
    except OSError:
        return []
    wanted = [n.lower() for n in extra_names if n]
    out = []
    for block in text.split("\n\n"):
        name = re.search(r'^N: Name="(.*)"', block, re.M)
        handlers = re.search(r"^H: Handlers=(.*)$", block, re.M)
        phys = re.search(r"^P: Phys=(.*)$", block, re.M)
        ids = re.search(r"Vendor=([0-9a-f]{4}) Product=([0-9a-f]{4})", block)
        if not (name and handlers):
            continue
        ev = re.search(r"\b(event\d+)\b", handlers.group(1))
        if not ev or "kbd" not in handlers.group(1):
            continue
        n = name.group(1)
        vid = f"{ids.group(1)}:{ids.group(2)}" if ids else ""
        if not (READER_NAME_RE.search(n) or vid in KNOWN_HID_IDS or any(w in n.lower() for w in wanted)):
            continue
        stable = re.sub(r"[^A-Za-z0-9_.:-]", "_", f"{vid}-{phys.group(1) if phys else ev.group(1)}")[:120]
        out.append({"id": f"hid:{stable}", "kind": "hid", "name": n[:100], "event": f"/dev/input/{ev.group(1)}"})
    return out


class HidReader:
    """Liest Ziffern bis Enter und meldet die UID. Exklusiver Zugriff: Eingaben gehen nicht an andere Programme."""

    def __init__(self, info: dict, on_uid: Callable[[str, str], None], fmt: str = "auto"):
        self.info, self.on_uid, self.fmt = info, on_uid, fmt
        self.buf = ""

    def feed(self, code: int, value: int) -> str | None:
        """Ein Tastenereignis verarbeiten (value 1 = gedrückt). Gibt die UID bei Enter zurück."""
        if value != 1:
            return None
        if code in (KEY_ENTER, KEY_KPENTER):
            text, self.buf = self.buf, ""
            uid = uid_from(text, self.fmt)
            if uid:
                self.on_uid(self.info["id"], uid)
            elif text:
                log.warning("Leser %s: unbekanntes Format %r", self.info["name"], text[-6:])
            return uid
        ch = KEYS.get(code)
        if ch and len(self.buf) < 24:
            self.buf += ch
        return None

    def run(self, stop: threading.Event) -> None:
        import fcntl

        while not stop.is_set():
            try:
                fd = os.open(self.info["event"], os.O_RDONLY)
            except OSError as exc:
                log.warning("Leser %s nicht lesbar (%s) – Gruppe 'input'?", self.info["name"], exc)
                stop.wait(10)
                continue
            try:
                try:
                    fcntl.ioctl(fd, EVIOCGRAB, 1)
                except OSError:
                    log.warning("Leser %s: exklusiver Zugriff nicht möglich", self.info["name"])
                log.info("NFC-Leser (USB, Tastaturmodus) aktiv: %s", self.info["name"])
                while not stop.is_set():
                    data = os.read(fd, EVENT_SIZE * 16)
                    if not data:
                        break
                    for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                        _, _, typ, code, value = struct.unpack_from(EVENT_FMT, data, off)
                        if typ == EV_KEY:
                            self.feed(code, value)
            except OSError as exc:
                if exc.errno != errno.ENODEV:
                    log.warning("Leser %s: %s", self.info["name"], exc)
            finally:
                os.close(fd)
            stop.wait(2)  # abgezogen: der Scan startet ihn neu


# ---------------------------------------------------------------------- PC/SC (ACR122U u. ä.)
GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]


def pcsc_available() -> bool:
    try:
        import smartcard.System  # noqa: F401
        return True
    except Exception:
        return False


def scan_pcsc() -> list[dict]:
    if not pcsc_available():
        return []
    try:
        from smartcard.System import readers
        return [{"id": f"pcsc:{re.sub(r'[^A-Za-z0-9_.-]', '_', str(r))[:100]}", "kind": "pcsc", "name": str(r)[:100], "reader": str(r)}
                for r in readers()]
    except Exception as exc:  # pcscd läuft nicht
        log.debug("PC/SC nicht verfügbar: %s", exc)
        return []


class PcscReader:
    """Fragt den Leser alle 0,3 s ab. Eine liegende Karte wird nur einmal gemeldet (bis sie entfernt wird)."""

    def __init__(self, info: dict, on_uid: Callable[[str, str], None], connect=None):
        self.info, self.on_uid = info, on_uid
        self._connect = connect  # für Tests: () -> transmit(apdu) -> (data, sw1, sw2), wirft bei fehlender Karte
        self.present: str | None = None

    def poll_once(self) -> str | None:
        try:
            transmit = self._connect() if self._connect else self._real_connect()
            data, sw1, sw2 = transmit(GET_UID)
        except Exception:
            self.present = None
            return None
        if (sw1, sw2) != (0x90, 0x00) or not 4 <= len(data) <= 10:
            return None
        uid = bytes(data).hex().upper()
        if uid != self.present:
            self.present = uid
            self.on_uid(self.info["id"], uid)
            return uid
        return None

    def _real_connect(self):
        from smartcard.System import readers
        r = next(x for x in readers() if str(x) == self.info["reader"])
        conn = r.createConnection()
        conn.connect()
        return conn.transmit

    def run(self, stop: threading.Event) -> None:
        log.info("NFC-Leser (PC/SC) aktiv: %s", self.info["name"])
        while not stop.is_set():
            self.poll_once()
            stop.wait(0.3)


# ---------------------------------------------------------------------- Überblick
def camera_kind() -> str:
    from shutil import which
    if which("rpicam-still") or which("libcamera-still"):
        return "rpicam"
    if which("fswebcam") and any(Path("/dev").glob("video*")):
        return "usb"
    return "none"


def kiosk_configured(home: Path | None = None) -> bool:
    """Wurde der Display-Autostart vom Installationsskript eingerichtet?"""
    return Path("/etc/xdg/autostart/bike-display.desktop").exists() or bool(
        home and (home / ".config" / "autostart" / "bike-display.desktop").exists())


def summary(ports: list[dict], readers: list[dict]) -> str:
    kinds: dict[str, int] = {}
    for p in ports:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    parts = [f"{n}× {k}" for k, n in kinds.items()] or ["kein serielles Gerät"]
    parts += [f"Leser: {r['name']} ({r['kind']})" for r in readers]
    return ", ".join(parts)


if __name__ == "__main__":  # vom Installationsskript genutzt: kurze Übersicht der erkannten Hardware
    import json
    import sys
    ports, readers = scan_serial(), scan_hid_readers() + scan_pcsc()
    if "--json" in sys.argv:
        print(json.dumps({"ports": ports, "readers": readers, "camera": camera_kind()}))
    else:
        print(f"Seriell: {', '.join(p['kind'] + ' ' + p['path'] for p in ports) or 'keine'}")
        print(f"NFC-Leser (USB/PC-SC): {', '.join(r['name'] + ' (' + r['kind'] + ')' for r in readers) or 'keine'}")
        print(f"Kamera: {camera_kind()}")
