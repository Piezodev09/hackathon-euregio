# Home Assistant integration

This folder contains the **Smart Bike Station agent** add-on for Home Assistant OS / Supervised.
User documentation (shown in Home Assistant's *Documentation* tab):
[bike-station-agent/DOCS.md](bike-station-agent/DOCS.md).

```
repository.yaml                       ← at the Git root: Home Assistant only looks there
integrations/home-assistant/
  bike-station-agent/
    config.yaml                       add-on manifest: options, schema, uart/usb, services: mqtt:want
    build.yaml                        base images per architecture (aarch64, amd64, armv7)
    Dockerfile                        pyserial + paho-mqtt, BIKE_AGENT_SELF_UPDATE=0, state in /data
    run.sh                            → python3 -m bikeagent.homeassistant
    DOCS.md · CHANGELOG.md · translations/{en,de,nl}.yaml · icon.png · logo.png
    bikeagent/ · VERSION              copy of agent/ – never edit here
```

How it works: Home Assistant writes the options to `/data/options.json`. On start,
`bikeagent/homeassistant.py` pairs with the platform on the first start (one-time code, optional CA
fingerprint pinning), applies source and serial port, asks the Supervisor for the MQTT broker
(`GET http://supervisor/services/mqtt` with `SUPERVISOR_TOKEN`) and then runs the normal agent with
MQTT discovery. A new pairing code in the options re-pairs the add-on.

## Maintenance

```bash
scripts/sync-ha-addon.sh           # after every change in agent/ (also sets the add-on version)
scripts/sync-ha-addon.sh --check   # what the tests run
node scripts/render-ha-addon-images.mjs   # icon.png / logo.png from web/static/img/icon.svg
cd agent && python3 -m pytest -q tests/test_homeassistant.py
```

Home Assistant only offers an update when `version` in `config.yaml` changes, so bump
`agent/VERSION` and sync.

## Local test without Home Assistant

```bash
docker build --build-arg BUILD_FROM=python:3.12-slim -t bike-station-ha-addon integrations/home-assistant/bike-station-agent
mkdir -p /tmp/ha-data && cat > /tmp/ha-data/options.json <<'EOF'
{"platform_url": "https://192.168.1.50", "pairing_code": "XXXXX-XXXXX", "ca_fingerprint": "SHA-256 …",
 "source": "simulator", "serial_port": "auto", "device_name": "Home Assistant", "mqtt": false}
EOF
docker run --rm -v /tmp/ha-data:/data bike-station-ha-addon
```

Tested this way against the platform container with a fake Supervisor API and a real Mosquitto
broker (pairing with a pinned CA, discovery, states, restart without re-pairing). Not tested: a real
Home Assistant Supervisor build and a real Arduino on Home Assistant hardware.
