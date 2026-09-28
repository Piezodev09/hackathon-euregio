"""Agent of the Smart Bike Station: device management on top of the measurement chain (gateway.py).

  * pairing with a one-time code from the portal (nobody copies tokens by hand)
  * credentials and configuration in one state file with mode 0600
  * heartbeat with health data (Arduino connected, buffer, CPU temperature, disk ...)
  * configuration sync: added/removed slots arrive without reinstalling
  * token rotation (automatically every 30 days or on request) with a grace period
  * a fixed set of remote commands (restart, rotate_token) - never arbitrary code
  * self-update: download, verify SHA-256, extract safely, switch, roll back on failed starts
  * certificate pinning for self-hosted platforms (own CA, verified by fingerprint)

Commands::

    python3 -m bikeagent --state-dir DIR enroll --url https://... [--code CODE] [--source serial|simulator]
    python3 -m bikeagent --state-dir DIR run [--source stdin]
    python3 -m bikeagent --state-dir DIR status
    python3 -m bikeagent --state-dir DIR rollback
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import platform
import queue
import re
import shutil
import signal
import socket
import ssl
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__ as VERSION
from .gateway import Gateway, GatewayConfig, serial_lines, stdin_lines

log = logging.getLogger("agent")

TOKEN_MAX_AGE_S = 30 * 86400
EXIT_RESTART = 3          # systemd (Restart=always) / the container runtime restarts the agent
MAX_BUNDLE_BYTES = 5 * 1024 * 1024
ROLLBACK_AFTER_STARTS = 3
PACKAGE = "bikeagent"
SOURCES = ("serial", "simulator", "stdin")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


class AgentError(RuntimeError):
    pass


def env_flag(name: str, default: bool = True) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------- state
class State:
    """Credentials and configuration. Written atomically with mode 0600."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.path = self.dir / "agent.json"
        self.data: dict = {}
        self._lock = threading.Lock()

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> "State":
        if not self.path.exists():
            raise AgentError(f"Not paired: {self.path} is missing. Run 'enroll' first.")
        if self.path.stat().st_mode & 0o077:
            log.warning("State file had loose permissions - fixing to 0600")
            os.chmod(self.path, 0o600)
        self.data = json.loads(self.path.read_text())
        return self

    def save(self) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)


# ---------------------------------------------------------------------- HTTP
def is_local_http(url: str) -> bool:
    return url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))


class Api:
    def __init__(self, base_url: str, ca_file: str | None = None, allow_http: bool = False, timeout: float = 10):
        self.base = base_url.rstrip("/")
        if not self.base.startswith("https://") and not allow_http and not is_local_http(self.base):
            raise AgentError("Platform URL must use HTTPS (development only: --allow-http)")
        self.ctx = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None, token: str | None = None, raw: bool = False,
                max_bytes: int = 1024 * 1024):
        url = path if path.startswith("http") else self.base + path
        if not url.startswith(self.base + "/"):
            raise AgentError("Downloads are only allowed from the own platform")
        headers = {"Accept": "application/json", "User-Agent": f"bike-agent/{VERSION}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        ctx = self.ctx if url.startswith("https") else None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as r:
                content = r.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise AgentError("Response too large")
                return r.status, (content if raw else json.loads(content or b"{}"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read(65536) or b"{}").get("detail")
            except Exception:
                detail = None
            return e.code, {"detail": detail}


def normalize_fingerprint(fp: str) -> str:
    """Hex digits only; accepts "SHA-256 81:7F:…", "sha256 Fingerprint=81:7F:…" (openssl) or plain hex."""
    s = re.sub(r"^\s*sha-?256(\s*fingerprint)?\s*[:=]?\s*", "", fp.lower())
    return "".join(ch for ch in s if ch in "0123456789abcdef")


def fetch_pinned_ca(url: str, fingerprint: str, dest: Path, timeout: float = 10) -> Path:
    """Download the platform CA and accept it only if its SHA-256 fingerprint matches.

    Used by the Home Assistant add-on and Docker, where no install script embeds the CA. The first
    request cannot verify TLS (the CA is what we are fetching), so trust comes from the fingerprint
    the admin copied from the portal.
    """
    want = normalize_fingerprint(fingerprint)
    if len(want) != 64:
        raise AgentError("CA fingerprint must be a SHA-256 hex value (64 hex digits)")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url.rstrip("/") + "/install/ca.crt", headers={"User-Agent": f"bike-agent/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            pem = r.read(65536).decode("ascii", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise AgentError(f"Platform not reachable for the CA download ({getattr(exc, 'reason', exc)})") from None
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
    except ValueError as exc:
        raise AgentError("Platform returned no valid CA certificate") from exc
    got = hashlib.sha256(der).hexdigest()
    if got != want:
        raise AgentError(f"CA fingerprint mismatch (expected {want[:16]}..., got {got[:16]}...) - wrong platform?")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(pem)
    return dest


# ---------------------------------------------------------------------- system info
def system_health() -> dict:
    h: dict = {}
    try:
        h["cpu_temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        pass
    try:
        h["load_1m"] = round(os.getloadavg()[0], 2)
    except OSError:
        pass
    try:
        h["disk_free_mb"] = shutil.disk_usage("/").free // (1024 * 1024)
    except OSError:
        pass
    return h


def os_info() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')[:100] + f" ({platform.machine()})"
    except OSError:
        pass
    return f"{platform.system()} {platform.release()} ({platform.machine()})"[:120]


def safe_hostname() -> str:
    return "".join(ch for ch in socket.gethostname() if ch.isalnum() or ch in "._-")[:64]


def detect_serial_port(dev: Path = Path("/dev")) -> str:
    """Stable ``/dev/serial/by-id`` name of an Arduino if present (survives re-plugging), else the first ACM/USB port."""
    by_id = sorted((dev / "serial" / "by-id").glob("*"))
    for p in by_id:
        if "arduino" in p.name.lower():
            return str(p)
    for pattern in ("ttyACM*", "ttyUSB*"):
        found = sorted(dev.glob(pattern))
        if found:
            return str(found[0])
    return str(by_id[0]) if by_id else "/dev/ttyACM0"


# ---------------------------------------------------------------------- pairing
def enroll(state: State, url: str, code: str, source: str, serial_port: str, ca_file: str | None,
           allow_http: bool, name: str = "", mqtt: dict | None = None) -> dict:
    api = Api(url, ca_file, allow_http)
    status, body = api.request("POST", "/api/v1/agent/enroll", {
        "code": code, "hostname": safe_hostname(), "name": name, "agent_version": VERSION, "os_info": os_info(),
        "source": source,
    })
    if status != 200:
        raise AgentError(f"Pairing failed ({status}: {body.get('detail')})")
    base = url.rstrip("/")
    state.data = {
        # The platform may announce its canonical URL, but only on the same origin we paired with.
        "api_url": body["api_url"] if str(body.get("api_url", "")).rstrip("/") == base else base,
        "device_id": body["device_id"],
        "token": body["token"],
        "token_issued_at": time.time(),
        "station_id": body["station_id"],
        "station_name": body.get("station_name"),
        "slot_map": body["slot_map"],
        "config_version": body["config_version"],
        "heartbeat_s": body.get("heartbeat_s", 60),
        "source": source,
        "serial_port": detect_serial_port() if serial_port == "auto" else serial_port,
        "ca_file": ca_file,
        "allow_http": allow_http,
        "enrolled_at": time.time(),
    }
    if mqtt:
        state.data["mqtt"] = mqtt
    state.save()
    return state.data


# ---------------------------------------------------------------------- updates
def install_dir_of(agent_file: Path) -> Path | None:
    """``<prefix>/releases/<version>/bikeagent/agent.py`` -> ``<prefix>``, else None (unmanaged layout)."""
    rel = agent_file.resolve().parent.parent
    if rel.parent.name == "releases":
        return rel.parent.parent
    return None


def _allowed_member(name: str) -> bool:
    p = Path(name)
    if p.is_absolute() or ".." in p.parts:
        return False
    if p.parts == ("VERSION",):
        return True
    return len(p.parts) == 2 and p.parts[0] == PACKAGE and bool(_SAFE_NAME.match(p.parts[1]))


def safe_extract(data: bytes, target: Path) -> None:
    """Extract only regular files of the known layout (``VERSION``, ``bikeagent/<file>``) - no links, no path tricks."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            if not m.isfile() or not _allowed_member(m.name):
                raise AgentError(f"Illegal entry in package: {m.name}")
        target.mkdir(parents=True, exist_ok=True)
        for m in members:
            dest = target / m.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tar.extractfile(m).read())
            os.chmod(dest, 0o644)


def switch_current(prefix: Path, release: Path) -> None:
    tmp = prefix / "current.new"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(release)
    os.replace(tmp, prefix / "current")


def apply_update(api: Api, token: str, info: dict, state_dir: Path, agent_file: Path = Path(__file__)) -> bool:
    prefix = install_dir_of(agent_file)
    if prefix is None:
        log.info("Update %s available - not a managed installation, please update manually", info.get("version"))
        return False
    version = str(info["version"])
    if not re.fullmatch(r"\d+(\.\d+){0,3}", version):
        raise AgentError("invalid version number")
    status, data = api.request("GET", info["url"], token=token, raw=True, max_bytes=MAX_BUNDLE_BYTES)
    if status != 200:
        raise AgentError(f"Download failed ({status})")
    if hashlib.sha256(data).hexdigest() != info["sha256"]:
        raise AgentError("Update checksum mismatch - update discarded")
    release = prefix / "releases" / version
    staging = prefix / "releases" / f".{version}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    safe_extract(data, staging)
    if (staging / "VERSION").read_text().strip() != version:
        raise AgentError("Version inside the package does not match")
    shutil.rmtree(release, ignore_errors=True)
    os.replace(staging, release)
    previous = (prefix / "current").resolve()
    (state_dir / "update-pending.json").write_text(json.dumps({"previous": str(previous), "version": version, "starts": 0}))
    switch_current(prefix, release)
    log.warning("Update to %s installed - restarting", version)
    return True


def check_rollback(state_dir: Path, agent_file: Path = Path(__file__)) -> None:
    """After an update: if the new version starts repeatedly without a successful heartbeat, roll back."""
    marker = state_dir / "update-pending.json"
    if not marker.exists():
        return
    info = json.loads(marker.read_text())
    info["starts"] = info.get("starts", 0) + 1
    marker.write_text(json.dumps(info))
    if info["starts"] >= ROLLBACK_AFTER_STARTS:
        prefix = install_dir_of(agent_file)
        if prefix and Path(info["previous"]).exists():
            switch_current(prefix, Path(info["previous"]))
            marker.unlink()
            log.error("Update %s does not start reliably - rolled back to %s", info.get("version"), info["previous"])
            sys.exit(EXIT_RESTART)


def confirm_update(state_dir: Path) -> None:
    marker = state_dir / "update-pending.json"
    if marker.exists():
        marker.unlink()
        log.info("Update confirmed (first successful heartbeat)")


def cleanup_releases(agent_file: Path = Path(__file__), keep: int = 3) -> None:
    prefix = install_dir_of(agent_file)
    if prefix is None:
        return
    current = (prefix / "current").resolve()
    rels = sorted((p for p in (prefix / "releases").iterdir() if p.is_dir() and not p.name.startswith(".")),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for p in rels[keep:]:
        if p.resolve() != current:
            shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------------- built-in simulator source
class QueueWriter:
    """File-like object that turns written text into queued lines."""

    def __init__(self, q: queue.Queue):
        self.q = q
        self.buf = ""

    def write(self, s: str) -> None:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.q.put(line + "\n")

    def flush(self) -> None:
        pass


def simulator_lines(slot_keys: list[str], stop: threading.Event):
    from .simulator import Simulator

    q: queue.Queue = queue.Queue()
    sim = Simulator(slot_keys, heartbeat_s=10, out=QueueWriter(q))
    for s in sim.slots.values():
        sim.emit(s)

    def loop():
        next_auto = time.monotonic() + 20
        while not stop.is_set():
            sim.tick()
            if time.monotonic() >= next_auto:
                sim.auto_step()
                next_auto = time.monotonic() + 30
            time.sleep(0.1)

    threading.Thread(target=loop, daemon=True, name="simulator").start()
    while not stop.is_set():
        try:
            yield q.get(timeout=1)
        except queue.Empty:
            yield None


# ---------------------------------------------------------------------- agent
class Agent:
    def __init__(self, state: State, clock=time.time, source: str | None = None):
        self.state = state
        self.clock = clock
        self.stop = threading.Event()
        self.exit_code = 0
        self.started = time.monotonic()
        self.last_error = ""
        self.self_update = env_flag("BIKE_AGENT_SELF_UPDATE", True)
        self.alerts: list[str] = []  # slot keys with an open movement warning (from the platform)
        s = state.data
        self.source = source or s.get("source", "serial")
        self.api = Api(s["api_url"], s.get("ca_file"), s.get("allow_http", False))
        self.cfg = GatewayConfig(
            api_url=s["api_url"], station_id=s["station_id"], slot_map=dict(s["slot_map"]), token=s["token"],
            serial_port=s.get("serial_port", "/dev/ttyACM0"), ca_file=s.get("ca_file"),
            state_dir=state.dir, source="live" if self.source == "serial" else "simulated",
        )
        self.gw = Gateway(self.cfg)
        self.mqtt = None

    # ---- control channel
    def heartbeat_once(self) -> dict | None:
        g = self.gw
        body = {
            "agent_version": VERSION, "hostname": safe_hostname(), "os_info": os_info(), "source": self.source,
            "uptime_s": int(time.monotonic() - self.started), "serial_connected": g.serial_ok,
            "buffer_len": len(g.uplink.buffer), "api_online": g.uplink.online, "last_error": self.last_error[:300],
            "config_version": self.state.get("config_version", 0), "self_update": self.self_update,
            "mqtt_connected": None if self.mqtt is None else self.mqtt.connected,
            **system_health(),
        }
        status, resp = self.api.request("POST", "/api/v1/agent/heartbeat", body, token=self.cfg.token)
        if status == 401:
            self.last_error = "token rejected (revoked?)"
            log.error("The platform rejects the device token - device revoked in the portal? Pair again with 'enroll'.")
            return None
        if status != 200:
            self.last_error = f"heartbeat HTTP {status}"
            return None
        confirm_update(self.state.dir)
        self.apply(resp)
        return resp

    def apply(self, resp: dict) -> None:
        if resp.get("config_version", 0) != self.state.get("config_version"):
            new_map = {str(k): str(v) for k, v in resp["slot_map"].items()}
            self.cfg.slot_map.clear()
            self.cfg.slot_map.update(new_map)  # parse_line uses the same dict -> effective immediately
            self.state.data.update(slot_map=new_map, config_version=resp["config_version"])
            self.state.save()
            log.info("New configuration %s: slots %s", resp["config_version"], ", ".join(new_map))
            if self.mqtt:
                self.mqtt.publish_discovery()
        if "alerts" in resp:
            self.alerts = [str(a) for a in resp.get("alerts") or []]
            if self.mqtt:
                self.mqtt.publish_alerts(self.alerts)
        for cmd in resp.get("commands", []):
            if cmd == "restart":
                log.warning("Restart requested from the portal")
                self.exit_code = EXIT_RESTART
                self.stop.set()
            elif cmd == "rotate_token":
                self.rotate_token()
            else:
                log.warning("Unknown command ignored: %r", cmd)
        if self.clock() - self.state.get("token_issued_at", self.clock()) > TOKEN_MAX_AGE_S:
            self.rotate_token()
        upd = resp.get("update")
        if upd and self.self_update:
            try:
                if apply_update(self.api, self.cfg.token, upd, self.state.dir):
                    self.exit_code = EXIT_RESTART
                    self.stop.set()
            except AgentError as exc:
                self.last_error = str(exc)
                log.error("Update failed: %s", exc)

    def rotate_token(self) -> bool:
        status, resp = self.api.request("POST", "/api/v1/agent/rotate-token", {}, token=self.cfg.token)
        if status != 200 or not resp.get("token"):
            log.error("Token rotation failed (%s)", status)
            return False
        # Persist first, then use - the old token stays valid on the platform for a short while.
        self.state.data.update(token=resp["token"], token_issued_at=self.clock())
        self.state.save()
        self.cfg.token = resp["token"]
        log.info("Device token renewed")
        return True

    def control_loop(self) -> None:
        interval = max(15, int(self.state.get("heartbeat_s", 60)))
        self.stop.wait(5)  # wait for source data first so the first health report is meaningful
        while not self.stop.is_set():
            try:
                resp = self.heartbeat_once()
                if resp:
                    interval = max(15, int(resp.get("heartbeat_s", interval)))
            except Exception as exc:  # the control channel must never stop the measurement chain
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("Heartbeat failed: %s", self.last_error)
            if self.mqtt:
                self.mqtt.publish_health(self.gw.serial_ok, system_health().get("cpu_temp_c"), self.gw.uplink.online)
            self.stop.wait(interval)

    def start_mqtt(self) -> None:
        conf = self.state.get("mqtt")
        if not conf or not conf.get("host"):
            return
        try:
            from .mqtt import MqttBridge

            self.mqtt = MqttBridge(conf, self.state, self.cfg)
            self.mqtt.start()
            self.gw.listeners.append(self.mqtt.on_measurement)
        except Exception as exc:  # MQTT is optional - never block the agent
            self.mqtt = None
            log.error("MQTT disabled: %s", exc)

    # ---- main loop
    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        self.start_mqtt()
        threading.Thread(target=self.gw.uplink.run, args=(self.stop,), daemon=True, name="uplink").start()
        threading.Thread(target=self.control_loop, daemon=True, name="control").start()
        cleanup_releases()
        log.info("Agent %s started: station %s (%s), source %s", VERSION, self.state["station_id"],
                 self.state.get("station_name"), self.source)
        if self.source == "simulator":
            lines = simulator_lines(list(self.cfg.slot_map), self.stop)
        elif self.source == "stdin":
            lines = stdin_lines(self.stop)
        else:
            lines = serial_lines(self.cfg, self.gw, self.stop)
        for line in lines:
            if line:
                self.gw.handle_line(line)
            self.gw.watchdog()
            if self.stop.is_set():
                break
        self.stop.set()
        self.gw.uplink.flush_once()
        if self.mqtt:
            self.mqtt.stop()
        log.info("Agent stopped (exit code %s)", self.exit_code)
        return self.exit_code


# ---------------------------------------------------------------------- CLI
def _mqtt_from_args(args) -> dict | None:
    if not getattr(args, "mqtt_host", None):
        return None
    return {"host": args.mqtt_host, "port": args.mqtt_port, "username": args.mqtt_username or "",
            "password": args.mqtt_password or os.environ.get("BIKE_MQTT_PASSWORD", ""), "tls": args.mqtt_tls,
            "discovery_prefix": args.mqtt_discovery_prefix}


def _add_mqtt_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mqtt-host", help="MQTT broker, e.g. the Home Assistant host")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-username", default="")
    p.add_argument("--mqtt-password", default="", help="better: environment variable BIKE_MQTT_PASSWORD")
    p.add_argument("--mqtt-tls", action="store_true")
    p.add_argument("--mqtt-discovery-prefix", default="homeassistant")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bike-agent", description="Smart Bike Station agent")
    ap.add_argument("--state-dir", default=os.environ.get("BIKE_AGENT_STATE", "/var/lib/bike-agent"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll", help="pair with a station")
    e.add_argument("--url", required=True)
    e.add_argument("--code", default=os.environ.get("BIKE_ENROLL_CODE", ""))
    e.add_argument("--source", choices=["serial", "simulator"], default="serial")
    e.add_argument("--serial-port", default="auto")
    e.add_argument("--ca-file", help="CA certificate of a self-hosted platform")
    e.add_argument("--ca-fingerprint", help="SHA-256 fingerprint of the platform CA (downloads and pins it)")
    e.add_argument("--name", default="")
    e.add_argument("--allow-http", action="store_true")
    _add_mqtt_args(e)
    r = sub.add_parser("run", help="start the agent")
    r.add_argument("--source", choices=SOURCES, help="override the paired source (stdin: lines from a pipe)")
    m = sub.add_parser("mqtt", help="configure local MQTT / Home Assistant sensors")
    _add_mqtt_args(m)
    m.add_argument("--disable", action="store_true")
    sub.add_parser("status", help="show the state (without secrets)")
    sub.add_parser("rollback", help="switch back to the previous version")
    sub.add_parser("version")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state = State(Path(args.state_dir))
    try:
        if args.cmd == "version":
            print(VERSION)
        elif args.cmd == "enroll":
            code = args.code or input("Pairing code from the portal: ").strip()
            ca_file = args.ca_file
            if args.ca_fingerprint and not ca_file:
                ca_file = str(fetch_pinned_ca(args.url, args.ca_fingerprint, state.dir / "ca.crt"))
            data = enroll(state, args.url, code, args.source, args.serial_port, ca_file, args.allow_http, args.name,
                          _mqtt_from_args(args))
            print(f"Paired with station '{data.get('station_name')}' ({data['station_id']}) as device {data['device_id']}.")
        elif args.cmd == "mqtt":
            state.load()
            if args.disable:
                state.data.pop("mqtt", None)
            elif args.mqtt_host:
                state.data["mqtt"] = _mqtt_from_args(args)
            else:
                raise AgentError("--mqtt-host or --disable required")
            state.save()
            print("MQTT " + ("disabled" if args.disable else f"configured for {args.mqtt_host}:{args.mqtt_port}")
                  + ". Restart the agent to apply.")
        elif args.cmd == "status":
            state.load()
            safe = {k: v for k, v in state.data.items() if k not in ("token", "mqtt")}
            safe["token"] = state["token"][:10] + "..."
            if state.get("mqtt"):
                safe["mqtt"] = {k: ("***" if k == "password" and v else v) for k, v in state["mqtt"].items()}
            safe["version"] = VERSION
            print(json.dumps(safe, indent=1, ensure_ascii=False))
        elif args.cmd == "rollback":
            prefix = install_dir_of(Path(__file__))
            if prefix is None:
                raise AgentError("not a managed installation")
            current = (prefix / "current").resolve()
            others = sorted((p for p in (prefix / "releases").iterdir() if p.is_dir() and p != current and not p.name.startswith(".")),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if not others:
                raise AgentError("no previous version available")
            switch_current(prefix, others[0])
            print(f"Switched back to {others[0].name}. Restart: sudo systemctl restart bike-agent")
        elif args.cmd == "run":
            state.load()
            check_rollback(state.dir)
            return Agent(state, source=args.source).run()
    except AgentError as exc:
        log.error("%s", exc)
        return 1
    return 0
