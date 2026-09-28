#!/usr/bin/env python3
"""Raspberry-Pi-Gateway der Smart Bicycle Box (Plan 3.1, 5, 10.3).

Aufgaben:
  * JSON-Zeilen vom Arduino (USB-Seriell) lesen, Format und Plausibilität prüfen
  * Arduino-Platzkennung auf Stellplatz der Station abbilden
  * eindeutige, monoton steigende Sequenznummern vergeben (Doppelungsschutz in der API)
  * bei Netzausfall begrenzt puffern und später mit Alter (age_ms) nachsenden
  * schweigt der Arduino, alle Plätze als Sensorfehler melden (-> "unbekannt", nie "frei")
  * Netzstatus an den Arduino zurückmelden ("NET 1"/"NET 0"), damit er ihn lokal anzeigen kann

Nur Standardbibliothek + pyserial (nur für den seriellen Modus).
"""

from __future__ import annotations

import argparse
import json
import re
import logging
import os
import signal
import ssl
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger("gateway")
HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------- Konfiguration
@dataclass
class GatewayConfig:
    api_url: str
    station_id: str
    token: str = field(repr=False)
    serial_port: str = "/dev/ttyACM0"
    baudrate: int = 115200
    ca_file: str | None = None
    buffer_max: int = 500
    batch_size: int = 50
    arduino_timeout_s: float = 15.0
    heartbeat_s: float = 10.0
    http_timeout_s: float = 5.0
    state_dir: Path = HERE / "state"
    source: str = "live"


def load_config(path: str | os.PathLike) -> GatewayConfig:
    with open(path, "rb") as f:
        c = tomllib.load(f)
    token = os.environ.get("BIKE_DEVICE_TOKEN", "")
    token_file = c.get("api", {}).get("token_file")
    if not token and token_file and Path(token_file).exists():
        token = Path(token_file).read_text().strip()
    if not token:
        raise SystemExit("Kein Geräte-Token: BIKE_DEVICE_TOKEN setzen oder api.token_file angeben.")
    api = c["api"]
    st = c["station"]
    ser = c.get("serial", {})
    tim = c.get("timing", {})
    buf = c.get("buffer", {})
    return GatewayConfig(
        api_url=api["url"].rstrip("/"),
        ca_file=api.get("ca_file") or None,
        http_timeout_s=float(api.get("timeout_s", 5)),
        token=token,
        station_id=st["id"],
        serial_port=ser.get("port", "/dev/ttyACM0"),
        baudrate=int(ser.get("baudrate", 115200)),
        arduino_timeout_s=float(tim.get("arduino_timeout_s", 15)),
        heartbeat_s=float(tim.get("heartbeat_s", 10)),
        buffer_max=int(buf.get("max_messages", 500)),
        batch_size=int(buf.get("batch_size", 50)),
        state_dir=Path(c.get("state_dir", HERE / "state")),
    )


# ---------------------------------------------------------------------- Parsing
class ParseError(ValueError):
    pass


NFC_UID_RE = re.compile(r"^[0-9A-F]{8,20}$")  # 4–10 Byte als Hex
NFC_MAX_AGE_S = 60.0


def parse_line(line: str) -> dict | None:
    """Arduino-Zeile -> {'occupied','vibration_score','sensor_state'} für den einen Stellplatz.

    Gibt None für Info-Zeilen (z. B. {"type":"hello"}) zurück, wirft ParseError bei ungültigen Daten.
    Erwartetes Format: {"presence":1,"vibration":12,"seq":1042[,"state":"ok"]}
    presence: 1 = belegt, 0 = frei, -1 = Sensor liefert keinen gültigen Wert.
    Ein "slot_id" älterer Firmware wird nur als "A" akzeptiert – es gibt genau einen Stellplatz.
    """
    line = line.strip()
    if not line:
        return None
    if len(line) > 512:
        raise ParseError("Zeile zu lang")
    try:
        d = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ParseError(f"kein JSON: {exc.msg}") from None
    if not isinstance(d, dict):
        raise ParseError("kein JSON-Objekt")
    if d.get("type") == "nfc":
        uid = str(d.get("uid", "")).upper()
        if not NFC_UID_RE.match(uid):
            raise ParseError("ungültige NFC-UID")
        return {"kind": "nfc", "uid": uid}
    if "presence" not in d:
        if d.get("type") in ("hello", "info", "debug"):
            return None
        raise ParseError("presence fehlt")
    if "slot_id" in d and str(d["slot_id"]) != "A":
        raise ParseError(f"unbekannter Platz {d['slot_id']!r} – nur ein Stellplatz vorgesehen")

    presence = d.get("presence")
    if isinstance(presence, bool) or presence not in (0, 1, -1):
        raise ParseError("presence muss 0, 1 oder -1 sein")

    vib = d.get("vibration", 0)
    if isinstance(vib, bool) or not isinstance(vib, (int, float)):
        raise ParseError("vibration muss eine Zahl sein")
    if vib < 0 or vib > 1023:
        raise ParseError("vibration außerhalb 0..1023")

    state = d.get("state", "ok")
    if state not in ("ok", "error"):
        raise ParseError("state muss ok oder error sein")
    if presence == -1:
        state = "error"

    return {
        "occupied": None if state == "error" else bool(presence),
        "vibration_score": 0 if state == "error" else int(vib),
        "sensor_state": state,
    }


# ---------------------------------------------------------------------- Sequenznummern
class SequenceCounter:
    """Monoton steigende Nummer, auch über Neustarts hinweg (Pi hat oft keine Echtzeituhr).

    Start bei max(gespeicherter Wert + Reserve, aktuelle Zeit in ms). Gespeichert wird nur
    alle `persist_every` Schritte, um die SD-Karte zu schonen; die Reserve deckt die Lücke ab.
    """

    def __init__(self, path: Path, persist_every: int = 100):
        self.path = path
        self.persist_every = persist_every
        self._lock = threading.Lock()
        stored = 0
        try:
            stored = int(path.read_text().strip())
        except (FileNotFoundError, ValueError):
            pass
        self.value = max(stored + persist_every + 1, int(time.time() * 1000))
        self._persist()

    def _persist(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(str(self.value))
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("Sequenzzähler konnte nicht gespeichert werden: %s", exc)

    def next(self) -> int:
        with self._lock:
            self.value += 1
            if self.value % self.persist_every == 0:
                self._persist()
            return self.value


# ---------------------------------------------------------------------- Puffer + Versand
@dataclass
class Queued:
    body: dict
    enqueued: float  # time.monotonic()


class Uplink:
    """Begrenzter Puffer und Versand an die API. Älteste Nachrichten fallen bei Überlauf weg."""

    def __init__(self, cfg: GatewayConfig, post: Callable[[str, dict], "int | tuple[int, dict]"] | None = None):
        self.cfg = cfg
        self.buffer: deque[Queued] = deque(maxlen=cfg.buffer_max)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.online: bool | None = None
        self.on_status_change: Callable[[bool], None] | None = None
        self.on_response: Callable[[dict], None] | None = None  # z. B. {"capture": true} -> Kamerabild
        self._post = post or self._http_post
        ctx = ssl.create_default_context(cafile=cfg.ca_file) if cfg.ca_file else ssl.create_default_context()
        self._ssl = ctx

    def enqueue(self, body: dict) -> None:
        with self.lock:
            if len(self.buffer) == self.buffer.maxlen:
                log.warning("Puffer voll – älteste Nachricht wird verworfen")
            self.buffer.append(Queued(body, time.monotonic()))
        self.wake.set()

    def request(self, path: str, payload: dict) -> tuple[int, dict]:
        """POST mit JSON-Antwort. Wirft URLError/OSError bei Netzfehlern."""
        res = self._post(path, payload)
        return res if isinstance(res, tuple) else (res, {})

    def _http_post(self, path: str, payload: dict) -> tuple[int, dict]:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.cfg.api_url + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.token}"},
        )
        try:
            ctx = self._ssl if self.cfg.api_url.startswith("https") else None
            with urllib.request.urlopen(req, timeout=self.cfg.http_timeout_s, context=ctx) as r:
                raw = r.read(65536)
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {}
                return r.status, body if isinstance(body, dict) else {}
        except urllib.error.HTTPError as e:
            return e.code, {}

    def _set_online(self, ok: bool) -> None:
        if ok != self.online:
            self.online = ok
            log.log(logging.INFO if ok else logging.WARNING, "API %s", "erreichbar" if ok else "NICHT erreichbar")
            if self.on_status_change:
                self.on_status_change(ok)

    def flush_once(self) -> bool:
        """Sendet einen Stapel. True = Puffer leer oder Fortschritt, False = Fehler (Backoff)."""
        with self.lock:
            batch = list(self.buffer)[: self.cfg.batch_size]
        if not batch:
            return True
        now = time.monotonic()
        payload = {
            "measurements": [
                {**q.body, "age_ms": min(86_400_000, int((now - q.enqueued) * 1000))} for q in batch
            ]
        }
        try:
            status, resp = self.request("/api/v1/measurements/batch", payload)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.debug("Versand fehlgeschlagen: %s", exc)
            self._set_online(False)
            return False

        if status in (200, 202):
            self._drop(batch)
            self._set_online(True)
            if resp and self.on_response:
                self.on_response(resp)
            return True
        if status == 422:
            # Dauerhaft ungültig – verwerfen statt endlos wiederholen.
            log.error("API lehnt Nachrichten als ungültig ab (422) – %d verworfen", len(batch))
            self._drop(batch)
            self._set_online(True)
            return True
        if status in (401, 403):
            log.error("API lehnt Geräte-Token ab (%d) – Konfiguration prüfen", status)
        else:
            log.warning("API antwortet mit HTTP %d", status)
        self._set_online(False)
        return False

    def _drop(self, batch: list[Queued]) -> None:
        with self.lock:
            ids = {id(q) for q in batch}
            self.buffer = deque((q for q in self.buffer if id(q) not in ids), maxlen=self.cfg.buffer_max)

    def run(self, stop: threading.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            ok = self.flush_once()
            with self.lock:
                pending = len(self.buffer)
            if ok:
                backoff = 1.0
                if pending == 0:
                    self.wake.wait(timeout=1.0)
                    self.wake.clear()
            else:
                stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)


# ---------------------------------------------------------------------- Gateway
class Gateway:
    def __init__(self, cfg: GatewayConfig, uplink: Uplink | None = None, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.uplink = uplink or Uplink(cfg)
        self.seq = SequenceCounter(cfg.state_dir / "sequence")
        self.clock = clock
        self.last_line_at: float | None = None
        self.last_fault_sent: float | None = None
        self.write_back: Callable[[str], None] | None = None
        self.uplink.on_status_change = self._net_status
        self.last_tap: dict | None = None
        self.hello: dict | None = None  # Kennung der Firmware (erste Zeile nach dem Verbinden)
        self.has_pn532 = False  # Arduino hat einen eigenen NFC-Leser (hello "nfc" oder erste NFC-Zeile)
        # Letzter Sensorzustand für die lokale Offline-Anzeige (agent.py LocalDisplay)
        self.last_local: dict | None = None
        # Taps werden sofort (nicht gepuffert) gesendet; eigener Thread, damit das Lesen weiterläuft.
        self.run_async: Callable[..., None] = lambda f, *a: threading.Thread(target=f, args=a, daemon=True).start()

    def _net_status(self, ok: bool) -> None:
        if self.write_back:
            try:
                self.write_back(f"NET {1 if ok else 0}\n")
            except OSError:
                pass

    def _emit(self, m: dict) -> None:
        body = {"station_id": self.cfg.station_id, "sequence": self.seq.next(), "source": self.cfg.source, **m}
        self.last_local = {"occupied": m.get("occupied"), "sensor_state": m.get("sensor_state"), "at": self.clock(),
                           "wall": time.time()}
        self.uplink.enqueue(body)

    def handle_line(self, line: str) -> None:
        try:
            m = parse_line(line)
        except ParseError as exc:
            log.warning("Ungültige Zeile verworfen: %s", exc)
            return
        self.last_line_at = self.clock()
        if m is None:
            self._hello(line)
            return
        if m.get("kind") == "nfc":
            self.has_pn532 = True
            self.tap(m["uid"], reader="pn532")
            return
        self._emit(m)

    def _hello(self, line: str) -> None:
        try:
            d = json.loads(line)
        except ValueError:
            return
        if isinstance(d, dict) and d.get("type") == "hello":
            self.hello = {"fw": str(d.get("fw", ""))[:20], "name": str(d.get("name", "bike-stall"))[:40], "nfc": bool(d.get("nfc"))}
            self.has_pn532 = self.has_pn532 or self.hello["nfc"]
            log.info("Firmware erkannt: %s %s%s", self.hello["name"], self.hello["fw"], " mit NFC-Leser" if self.hello["nfc"] else "")

    def identify(self) -> bool:
        """LEDs am Stellplatz 10 s blinken lassen (Befehl aus dem Portal), damit man ihn vor Ort findet."""
        if not self.write_back:
            log.warning("Identifizieren nicht möglich: kein Arduino verbunden")
            return False
        try:
            self.write_back("IDENT\n")
            return True
        except OSError:
            return False

    # ---- NFC
    def tap(self, uid: str, reader: str | None = None) -> None:
        body = {"station_id": self.cfg.station_id, "sequence": self.seq.next(), "uid": uid, "source": self.cfg.source}
        if reader:
            body["reader"] = reader[:80]
        log.info("NFC-Karte gelesen (…%s)", uid[-4:])
        self.run_async(self._send_tap, body, time.monotonic())

    def _send_tap(self, body: dict, t0: float, sleep: Callable[[float], None] = time.sleep) -> str:
        delay = 1.0
        while True:
            age = time.monotonic() - t0
            if age > NFC_MAX_AGE_S:
                log.warning("NFC-Vorgang verworfen: Plattform %d s nicht erreichbar", int(age))
                result = "offline"
                break
            try:
                status, resp = self.uplink.request("/api/v1/nfc/tap", {**body, "age_ms": int(age * 1000)})
            except (urllib.error.URLError, OSError, TimeoutError):
                status, resp = 0, {}
            if status == 200:
                result = str(resp.get("result", "error"))
                amount = resp.get("amount_cents")
                log.info("NFC: %s%s", result, f" ({amount / 100:.2f} EUR)" if isinstance(amount, int) and amount else "")
                break
            if status in (401, 403, 422):
                log.error("NFC-Vorgang abgelehnt (HTTP %d)", status)
                result = "rejected"
                break
            sleep(delay)
            delay = min(delay * 2, 10.0)
        self.last_tap = {"result": result, "at": time.time()}
        if self.write_back:
            try:
                self.write_back(f"NFC {result}\n")
            except OSError:
                pass
        return result

    def watchdog(self) -> None:
        """Schweigt der Arduino, wird der Stellplatz als Sensorfehler (-> STATUS UNBEKANNT) gemeldet."""
        now = self.clock()
        silent = self.last_line_at is None or now - self.last_line_at > self.cfg.arduino_timeout_s
        if not silent:
            self.last_fault_sent = None
            return
        if self.last_fault_sent is not None and now - self.last_fault_sent < self.cfg.heartbeat_s:
            return
        if self.last_line_at is not None or self.last_fault_sent is None:
            log.warning("Keine Daten vom Arduino – Stellplatz wird als unbekannt gemeldet")
        self.last_fault_sent = now
        self._emit({"occupied": None, "vibration_score": 0, "sensor_state": "error"})


# ---------------------------------------------------------------------- Quellen
def serial_lines(cfg: GatewayConfig, gw: Gateway, stop: threading.Event) -> Iterator[str | None]:
    import serial  # pyserial

    while not stop.is_set():
        try:
            with serial.Serial(cfg.serial_port, cfg.baudrate, timeout=1) as ser:
                log.info("Seriell verbunden: %s", cfg.serial_port)
                gw.write_back = lambda s: ser.write(s.encode())
                if gw.uplink.online is not None:
                    gw._net_status(gw.uplink.online)
                while not stop.is_set():
                    raw = ser.readline()
                    yield raw.decode("utf-8", errors="replace") if raw else None
        except (serial.SerialException, OSError) as exc:
            gw.write_back = None
            log.warning("Serielle Verbindung fehlt (%s) – neuer Versuch in 3 s", exc)
            yield None
            stop.wait(3)


def stdin_lines(stop: threading.Event) -> Iterator[str | None]:
    # Eigener Lese-Thread: select() auf gepuffertes stdin würde Zeilen im Puffer übersehen.
    import queue

    q: queue.Queue[str | None] = queue.Queue()

    def reader():
        for line in sys.stdin:
            q.put(line)
        q.put(None)  # EOF

    threading.Thread(target=reader, daemon=True, name="stdin").start()
    while not stop.is_set():
        try:
            line = q.get(timeout=1)
        except queue.Empty:
            yield None
            continue
        if line is None:
            return
        yield line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gateway Arduino -> API")
    ap.add_argument("-c", "--config", default=os.environ.get("GATEWAY_CONFIG", HERE / "config.toml"))
    ap.add_argument("--stdin", action="store_true", help="Zeilen von stdin statt seriell lesen (z. B. vom Simulator)")
    ap.add_argument("--simulated", action="store_true", help="Daten als 'simulated' kennzeichnen (Pflicht bei Simulator!)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)
    if args.simulated:
        cfg.source = "simulated"
    gw = Gateway(cfg)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    sender = threading.Thread(target=gw.uplink.run, args=(stop,), daemon=True, name="uplink")
    sender.start()
    log.info("Gateway gestartet: Station %s, API %s, Quelle %s", cfg.station_id, cfg.api_url, cfg.source)

    source = stdin_lines(stop) if args.stdin else serial_lines(cfg, gw, stop)
    for line in source:
        if line:
            gw.handle_line(line)
        gw.watchdog()
        if stop.is_set():
            break
    stop.set()
    # Letzter Versuch, den Puffer zu leeren.
    gw.uplink.flush_once()
    log.info("Gateway beendet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
