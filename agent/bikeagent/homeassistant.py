"""Start-up of the Home Assistant add-on (``integrations/home-assistant/bike-station-agent``).

Home Assistant writes the add-on options to ``/data/options.json``; this module turns them into the
agent state and then runs the normal agent:

  * first start: pair with ``platform_url`` + ``pairing_code`` (+ ``ca_fingerprint`` for a self-hosted
    platform with its own CA). The code is used once; a *different* code later re-pairs the add-on
    (e.g. moving the gateway to another station).
  * every start: apply ``source``/``serial_port`` and, with ``mqtt: true``, fetch the broker credentials
    from the Supervisor (``services: mqtt:want`` → usually the Mosquitto add-on) so the spaces appear in
    Home Assistant via MQTT discovery without typing a password.
  * self-update is off: Home Assistant updates the add-on as a whole.

Run: ``python3 -m bikeagent.homeassistant`` (the add-on's ``run.sh``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from .agent import Agent, AgentError, State, detect_serial_port, enroll, fetch_pinned_ca

log = logging.getLogger("homeassistant")

OPTIONS_FILE = Path(os.environ.get("BIKE_HA_OPTIONS", "/data/options.json"))
SUPERVISOR_URL = os.environ.get("BIKE_HA_SUPERVISOR_URL", "http://supervisor")


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def load_options(path: Path = OPTIONS_FILE) -> dict:
    try:
        opts = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise AgentError(f"Add-on options not readable ({path}): {exc}") from None
    return {
        "platform_url": str(opts.get("platform_url") or "").strip().rstrip("/"),
        "pairing_code": str(opts.get("pairing_code") or "").strip(),
        "ca_fingerprint": str(opts.get("ca_fingerprint") or "").strip(),
        "source": opts.get("source") if opts.get("source") in ("serial", "simulator") else "serial",
        "serial_port": str(opts.get("serial_port") or "auto").strip() or "auto",
        "device_name": str(opts.get("device_name") or "").strip()[:80],
        "mqtt": bool(opts.get("mqtt", True)),
    }


def supervisor_mqtt(token: str | None, base: str = SUPERVISOR_URL, timeout: float = 5) -> dict | None:
    """Broker credentials from the Supervisor services API, or None when no broker is installed."""
    if not token:
        return None
    req = urllib.request.Request(f"{base}/services/mqtt", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read(64 * 1024) or b"{}").get("data") or {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("MQTT service not available from the Supervisor (%s)", getattr(exc, "code", exc))
        return None
    if not data.get("host"):
        return None
    return {"host": data["host"], "port": int(data.get("port") or 1883), "username": data.get("username") or "",
            "password": data.get("password") or "", "tls": bool(data.get("ssl")), "discovery_prefix": "homeassistant"}


def prepare(state: State, opts: dict, token: str | None, supervisor: str = SUPERVISOR_URL) -> State:
    """Pair if needed and apply the options. Returns the loaded state (raises AgentError with a clear message)."""
    code = opts["pairing_code"]
    paired = state.exists
    if paired:
        state.load()
    new_code = bool(code) and state.get("pairing_code_sha") != _code_hash(code)
    if not paired or new_code:
        if not opts["platform_url"] or not code:
            raise AgentError("Not paired yet: set 'platform_url' and 'pairing_code' in the add-on configuration "
                             "(portal: Station -> Settings -> Set up gateway), then restart the add-on.")
        ca_file = None
        if opts["ca_fingerprint"]:
            ca_file = str(fetch_pinned_ca(opts["platform_url"], opts["ca_fingerprint"], state.dir / "ca.crt"))
        data = enroll(state, opts["platform_url"], code, opts["source"], opts["serial_port"], ca_file, False,
                      opts["device_name"] or "Home Assistant")
        log.info("Paired with station '%s' (%s)", data.get("station_name"), data["station_id"])
        state.data["pairing_code_sha"] = _code_hash(code)
    elif opts["platform_url"] and opts["platform_url"] != str(state.get("api_url", "")).rstrip("/"):
        log.warning("platform_url differs from the paired platform (%s) - enter a new pairing code to switch",
                    state.get("api_url"))
    state.data["source"] = opts["source"]
    state.data["serial_port"] = detect_serial_port() if opts["serial_port"] == "auto" else opts["serial_port"]
    mqtt = supervisor_mqtt(token, supervisor) if opts["mqtt"] else None
    if mqtt:
        state.data["mqtt"] = mqtt
        log.info("MQTT: publishing to %s:%s (Home Assistant discovery)", mqtt["host"], mqtt["port"])
    else:
        state.data.pop("mqtt", None)
        if opts["mqtt"]:
            log.warning("No MQTT broker found - install the Mosquitto broker add-on to get Home Assistant entities")
    state.save()
    return state


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.environ["BIKE_AGENT_SELF_UPDATE"] = "0"  # Home Assistant updates the add-on
    state = State(Path(os.environ.get("BIKE_AGENT_STATE", "/data")))
    try:
        opts = load_options()
        prepare(state, opts, os.environ.get("SUPERVISOR_TOKEN"))
        return Agent(state).run()
    except AgentError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
