#!/usr/bin/env python3
"""Agent der Smart Bicycle Box für den Raspberry Pi.

Baut auf dem Gateway (gateway.py) auf und ergänzt die Geräteverwaltung aus der Cloud:
  * Kopplung per Einmal-Code aus dem Portal (kein manuelles Kopieren von Tokens)
  * Zustand/Zugangsdaten in einer Datei mit Rechten 0600
  * Heartbeat mit Gesundheitsdaten (Arduino verbunden, Puffer, Temperatur, Speicher …)
  * Konfigurations-Sync: neue/entfernte Stellplätze ohne Neuinstallation
  * Token-Rotation (automatisch alle 30 Tage oder auf Befehl) mit Übergangsfrist
  * feste Fernbefehle (restart, rotate_token, identify) – niemals beliebiger Code
  * Selbst-Update: Paket laden, SHA-256 prüfen, sicher entpacken, umschalten, bei Fehlstart zurückrollen
  * mehrere Stellplätze an einem Pi (je Stellplatz ein Arduino): Ports und NFC-Leser werden automatisch erkannt
    und zugeordnet (hardware.py), die Zuordnung ist im Portal änderbar

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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hardware  # noqa: E402
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
        self.data = migrate_state(json.loads(self.path.read_text()))
        return self

    def save(self) -> None:
        with self._lock:
            mirror_legacy(self.data)
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


STALL_KEYS = ("station_id", "station_name", "device_id", "token", "token_issued_at", "config_version")


def migrate_state(data: dict) -> dict:
    """Zustand bis 1.3 (ein Stellplatz, Felder oben) -> Liste "stalls". Ein fest gewählter Port bleibt fest."""
    if "stalls" not in data and data.get("station_id"):
        stall = {k: data.get(k) for k in STALL_KEYS}
        stall["legacy"] = True  # Sequenzzähler liegt weiter direkt im Zustandsverzeichnis
        port = data.get("serial_port")
        if port and port != "auto":
            stall["assign_port"] = port
        data["stalls"] = [stall]
    return data


def mirror_legacy(data: dict) -> None:
    """Felder des ersten Stellplatzes auch oben speichern: ein Rollback auf 1.3 findet so weiter seine Daten."""
    if data.get("stalls"):
        for k in STALL_KEYS:
            data[k] = data["stalls"][0].get(k)


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


# ---------------------------------------------------------------------- Kopplung
def enroll(state: State, url: str, code: str, source: str, serial_port: str, ca_file: str | None,
           allow_http: bool, name: str = "") -> dict:
    """Koppeln. Ist der Pi schon mit derselben Plattform gekoppelt, kommen die neuen Stellplätze dazu
    (einfach das Installationsskript mit dem neuen Code erneut ausführen)."""
    api = Api(url, ca_file, allow_http)
    status, body = api.request("POST", "/api/v1/agent/enroll", {
        "code": code, "hostname": safe_hostname(), "name": name, "agent_version": VERSION, "os_info": os_info(), "source": source,
    })
    if status != 200:
        raise AgentError(f"Kopplung fehlgeschlagen ({status}: {body.get('detail')})")
    now = time.time()
    stalls = body.get("stalls") or [{k: body.get(k) for k in ("station_id", "station_name", "device_id", "token", "config_version")}]
    new = [{**{k: st.get(k) for k in ("station_id", "station_name", "device_id", "token", "config_version")}, "token_issued_at": now}
           for st in stalls]
    if serial_port not in ("", "auto") and len(new) == 1:
        new[0]["assign_port"] = serial_port
    api_url = body["api_url"] if body.get("api_url", "").startswith(url.rstrip("/")) else url.rstrip("/")
    old: dict = {}
    if state.path.exists():
        try:
            old = migrate_state(json.loads(state.path.read_text()))
        except (OSError, ValueError):
            old = {}
    keep = [st for st in old.get("stalls", []) if old.get("api_url") == api_url and st["station_id"] not in {n["station_id"] for n in new}]
    state.data = {
        **{k: v for k, v in old.items() if k not in STALL_KEYS and old.get("api_url") == api_url},
        "api_url": api_url,
        "gateway_id": body.get("gateway_id") or old.get("gateway_id"),
        "heartbeat_s": body.get("heartbeat_s", 60),
        "source": source,
        "ca_file": ca_file,
        "allow_http": allow_http,
        "enrolled_at": old.get("enrolled_at", now) if keep else now,
        "stalls": keep + new,
    }
    state.data.pop("serial_port", None)
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


# ---------------------------------------------------------------------- Lokale Anzeige (auch ohne Plattform)
LOCAL_PORT = 8088
STALE_S = 30
SERVER_FRESH_S = 10
# Dateien der Kiosk-Anzeige liegen flach im Agent-Paket (ältere Agents entpacken nur flache Pakete).
LOCAL_FILES = {"display.html": "text/html; charset=utf-8", "display.js": "text/javascript; charset=utf-8",
               "display-i18n.js": "text/javascript; charset=utf-8", "display.css": "text/css", "tokens.css": "text/css",
               "components.css": "text/css", "fonts.css": "text/css", "AtkinsonHyperlegible-400.woff2": "font/woff2",
               "AtkinsonHyperlegible-700.woff2": "font/woff2", "AtkinsonHyperlegibleMono.woff2": "font/woff2",
               "icon.svg": "image/svg+xml", "local-overview.html": "text/html; charset=utf-8",
               "local-overview.js": "text/javascript; charset=utf-8"}


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


class LocalDisplay:
    """Die Kiosk-Anzeige am Pi zeigt http://127.0.0.1:8088/local.

    Ein Stellplatz: dessen Anzeige. Mehrere Stellplätze: Übersicht aller Plätze, Einzelanzeige unter /local?stall=<id>.
    Normal: Status der Plattform (inkl. Reservierung, Öffnungszeiten, Check-in). Ist die Plattform länger als
    10 s nicht erreichbar: Zustand direkt vom Sensor – deutlich als OFFLINE gekennzeichnet. Auch hier gilt:
    keine gültige Messung seit 30 s oder Sensorfehler = STATUS UNBEKANNT, nie „frei“.
    """

    def __init__(self, agent: "Agent", files_dir: Path = HERE):
        self.agent = agent
        # Installiert: Dateien liegen neben agent.py. Entwicklung (Repository): unter web/.
        web = files_dir.parent / "web"
        self.search = [files_dir, web, web / "static" / "js", web / "static" / "css", web / "static" / "fonts", web / "static" / "img"]
        self.api = Api(agent.state["api_url"], agent.state.get("ca_file"), agent.state.get("allow_http", False), timeout=2)
        self.server: dict[str, tuple[dict, float]] = {}  # station_id -> (Status, Zeitpunkt)
        self._fetched: dict[str, float] = {}
        self._lock = threading.Lock()

    def stall(self, station_id: str | None) -> "Stall":
        stalls = self.agent.stalls
        return next((s for s in stalls if s.station_id == station_id), stalls[0])

    def _refresh(self, st: "Stall") -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._fetched.get(st.station_id, 0.0) < 1.5:
                return
            self._fetched[st.station_id] = now
        try:
            status, body = self.api.request("GET", "/api/v1/agent/status", token=st.cfg.token)
            if status == 200 and isinstance(body, dict) and "state" in body:
                self.server[st.station_id] = (body, time.monotonic())
        except Exception:  # Netzwerk weg: lokale Daten übernehmen
            pass

    def status(self, station_id: str | None = None) -> dict:
        st = self.stall(station_id)
        self._refresh(st)
        known, ok_at = self.server.get(st.station_id, (None, None))
        if known is not None and ok_at is not None and time.monotonic() - ok_at <= SERVER_FRESH_S:
            return {**known, "offline": False}
        last = st.gw.last_local
        now_mono = time.monotonic()
        if last is None:
            state, reason, wall = "unknown", "no_data", None
        elif now_mono - last["at"] > STALE_S:
            state, reason, wall = "unknown", "stale", last["wall"]
        elif last["sensor_state"] != "ok" or last["occupied"] is None:
            state, reason, wall = "unknown", "sensor_error", last["wall"]
        else:
            state, reason, wall = ("occupied" if last["occupied"] else "free"), None, last["wall"]
        known = known or {}
        return {"station_id": st.station_id, "display_name": known.get("display_name") or st.name,
                "location": known.get("location", ""), "state": state, "unknown_reason": reason, "last_update": _iso(wall),
                "age_s": None if last is None else round(now_mono - last["at"], 1), "stale_after_s": STALE_S, "poll_interval_s": 2,
                "server_time": _iso(time.time()), "alert": None, "session": None, "last_tap": None, "reservation": None, "closed": None,
                "maintenance": bool(known.get("maintenance")), "camera_active": bool(known.get("camera_active")),
                "simulated_data": st.cfg.source == "simulated", "offline": True}

    def overview(self) -> dict:
        return {"stalls": [self.status(s.station_id) for s in self.agent.stalls], "hostname": safe_hostname()}

    def handler(self):
        display = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keine Zugriffslogs
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                path, _, query = self.path.partition("?")
                params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                stall = params.get("stall") or None
                if path == "/local/status":
                    return self._send(200, json.dumps(display.status(stall)).encode(), "application/json")
                if path == "/local/overview":
                    return self._send(200, json.dumps(display.overview()).encode(), "application/json")
                if path in ("/", "/local"):
                    if path == "/":
                        self.send_response(302)
                        self.send_header("Location", "/local")
                        self.end_headers()
                        return None
                    name = "local-overview.html" if len(display.agent.stalls) > 1 and not stall else "display.html"
                else:
                    name = path.rsplit("/", 1)[-1]
                found = next((d / name for d in display.search if (d / name).is_file()), None) if name in LOCAL_FILES else None
                if found is None:
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, found.read_bytes(), LOCAL_FILES[name])

        return Handler

    def serve(self, stop: threading.Event, port: int = LOCAL_PORT) -> None:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), self.handler())
        except OSError as exc:
            log.warning("Lokale Anzeige nicht gestartet (Port %s): %s", port, exc)
            return
        srv.timeout = 1
        log.info("Lokale Anzeige: http://127.0.0.1:%s/local", port)
        while not stop.is_set():
            srv.handle_request()
        srv.server_close()


# ---------------------------------------------------------------------- Stellplatz
class Stall:
    """Ein Stellplatz am Pi: eigenes Geräte-Token, eigener Puffer, eigener Arduino-Port und NFC-Leser."""

    def __init__(self, agent: "Agent", data: dict):
        self.agent = agent
        self.data = data
        s = agent.state
        state_dir = s.dir if data.get("legacy") else s.dir / "stalls" / data["station_id"]
        self.cfg = GatewayConfig(api_url=s["api_url"], station_id=data["station_id"], token=data["token"], serial_port="",
                                 ca_file=s.get("ca_file"), state_dir=state_dir,
                                 source="simulated" if s.get("source") == "simulator" else "live")
        self.gw = Gateway(self.cfg)
        self.port: str | None = None     # zugeordneter serieller Port (stabiler Name)
        self.reader: str | None = None   # zugeordneter externer Leser (USB/PC-SC)
        self._port_stop: threading.Event | None = None

    @property
    def station_id(self) -> str:
        return self.data["station_id"]

    @property
    def name(self) -> str:
        return self.data.get("station_name") or self.station_id

    def fixed(self, key: str) -> str | None:
        return self.data.get(f"assign_{key}")

    def reader_id(self) -> str:
        if self.reader:
            return self.reader
        return f"pn532@{self.port}" if self.gw.has_pn532 and self.port else ""

    # ---- serieller Port (wechselt bei Umstecken oder neuer Zuordnung im Portal)
    def set_port(self, port: str | None, stop: threading.Event) -> None:
        if port == self.port:
            return
        if self._port_stop:
            self._port_stop.set()
        self.port = port
        self.gw.write_back = None
        if port is None:
            log.warning("Stellplatz %s: kein Arduino zugeordnet – Status bleibt UNBEKANNT", self.name)
            return
        log.info("Stellplatz %s liest %s", self.name, port)
        ev = threading.Event()
        self._port_stop = ev
        self.cfg.serial_port = port
        if stop.is_set():
            ev.set()
        threading.Thread(target=self._read_serial, args=(ev,), daemon=True, name=f"serial-{self.station_id[-6:]}").start()

    def _read_serial(self, ev: threading.Event) -> None:
        # ev wird bei neuer Zuordnung oder beim Beenden des Agents gesetzt (Agent.run)
        for line in serial_lines(self.cfg, self.gw, ev):
            if line:
                self.gw.handle_line(line)
            if ev.is_set():
                break

    def close(self) -> None:
        if self._port_stop:
            self._port_stop.set()

    def run_simulator(self, stop: threading.Event) -> None:
        for line in simulator_lines(stop):
            if line:
                self.gw.handle_line(line)

    def hw(self) -> dict:
        a = self.agent
        fw = {st.port: st.gw.hello for st in a.stalls if st.port and st.gw.hello}
        return {"ports": [{"path": p["path"][:160], "kind": p["kind"], "firmware": " ".join(filter(None, [
                    (fw.get(p["path"]) or {}).get("name"), (fw.get(p["path"]) or {}).get("fw")]))[:160]} for p in a.ports[:32]],
                "readers": [{"id": r["id"][:160], "kind": r["kind"], "name": r["name"][:160]} for r in a.readers[:32]],
                "port": (self.port or "")[:160], "reader": self.reader_id()[:160], "camera": a.camera.kind,
                "kiosk": hardware.kiosk_configured(), "stalls": len(a.stalls)}


# ---------------------------------------------------------------------- Agent
HW_SCAN_S = 30


class Agent:
    def __init__(self, state: State, clock=time.time):
        self.state = state
        self.clock = clock
        self.stop = threading.Event()
        self.exit_code = 0
        self.started = time.monotonic()
        self.last_error = ""
        s = migrate_state(state.data)
        self.api = Api(s["api_url"], s.get("ca_file"), s.get("allow_http", False))
        self.stalls = [Stall(self, d) for d in s["stalls"]]
        self.camera = Camera(simulated=s.get("source") == "simulator")
        for st in self.stalls:
            st.gw.uplink.on_response = lambda resp, st=st: self.on_uplink_response(st, resp)
        self.ports: list[dict] = []
        self.readers: list[dict] = []
        self._reader_threads: dict[str, threading.Thread] = {}
        self._last_capture = 0.0
        self._capture_lock = threading.Lock()

    # Kompatibilität (Tests/Anzeige mit einem Stellplatz)
    @property
    def gw(self) -> Gateway:
        return self.stalls[0].gw

    @property
    def cfg(self) -> GatewayConfig:
        return self.stalls[0].cfg

    # ---- Hardware
    def scan_hardware(self) -> None:
        """Ports und Leser erkennen und den Stellplätzen zuordnen (Portal-Zuordnung zuerst)."""
        if self.state.get("source") == "simulator":
            return
        self.ports = hardware.scan_serial()
        names = self.state.get("hid_reader_names", [])
        self.readers = hardware.scan_hid_readers(extra_names=names) + hardware.scan_pcsc()
        by_real = {p["real"]: p["path"] for p in self.ports}
        ids = [st.station_id for st in self.stalls]
        fixed = {st.station_id: by_real.get(st.fixed("port"), st.fixed("port")) for st in self.stalls}
        known = [st.port for st in self.stalls if st.port and st.gw.hello]
        ports = hardware.assign(ids, [p["path"] for p in self.ports], fixed, preferred=known)
        # Externe Leser bevorzugt an Stellplätze ohne eigenen PN532 am Arduino
        order = [st.station_id for st in self.stalls if not st.gw.has_pn532] + [st.station_id for st in self.stalls if st.gw.has_pn532]
        readers = hardware.assign(order, [r["id"] for r in self.readers], {st.station_id: st.fixed("reader") for st in self.stalls})
        for st in self.stalls:
            st.set_port(ports[st.station_id], self.stop)
            st.reader = readers[st.station_id]
        for r in self.readers:
            if r["id"] not in self._reader_threads or not self._reader_threads[r["id"]].is_alive():
                rd = hardware.HidReader(r, self.on_reader_uid, self.state.get("hid_uid_format", "auto")) if r["kind"] == "hid" \
                    else hardware.PcscReader(r, self.on_reader_uid)
                t = threading.Thread(target=rd.run, args=(self.stop,), daemon=True, name=f"reader-{r['kind']}")
                t.start()
                self._reader_threads[r["id"]] = t

    def on_reader_uid(self, reader_id: str, uid: str) -> None:
        st = next((s for s in self.stalls if s.reader == reader_id), None)
        if st is None and len(self.stalls) == 1:
            st = self.stalls[0]
        if st is None:
            log.warning("Karte an Leser %s gelesen, aber keinem Stellplatz zugeordnet (Portal → Lesegeräte)", reader_id)
            return
        st.gw.tap(uid, reader=reader_id)

    # ---- Steuerkanal (je Stellplatz, eigenes Token)
    def heartbeat_once(self, st: Stall | None = None) -> dict | None:
        st = st or self.stalls[0]
        g = st.gw
        serial_ok = g.last_line_at is not None and time.monotonic() - g.last_line_at < st.cfg.arduino_timeout_s
        body = {
            "agent_version": VERSION, "hostname": safe_hostname(), "os_info": os_info(), "source": self.state.get("source", "serial"),
            "uptime_s": int(time.monotonic() - self.started), "serial_connected": serial_ok, "buffer_len": len(g.uplink.buffer),
            "api_online": g.uplink.online, "last_error": self.last_error[:300], "config_version": st.data.get("config_version") or 0,
            "camera": self.camera.kind, "hw": st.hw(),
            **system_health(),
        }
        status, resp = self.api.request("POST", "/api/v1/agent/heartbeat", body, token=st.cfg.token)
        if status == 401:
            self.last_error = "Token abgelehnt (widerrufen?)"
            log.error("Plattform lehnt das Token für %s ab – Gerät im Portal gesperrt? Neu koppeln mit 'enroll'.", st.name)
            return None
        if status != 200:
            self.last_error = f"Heartbeat HTTP {status}"
            return None
        confirm_update(self.state.dir)
        self.apply(resp, st)
        return resp

    def apply(self, resp: dict, st: Stall | None = None) -> None:
        st = st or self.stalls[0]
        changed = False
        if resp.get("config_version", 0) != st.data.get("config_version"):
            st.data["config_version"] = resp["config_version"]
            changed = True
            log.info("Neue Konfiguration %s für %s", resp["config_version"], st.name)
        assign = resp.get("assign") or {}
        for key in ("port", "reader"):
            if key in assign and assign[key] != st.data.get(f"assign_{key}"):
                st.data[f"assign_{key}"] = assign[key]
                changed = True
                log.info("Zuordnung aus dem Portal: %s %s = %s", st.name, key, assign[key] or "automatisch")
        if changed:
            self.state.save()
            if assign:
                threading.Thread(target=self.scan_hardware, daemon=True).start()
        for cmd in resp.get("commands", []):
            if cmd == "restart":
                log.warning("Neustart auf Anforderung aus dem Portal")
                self.exit_code = EXIT_RESTART
                self.stop.set()
            elif cmd == "rotate_token":
                self.rotate_token(st)
            elif cmd == "snapshot":
                threading.Thread(target=self.capture_and_upload, args=("manual", None, st), daemon=True).start()
            elif cmd == "identify":
                log.info("Identifizieren: %s blinkt", st.name)
                st.gw.identify()
            else:
                log.warning("Unbekannter Befehl ignoriert: %r", cmd)
        issued = st.data.get("token_issued_at")
        if issued is not None and self.clock() - issued > TOKEN_MAX_AGE_S:
            self.rotate_token(st)
        upd = resp.get("update")
        if upd:
            try:
                if apply_update(self.api, st.cfg.token, upd, self.state.dir):
                    self.exit_code = EXIT_RESTART
                    self.stop.set()
            except AgentError as exc:
                self.last_error = str(exc)
                log.error("Update fehlgeschlagen: %s", exc)

    # ---- Kamera (nur wenn im Portal freigegeben; die Plattform lehnt sonst ab)
    def on_uplink_response(self, st: Stall, resp: dict) -> None:
        if resp.get("capture"):
            threading.Thread(target=self.capture_and_upload, args=("alert", resp.get("event_id"), st), daemon=True).start()

    def capture_and_upload(self, reason: str, event_id: str | None = None, st: Stall | None = None) -> int:
        st = st or self.stalls[0]
        with self._capture_lock:
            if time.monotonic() - self._last_capture < 10:
                return 0  # höchstens ein Bild alle 10 s
            self._last_capture = time.monotonic()
            data = self.camera.capture()
            if not data:
                log.warning("Kein Kamerabild (%s)", self.camera.kind)
                return 0
            q = f"?reason={reason}" + (f"&event_id={event_id}" if event_id else "")
            status, _ = self.api.upload(f"/api/v1/agent/snapshot{q}", data, st.cfg.token)
            log.info("Kamerabild (%s, %d kB) hochgeladen: HTTP %s", reason, len(data) // 1024, status)
            return status

    def rotate_token(self, st: Stall | None = None) -> bool:
        st = st or self.stalls[0]
        status, resp = self.api.request("POST", "/api/v1/agent/rotate-token", {}, token=st.cfg.token)
        if status != 200 or not resp.get("token"):
            log.error("Token-Rotation fehlgeschlagen (%s)", status)
            return False
        # Erst sicher speichern, dann benutzen – das alte Token bleibt serverseitig kurz gültig.
        st.data.update(token=resp["token"], token_issued_at=self.clock())
        self.state.save()
        st.cfg.token = resp["token"]
        log.info("Geräte-Token für %s erneuert", st.name)
        return True

    def control_loop(self) -> None:
        interval = max(15, int(self.state.get("heartbeat_s", 60)))
        self.stop.wait(5)  # erst Daten der Quelle abwarten, damit der erste Zustand aussagekräftig ist
        while not self.stop.is_set():
            for st in list(self.stalls):
                try:
                    resp = self.heartbeat_once(st)
                    if resp:
                        interval = max(15, int(resp.get("heartbeat_s", interval)))
                except Exception as exc:  # Steuerkanal darf die Messkette nie stoppen
                    self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                    log.warning("Heartbeat fehlgeschlagen: %s", self.last_error)
                if self.stop.is_set():
                    break
            self.stop.wait(interval)

    def hardware_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.scan_hardware()
            except Exception as exc:  # Erkennung darf nie den Agent stoppen
                log.warning("Hardware-Erkennung fehlgeschlagen: %s", exc)
            self.stop.wait(HW_SCAN_S)

    # ---- Hauptschleife
    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        for st in self.stalls:
            threading.Thread(target=st.gw.uplink.run, args=(self.stop,), daemon=True, name=f"uplink-{st.station_id[-6:]}").start()
            if self.state.get("source") == "simulator":
                threading.Thread(target=st.run_simulator, args=(self.stop,), daemon=True, name="simulator").start()
        threading.Thread(target=self.control_loop, daemon=True, name="control").start()
        threading.Thread(target=self.hardware_loop, daemon=True, name="hardware").start()
        port = int(self.state.get("local_display_port", LOCAL_PORT))
        if port:
            threading.Thread(target=LocalDisplay(self).serve, args=(self.stop, port), daemon=True, name="local-display").start()
        cleanup_releases()
        log.info("Agent %s gestartet: %d Stellplatz/Stellplätze (%s), Quelle %s", VERSION, len(self.stalls),
                 ", ".join(st.name for st in self.stalls), self.state.get("source"))
        while not self.stop.is_set():
            for st in self.stalls:
                st.gw.watchdog()
            self.stop.wait(0.5)
        for st in self.stalls:
            st.close()
            st.gw.uplink.flush_once()
        log.info("Agent beendet (Code %s)", self.exit_code)
        return self.exit_code


# ---------------------------------------------------------------------- Diagnose
def doctor(state: State, out=print) -> int:
    """Prüft die typischen Fehlerquellen vor Ort und gibt eine verständliche Liste aus. Rückgabe: Anzahl Fehler."""
    errors = 0

    def line(ok: bool | None, text: str, hint: str = "") -> None:
        nonlocal errors
        mark = "✓" if ok else ("–" if ok is None else "✗")
        errors += ok is False
        out(f" {mark} {text}" + (f"\n     → {hint}" if hint and ok is False else ""))

    out(f"Smart Bicycle Box – Diagnose (Agent {VERSION})")
    line(sys.version_info >= (3, 11), f"Python {platform.python_version()}", "Raspberry Pi OS Bookworm oder neuer verwenden")
    try:
        state.load()
        stalls = state["stalls"]
        line(True, f"Gekoppelt: {len(stalls)} Stellplatz/Stellplätze – " + ", ".join(s.get("station_name") or s["station_id"] for s in stalls))
    except (AgentError, KeyError) as exc:
        line(False, "Nicht gekoppelt", f"sudo sh agent.sh --code … ({exc})")
        return errors
    src = state.get("source", "serial")
    if src == "serial":
        try:
            import serial  # noqa: F401
            line(True, "pyserial installiert")
        except ImportError:
            line(False, "pyserial fehlt", "sudo apt install python3-serial")
        ports = hardware.scan_serial()
        line(bool(ports), "Serielle Geräte: " + (", ".join(f"{p['kind']} {p['path']}" for p in ports) or "keine"),
             "Arduino per USB anschließen, Kabel mit Datenleitung verwenden")
        if ports and len(ports) < len(stalls):
            line(False, f"{len(ports)} Arduino für {len(stalls)} Stellplätze",
                 "je Stellplatz einen Arduino anschließen – Plätze ohne Arduino zeigen STATUS UNBEKANNT")
        for s in stalls:
            fixed = s.get("assign_port")
            if fixed:
                line(any(fixed in (p["path"], p["real"]) for p in ports), f"{s.get('station_name')}: fest zugeordnet {fixed}",
                     "Gerät fehlt – im Portal (Geräte) auf „automatisch“ stellen oder Arduino anschließen")
        readers = hardware.scan_hid_readers(extra_names=state.get("hid_reader_names", [])) + hardware.scan_pcsc()
        line(None, "Externe NFC-Leser: " + (", ".join(f"{r['name']} ({r['kind']})" for r in readers) or "keine (PN532 am Arduino genügt)"))
        for r in readers:
            if r["kind"] == "hid":
                line(os.access(r["event"], os.R_OK), f"Leser {r['name']} lesbar", "Benutzer bike-agent in Gruppe 'input'; Installationsskript erneut ausführen")
        if not hardware.pcsc_available() and any(p for p in Path("/dev/bus/usb").glob("*/*")) and Path("/usr/sbin/pcscd").exists():
            line(None, "pcscd vorhanden, aber pyscard fehlt: sudo apt install python3-pyscard")
    else:
        line(None, "Datenquelle: Simulator (keine Hardware)")
    cam = Camera(simulated=src == "simulator")
    line(None if cam.kind == "none" else True, f"Kamera: {cam.kind}",
         "optional – nur nötig, wenn die Kamera im Portal freigegeben ist")
    line(None, "Display-Autostart eingerichtet" if hardware.kiosk_configured() else "Display-Autostart: nicht eingerichtet (optional)")
    ca = state.get("ca_file")
    if ca:
        line(Path(ca).is_file(), f"Plattform-Zertifikat {ca}", "Datei fehlt – neu koppeln")
    try:
        api = Api(state["api_url"], ca, state.get("allow_http", False), timeout=8)
        t0 = time.time()
        status, _ = api.request("GET", "/health")
        line(status == 200, f"Plattform erreichbar: {state['api_url']} ({int((time.time() - t0) * 1000)} ms)", f"HTTP {status}")
        for s in stalls:
            status, who = api.request("GET", "/api/v1/agent/whoami", token=s["token"])
            line(status == 200, f"Geräte-Token gültig (Stellplatz: {who.get('station_name', s.get('station_name') or '?')})",
                 "Gerät im Portal gesperrt? Neu koppeln")
        if status == 200 and isinstance(who.get("server_time"), (int, float)):
            skew = time.time() - who["server_time"]
            line(abs(skew) <= 5, f"Uhrzeit weicht {skew:+.1f} s von der Plattform ab",
                 "Zeitsynchronisation prüfen: timedatectl (NTP aktiv?) – sonst erscheinen Messungen als veraltet")
    except (urllib.error.URLError, OSError, AgentError) as exc:
        reason = getattr(exc, "reason", exc)  # urllib verpackt TLS-Fehler in URLError
        if isinstance(reason, ssl.SSLError):
            line(False, f"TLS-Fehler: {reason}",
                 "Zertifikat geändert oder IP fehlt im Zertifikat → Befehle im Portal neu anzeigen und neu koppeln")
        else:
            line(False, f"Plattform nicht erreichbar: {exc}", "Netzwerk, Firewall (Port) und Adresse prüfen")
    out("Fertig: " + ("keine Fehler." if not errors else f"{errors} Problem(e) gefunden."))
    return errors


# ---------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agent der Smart Bicycle Box")
    ap.add_argument("--state-dir", default=os.environ.get("BIKE_AGENT_STATE", "/var/lib/bike-agent"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll", help="Mit Stellplätzen koppeln (erneut ausführen = weitere Stellplätze hinzufügen)")
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
    sub.add_parser("doctor", help="Diagnose: Kopplung, Arduino, NFC-Leser, Kamera, Plattform, Zertifikat")
    sub.add_parser("hardware", help="Erkannte Hardware anzeigen (Arduino, NFC-Leser, Kamera)")
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
            for st in data["stalls"]:
                print(f"Gekoppelt: Stellplatz '{st.get('station_name')}' ({st['station_id']}) als Gerät {st['device_id']}.")
        elif args.cmd == "status":
            state.load()
            safe = {k: v for k, v in state.data.items() if k not in ("token", "stalls")}
            safe["token"] = (state.get("token") or "")[:10] + "…"
            safe["stalls"] = [{**st, "token": (st.get("token") or "")[:10] + "…"} for st in state["stalls"]]
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
        elif args.cmd == "doctor":
            return 1 if doctor(state) else 0
        elif args.cmd == "hardware":
            ports = hardware.scan_serial()
            readers = hardware.scan_hid_readers() + hardware.scan_pcsc()
            print(json.dumps({"ports": ports, "readers": readers, "camera": Camera().kind,
                              "summary": hardware.summary(ports, readers)}, indent=1, ensure_ascii=False))
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
