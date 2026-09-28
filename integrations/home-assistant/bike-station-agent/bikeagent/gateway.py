"""Measurement chain of the agent: Arduino line -> validated measurement -> buffered upload.

Responsibilities:
  * read JSON lines from the Arduino (USB serial) and validate format and plausibility
  * map the Arduino slot id to the slot key of the station
  * assign unique, monotonically increasing sequence numbers (the API drops duplicates)
  * buffer a bounded number of messages while the platform is unreachable and resend them
    later together with their age (``age_ms``) - the Pi often has no real-time clock
  * if the Arduino goes silent, report every slot as a sensor error ("unknown", never "free")
  * report the network state back to the Arduino (``NET 1`` / ``NET 0``) for a local LED

Only the standard library is used; pyserial is imported lazily for the serial source.
"""

from __future__ import annotations

import json
import logging
import queue
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger("gateway")


# ---------------------------------------------------------------------- configuration
@dataclass
class GatewayConfig:
    api_url: str
    station_id: str
    slot_map: dict[str, str]
    token: str = field(repr=False)
    serial_port: str = "/dev/ttyACM0"
    baudrate: int = 115200
    ca_file: str | None = None
    buffer_max: int = 500
    batch_size: int = 50
    arduino_timeout_s: float = 15.0
    heartbeat_s: float = 10.0
    http_timeout_s: float = 5.0
    state_dir: Path = Path("state")
    source: str = "live"  # "live" or "simulated" - simulated data is always labelled as such


# ---------------------------------------------------------------------- parsing
class ParseError(ValueError):
    pass


def parse_line(line: str, slot_map: dict[str, str]) -> dict | None:
    """Arduino line -> ``{'slot_id', 'occupied', 'vibration_score', 'sensor_state'}``.

    Returns None for info lines (e.g. ``{"type":"hello"}``) and raises ParseError for invalid data.
    Expected format: ``{"slot_id":"A","presence":1,"vibration":12,"seq":1042[,"state":"ok"]}``
    presence: 1 = occupied, 0 = free, -1 = the sensor delivered no valid reading.
    """
    line = line.strip()
    if not line:
        return None
    if len(line) > 512:
        raise ParseError("line too long")
    try:
        d = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ParseError(f"not JSON: {exc.msg}") from None
    if not isinstance(d, dict):
        raise ParseError("not a JSON object")
    if "slot_id" not in d:
        if d.get("type") in ("hello", "info", "debug"):
            return None
        raise ParseError("slot_id missing")

    ard_slot = str(d["slot_id"])
    if ard_slot not in slot_map:
        raise ParseError(f"unknown slot {ard_slot!r}")

    presence = d.get("presence")
    if isinstance(presence, bool) or presence not in (0, 1, -1):
        raise ParseError("presence must be 0, 1 or -1")

    vib = d.get("vibration", 0)
    if isinstance(vib, bool) or not isinstance(vib, (int, float)):
        raise ParseError("vibration must be a number")
    if vib < 0 or vib > 1023:
        raise ParseError("vibration outside 0..1023")

    state = d.get("state", "ok")
    if state not in ("ok", "error"):
        raise ParseError("state must be ok or error")
    if presence == -1:
        state = "error"

    return {
        "slot_id": slot_map[ard_slot],
        "occupied": None if state == "error" else bool(presence),
        "vibration_score": 0 if state == "error" else int(vib),
        "sensor_state": state,
    }


# ---------------------------------------------------------------------- sequence numbers
class SequenceCounter:
    """Monotonically increasing number that survives restarts (the Pi often has no RTC).

    Starts at max(stored value + reserve, current time in ms). The value is only persisted every
    ``persist_every`` steps to spare the SD card; the reserve covers the gap after a crash.
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
            log.warning("Could not persist sequence counter: %s", exc)

    def next(self) -> int:
        with self._lock:
            self.value += 1
            if self.value % self.persist_every == 0:
                self._persist()
            return self.value


# ---------------------------------------------------------------------- buffer + upload
@dataclass
class Queued:
    body: dict
    enqueued: float  # time.monotonic()


class Uplink:
    """Bounded buffer and upload to the platform. The oldest messages are dropped on overflow."""

    def __init__(self, cfg: GatewayConfig, post: Callable[[str, dict], int] | None = None):
        self.cfg = cfg
        self.buffer: deque[Queued] = deque(maxlen=cfg.buffer_max)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.online: bool | None = None
        self.on_status_change: Callable[[bool], None] | None = None
        self._post = post or self._http_post
        self._ssl = ssl.create_default_context(cafile=cfg.ca_file) if cfg.ca_file else ssl.create_default_context()

    def enqueue(self, body: dict) -> None:
        with self.lock:
            if len(self.buffer) == self.buffer.maxlen:
                log.warning("Buffer full - dropping the oldest message")
            self.buffer.append(Queued(body, time.monotonic()))
        self.wake.set()

    def _http_post(self, path: str, payload: dict) -> int:
        req = urllib.request.Request(
            self.cfg.api_url + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.token}"},
        )
        try:
            ctx = self._ssl if self.cfg.api_url.startswith("https") else None
            with urllib.request.urlopen(req, timeout=self.cfg.http_timeout_s, context=ctx) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _set_online(self, ok: bool) -> None:
        if ok != self.online:
            self.online = ok
            log.log(logging.INFO if ok else logging.WARNING, "Platform %s", "reachable" if ok else "NOT reachable")
            if self.on_status_change:
                self.on_status_change(ok)

    def flush_once(self) -> bool:
        """Send one batch. True = buffer empty or progress, False = error (caller backs off)."""
        with self.lock:
            batch = list(self.buffer)[: self.cfg.batch_size]
        if not batch:
            return True
        now = time.monotonic()
        payload = {"measurements": [{**q.body, "age_ms": min(86_400_000, int((now - q.enqueued) * 1000))} for q in batch]}
        try:
            status = self._post("/api/v1/measurements/batch", payload)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.debug("Upload failed: %s", exc)
            self._set_online(False)
            return False

        if status in (200, 202):
            self._drop(batch)
            self._set_online(True)
            return True
        if status == 422:
            # Permanently invalid - drop instead of retrying forever.
            log.error("Platform rejected messages as invalid (422) - %d dropped", len(batch))
            self._drop(batch)
            self._set_online(True)
            return True
        if status in (401, 403):
            log.error("Platform rejected the device token (%d) - check the pairing", status)
        else:
            log.warning("Platform answered HTTP %d", status)
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


# ---------------------------------------------------------------------- gateway
class Gateway:
    """Turns source lines into measurements and hands them to the uplink (and optional listeners)."""

    def __init__(self, cfg: GatewayConfig, uplink: Uplink | None = None, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.uplink = uplink or Uplink(cfg)
        self.seq = SequenceCounter(cfg.state_dir / "sequence")
        self.clock = clock
        self.last_line_at: float | None = None
        self.last_fault_sent: float | None = None
        self.write_back: Callable[[str], None] | None = None
        # Called with every measurement (e.g. the local MQTT publisher) - works without internet.
        self.listeners: list[Callable[[dict], None]] = []
        self.uplink.on_status_change = self._net_status

    def _net_status(self, ok: bool) -> None:
        if self.write_back:
            try:
                self.write_back(f"NET {1 if ok else 0}\n")
            except OSError:
                pass

    def _emit(self, m: dict) -> None:
        body = {"station_id": self.cfg.station_id, "sequence": self.seq.next(), "source": self.cfg.source, **m}
        self.uplink.enqueue(body)
        for listener in self.listeners:
            try:
                listener(m)
            except Exception as exc:  # a listener must never break the measurement chain
                log.warning("Listener failed: %s", exc)

    def handle_line(self, line: str) -> None:
        try:
            m = parse_line(line, self.cfg.slot_map)
        except ParseError as exc:
            log.warning("Invalid line dropped: %s", exc)
            return
        self.last_line_at = self.clock()
        if m is None:
            return
        self._emit(m)

    @property
    def serial_ok(self) -> bool:
        return self.last_line_at is not None and self.clock() - self.last_line_at < self.cfg.arduino_timeout_s

    def watchdog(self) -> None:
        """If the Arduino is silent, report every slot as a sensor error."""
        now = self.clock()
        silent = self.last_line_at is None or now - self.last_line_at > self.cfg.arduino_timeout_s
        if not silent:
            self.last_fault_sent = None
            return
        if self.last_fault_sent is not None and now - self.last_fault_sent < self.cfg.heartbeat_s:
            return
        if self.last_line_at is not None or self.last_fault_sent is None:
            log.warning("No data from the Arduino - reporting all slots as unknown")
        self.last_fault_sent = now
        for slot in self.cfg.slot_map.values():
            self._emit({"slot_id": slot, "occupied": None, "vibration_score": 0, "sensor_state": "error"})


# ---------------------------------------------------------------------- sources
def serial_lines(cfg: GatewayConfig, gw: Gateway, stop: threading.Event) -> Iterator[str | None]:
    """Lines from the Arduino. Reconnects automatically; yields None while idle."""
    import serial  # pyserial

    while not stop.is_set():
        try:
            with serial.Serial(cfg.serial_port, cfg.baudrate, timeout=1) as ser:
                log.info("Serial connected: %s", cfg.serial_port)
                gw.write_back = lambda s: ser.write(s.encode())
                if gw.uplink.online is not None:
                    gw._net_status(gw.uplink.online)
                while not stop.is_set():
                    raw = ser.readline()
                    yield raw.decode("utf-8", errors="replace") if raw else None
        except (serial.SerialException, OSError) as exc:
            gw.write_back = None
            log.warning("Serial connection missing (%s) - retrying in 3 s", exc)
            yield None
            stop.wait(3)


def stdin_lines(stop: threading.Event) -> Iterator[str | None]:
    """Lines from stdin, e.g. ``python3 -m bikeagent.simulator | python3 -m bikeagent run --source stdin``."""
    # Separate reader thread: select() on buffered stdin would miss lines that are already buffered.
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
