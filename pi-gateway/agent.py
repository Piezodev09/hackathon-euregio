#!/usr/bin/env python3
"""Agent der Smart Bicycle Box für den Raspberry Pi.

Baut auf dem Gateway (gateway.py) auf und ergänzt die Geräteverwaltung aus der Cloud:
  * Kopplung per Einmal-Code aus dem Portal (kein manuelles Kopieren von Tokens)
  * Zustand/Zugangsdaten in einer Datei mit Rechten 0600
  * Heartbeat mit Gesundheitsdaten (Arduino verbunden, Puffer, Temperatur, Speicher …)
  * Konfigurations-Sync: neue/entfernte Stellplätze ohne Neuinstallation
  * Token-Rotation (automatisch alle 30 Tage oder auf Befehl) mit Übergangsfrist
  * feste Fernbefehle (restart, rotate_token) – niemals beliebiger Code
  * Selbst-Update: Paket laden, SHA-256 prüfen, sicher entpacken, umschalten, bei Fehlstart zurückrollen

Befehle:
  agent.py --state-dir DIR enroll --url https://… [--code CODE] [--source serial|simulator]
  agent.py --state-dir DIR run
  agent.py --state-dir DIR status
  agent.py --state-dir DIR rollback
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from camera import Camera  # noqa: E402
from gateway import Gateway, GatewayConfig, serial_lines  # noqa: E402

VERSION = (HERE / "VERSION").read_text().strip() if (HERE / "VERSION").exists() else "0.0.0"
log = logging.getLogger("agent")

TOKEN_MAX_AGE_S = 30 * 86400
EXIT_RESTART = 3          # systemd (Restart=always) startet den Agenten neu
MAX_BUNDLE_BYTES = 5 * 1024 * 1024
ROLLBACK_AFTER_STARTS = 3


class AgentError(RuntimeError):
    pass


# ---------------------------------------------------------------------- Zustand
class State:
    """Zugangsdaten und Konfiguration. Atomar geschrieben, Rechte 0600."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.path = self.dir / "agent.json"
        self.data: dict = {}
        self._lock = threading.Lock()

    def load(self) -> "State":
        if not self.path.exists():
            raise AgentError(f"Nicht gekoppelt: {self.path} fehlt. Zuerst 'enroll' ausführen.")
        mode = self.path.stat().st_mode & 0o077
        if mode:
            log.warning("Zustandsdatei hatte zu weite Rechte – korrigiere auf 0600")
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
class Api:
    def __init__(self, base_url: str, ca_file: str | None = None, allow_http: bool = False, timeout: float = 10):
        self.base = base_url.rstrip("/")
        if not self.base.startswith("https://") and not allow_http and not self.base.startswith(("http://127.0.0.1", "http://localhost")):
            raise AgentError("Plattform-URL muss HTTPS sein (nur für Entwicklung: --allow-http)")
        self.ctx = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None, token: str | None = None, raw: bool = False,
                max_bytes: int = 1024 * 1024):
        url = path if path.startswith("http") else self.base + path
        if not url.startswith(self.base):
            raise AgentError("Download nur von der eigenen Plattform erlaubt")
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
                    raise AgentError("Antwort zu groß")
                return r.status, (content if raw else json.loads(content or b"{}"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read(65536) or b"{}").get("detail")
            except Exception:
                detail = None
            return e.code, {"detail": detail}

    def upload(self, path: str, data: bytes, token: str, content_type: str = "image/jpeg") -> tuple[int, dict]:
        url = self.base + path
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": content_type, "Authorization": f"Bearer {token}", "User-Agent": f"bike-agent/{VERSION}"})
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 30), context=self.ctx if url.startswith("https") else None) as r:
                return r.status, json.loads(r.read(65536) or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {}


# ---------------------------------------------------------------------- Systeminfos
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


def detect_serial_port() -> str:
    for pattern in ("ttyACM*", "ttyUSB*"):
        found = sorted(Path("/dev").glob(pattern))
        if found:
            return str(found[0])
    return "/dev/ttyACM0"


# ---------------------------------------------------------------------- Kopplung
def enroll(state: State, url: str, code: str, source: str, serial_port: str, ca_file: str | None,
           allow_http: bool, name: str = "") -> dict:
    api = Api(url, ca_file, allow_http)
    status, body = api.request("POST", "/api/v1/agent/enroll", {
        "code": code, "hostname": safe_hostname(), "name": name, "agent_version": VERSION, "os_info": os_info(), "source": source,
    })
    if status != 200:
        raise AgentError(f"Kopplung fehlgeschlagen ({status}: {body.get('detail')})")
    state.data = {
        "api_url": body["api_url"] if body.get("api_url", "").startswith(url.rstrip("/")) else url.rstrip("/"),
        "device_id": body["device_id"],
        "token": body["token"],
        "token_issued_at": time.time(),
        "station_id": body["station_id"],
        "station_name": body.get("station_name"),
        "config_version": body["config_version"],
        "heartbeat_s": body.get("heartbeat_s", 60),
        "source": source,
        "serial_port": detect_serial_port() if serial_port == "auto" else serial_port,
        "ca_file": ca_file,
        "allow_http": allow_http,
        "enrolled_at": time.time(),
    }
    state.save()
    return state.data


# ---------------------------------------------------------------------- Updates
def install_dir_of(agent_file: Path) -> Path | None:
    """<prefix>/releases/<version>/agent.py -> <prefix>, sonst None (kein verwaltetes Layout)."""
    rel = agent_file.resolve().parent
    if rel.parent.name == "releases":
        return rel.parent.parent
    return None


def safe_extract(data: bytes, target: Path) -> None:
    """Entpackt nur reguläre Dateien ohne Pfadtricks (kein '..', keine absoluten Pfade, keine Links)."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            p = Path(m.name)
            if not m.isfile() or p.is_absolute() or ".." in p.parts or len(p.parts) != 1:
                raise AgentError(f"Unzulässiger Eintrag im Paket: {m.name}")
        target.mkdir(parents=True, exist_ok=True)
        for m in members:
            f = tar.extractfile(m)
            dest = target / m.name
            dest.write_bytes(f.read())
            os.chmod(dest, 0o755 if m.name.endswith(".py") else 0o644)


def switch_current(prefix: Path, release: Path) -> None:
    tmp = prefix / "current.new"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(release)
    os.replace(tmp, prefix / "current")


def apply_update(api: Api, token: str, info: dict, state_dir: Path, agent_file: Path = Path(__file__)) -> bool:
    prefix = install_dir_of(agent_file)
    if prefix is None:
        log.info("Update %s verfügbar – keine verwaltete Installation, bitte manuell aktualisieren", info.get("version"))
        return False
    version = str(info["version"])
    if not version.replace(".", "").isdigit():
        raise AgentError("ungültige Versionsnummer")
    status, data = api.request("GET", info["url"], token=token, raw=True, max_bytes=MAX_BUNDLE_BYTES)
    if status != 200:
        raise AgentError(f"Download fehlgeschlagen ({status})")
    digest = hashlib.sha256(data).hexdigest()
    if digest != info["sha256"]:
        raise AgentError("Prüfsumme des Updates stimmt nicht – Update verworfen")
    release = prefix / "releases" / version
    staging = prefix / "releases" / f".{version}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    safe_extract(data, staging)
    if (staging / "VERSION").read_text().strip() != version:
        raise AgentError("Version im Paket passt nicht")
    shutil.rmtree(release, ignore_errors=True)
    os.replace(staging, release)
    previous = (prefix / "current").resolve()
    (state_dir / "update-pending.json").write_text(json.dumps({"previous": str(previous), "version": version, "starts": 0}))
    switch_current(prefix, release)
    log.warning("Update auf %s installiert – Neustart", version)
    return True


def check_rollback(state_dir: Path, agent_file: Path = Path(__file__)) -> None:
    """Nach einem Update: startet die neue Version mehrfach ohne erfolgreichen Heartbeat, zurückrollen."""
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
            log.error("Update %s startet nicht zuverlässig – zurück auf %s", info.get("version"), info["previous"])
            sys.exit(EXIT_RESTART)


def confirm_update(state_dir: Path) -> None:
    marker = state_dir / "update-pending.json"
    if marker.exists():
        marker.unlink()
        log.info("Update bestätigt (erster erfolgreicher Heartbeat)")


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


# ---------------------------------------------------------------------- Simulator als Quelle
class QueueWriter:
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


def simulator_lines(stop: threading.Event):
    from simulator import Simulator

    q: queue.Queue = queue.Queue()
    sim = Simulator(heartbeat_s=10, out=QueueWriter(q))
    sim.emit()

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


# ---------------------------------------------------------------------- Agent
class Agent:
    def __init__(self, state: State, clock=time.time):
        self.state = state
        self.clock = clock
        self.stop = threading.Event()
        self.exit_code = 0
        self.started = time.monotonic()
        self.last_error = ""
        s = state.data
        self.api = Api(s["api_url"], s.get("ca_file"), s.get("allow_http", False))
        self.cfg = GatewayConfig(
            api_url=s["api_url"], station_id=s["station_id"], token=s["token"],
            serial_port=s.get("serial_port", "/dev/ttyACM0"), ca_file=s.get("ca_file"),
            state_dir=state.dir, source="simulated" if s.get("source") == "simulator" else "live",
        )
        self.gw = Gateway(self.cfg)
        self.camera = Camera(simulated=s.get("source") == "simulator")
        self.gw.uplink.on_response = self.on_uplink_response
        self._last_capture = 0.0
        self._capture_lock = threading.Lock()

    # ---- Steuerkanal
    def heartbeat_once(self) -> dict | None:
        g = self.gw
        serial_ok = g.last_line_at is not None and time.monotonic() - g.last_line_at < self.cfg.arduino_timeout_s
        body = {
            "agent_version": VERSION, "hostname": safe_hostname(), "os_info": os_info(), "source": self.state.get("source", "serial"),
            "uptime_s": int(time.monotonic() - self.started), "serial_connected": serial_ok, "buffer_len": len(g.uplink.buffer),
            "api_online": g.uplink.online, "last_error": self.last_error[:300], "config_version": self.state.get("config_version", 0),
            "camera": self.camera.kind,
            **system_health(),
        }
        status, resp = self.api.request("POST", "/api/v1/agent/heartbeat", body, token=self.cfg.token)
        if status == 401:
            self.last_error = "Token abgelehnt (widerrufen?)"
            log.error("Plattform lehnt das Geräte-Token ab – Gerät im Portal gesperrt? Neu koppeln mit 'enroll'.")
            return None
        if status != 200:
            self.last_error = f"Heartbeat HTTP {status}"
            return None
        confirm_update(self.state.dir)
        self.apply(resp)
        return resp

    def apply(self, resp: dict) -> None:
        if resp.get("config_version", 0) != self.state.get("config_version"):
            self.state.data.update(config_version=resp["config_version"])
            self.state.save()
            log.info("Neue Konfiguration %s", resp["config_version"])
        for cmd in resp.get("commands", []):
            if cmd == "restart":
                log.warning("Neustart auf Anforderung aus dem Portal")
                self.exit_code = EXIT_RESTART
                self.stop.set()
            elif cmd == "rotate_token":
                self.rotate_token()
            elif cmd == "snapshot":
                threading.Thread(target=self.capture_and_upload, args=("manual",), daemon=True).start()
            else:
                log.warning("Unbekannter Befehl ignoriert: %r", cmd)
        if self.clock() - self.state.get("token_issued_at", self.clock()) > TOKEN_MAX_AGE_S:
            self.rotate_token()
        upd = resp.get("update")
        if upd:
            try:
                if apply_update(self.api, self.cfg.token, upd, self.state.dir):
                    self.exit_code = EXIT_RESTART
                    self.stop.set()
            except AgentError as exc:
                self.last_error = str(exc)
                log.error("Update fehlgeschlagen: %s", exc)

    # ---- Kamera (nur wenn im Portal freigegeben; die Plattform lehnt sonst ab)
    def on_uplink_response(self, resp: dict) -> None:
        if resp.get("capture"):
            threading.Thread(target=self.capture_and_upload, args=("alert", resp.get("event_id")), daemon=True).start()

    def capture_and_upload(self, reason: str, event_id: str | None = None) -> int:
        with self._capture_lock:
            if time.monotonic() - self._last_capture < 10:
                return 0  # höchstens ein Bild alle 10 s
            self._last_capture = time.monotonic()
            data = self.camera.capture()
            if not data:
                log.warning("Kein Kamerabild (%s)", self.camera.kind)
                return 0
            q = f"?reason={reason}" + (f"&event_id={event_id}" if event_id else "")
            status, _ = self.api.upload(f"/api/v1/agent/snapshot{q}", data, self.cfg.token)
            log.info("Kamerabild (%s, %d kB) hochgeladen: HTTP %s", reason, len(data) // 1024, status)
            return status

    def rotate_token(self) -> bool:
        status, resp = self.api.request("POST", "/api/v1/agent/rotate-token", {}, token=self.cfg.token)
        if status != 200 or not resp.get("token"):
            log.error("Token-Rotation fehlgeschlagen (%s)", status)
            return False
        # Erst sicher speichern, dann benutzen – das alte Token bleibt serverseitig kurz gültig.
        self.state.data.update(token=resp["token"], token_issued_at=self.clock())
        self.state.save()
        self.cfg.token = resp["token"]
        log.info("Geräte-Token erneuert")
        return True

    def control_loop(self) -> None:
        interval = max(15, int(self.state.get("heartbeat_s", 60)))
        self.stop.wait(5)  # erst Daten der Quelle abwarten, damit der erste Zustand aussagekräftig ist
        while not self.stop.is_set():
            try:
                resp = self.heartbeat_once()
                if resp:
                    interval = max(15, int(resp.get("heartbeat_s", interval)))
            except Exception as exc:  # Steuerkanal darf die Messkette nie stoppen
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("Heartbeat fehlgeschlagen: %s", self.last_error)
            self.stop.wait(interval)

    # ---- Hauptschleife
    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        threading.Thread(target=self.gw.uplink.run, args=(self.stop,), daemon=True, name="uplink").start()
        threading.Thread(target=self.control_loop, daemon=True, name="control").start()
        cleanup_releases()
        log.info("Agent %s gestartet: Station %s (%s), Quelle %s", VERSION, self.state["station_id"],
                 self.state.get("station_name"), self.state.get("source"))
        if self.state.get("source") == "simulator":
            source = simulator_lines(self.stop)
        else:
            source = serial_lines(self.cfg, self.gw, self.stop)
        for line in source:
            if line:
                self.gw.handle_line(line)
            self.gw.watchdog()
            if self.stop.is_set():
                break
        self.stop.set()
        self.gw.uplink.flush_once()
        log.info("Agent beendet (Code %s)", self.exit_code)
        return self.exit_code


# ---------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agent der Smart Bicycle Box")
    ap.add_argument("--state-dir", default=os.environ.get("BIKE_AGENT_STATE", "/var/lib/bike-agent"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll", help="Mit einer Station koppeln")
    e.add_argument("--url", required=True)
    e.add_argument("--code", default=os.environ.get("BIKE_ENROLL_CODE", ""))
    e.add_argument("--source", choices=["serial", "simulator"], default="serial")
    e.add_argument("--serial-port", default="auto")
    e.add_argument("--ca-file")
    e.add_argument("--name", default="")
    e.add_argument("--allow-http", action="store_true")
    sub.add_parser("run", help="Agent starten")
    sub.add_parser("status", help="Zustand anzeigen (ohne Geheimnisse)")
    sub.add_parser("rollback", help="Auf die vorherige Version zurückschalten")
    sub.add_parser("version")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state = State(Path(args.state_dir))
    try:
        if args.cmd == "version":
            print(VERSION)
        elif args.cmd == "enroll":
            code = args.code or input("Kopplungscode: ").strip()
            data = enroll(state, args.url, code, args.source, args.serial_port, args.ca_file, args.allow_http, args.name)
            print(f"Gekoppelt mit Station '{data.get('station_name')}' ({data['station_id']}) als Gerät {data['device_id']}.")
        elif args.cmd == "status":
            state.load()
            safe = {k: v for k, v in state.data.items() if k != "token"}
            safe["token"] = state["token"][:10] + "…"
            safe["version"] = VERSION
            print(json.dumps(safe, indent=1, ensure_ascii=False))
        elif args.cmd == "rollback":
            prefix = install_dir_of(Path(__file__))
            if prefix is None:
                raise AgentError("keine verwaltete Installation")
            current = (prefix / "current").resolve()
            others = sorted((p for p in (prefix / "releases").iterdir() if p.is_dir() and p != current and not p.name.startswith(".")),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if not others:
                raise AgentError("keine vorherige Version vorhanden")
            switch_current(prefix, others[0])
            print(f"Zurückgeschaltet auf {others[0].name}. Neustart: sudo systemctl restart bike-agent")
        elif args.cmd == "run":
            state.load()
            check_rollback(state.dir)
            return Agent(state).run()
    except AgentError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
