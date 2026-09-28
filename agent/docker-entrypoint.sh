#!/bin/sh
# Container entry point: pair on the first start (environment variables), configure MQTT, run the agent.
#   BIKE_PLATFORM_URL   https://<platform>           (required for pairing)
#   BIKE_PAIRING_CODE   code from the portal         (required for pairing, used once)
#   BIKE_CA_FINGERPRINT SHA-256 of the platform CA   (self-hosted platform with its own CA)
#   BIKE_SOURCE         serial | simulator           (default serial)
#   BIKE_SERIAL_PORT    /dev/ttyACM0 | auto          (default auto)
#   BIKE_NAME           device name in the portal
#   BIKE_MQTT_HOST / BIKE_MQTT_PORT / BIKE_MQTT_USERNAME / BIKE_MQTT_PASSWORD / BIKE_MQTT_TLS=1
set -eu
STATE="${BIKE_AGENT_STATE:-/data}"
agent() { python -m bikeagent --state-dir "$STATE" "$@"; }

if [ ! -f "$STATE/agent.json" ]; then
  if [ -z "${BIKE_PLATFORM_URL:-}" ] || [ -z "${BIKE_PAIRING_CODE:-}" ]; then
    echo "Not paired yet: set BIKE_PLATFORM_URL and BIKE_PAIRING_CODE (portal: Station -> Settings -> Set up gateway)." >&2
    exit 1
  fi
  set -- --url "$BIKE_PLATFORM_URL" --source "${BIKE_SOURCE:-serial}" --serial-port "${BIKE_SERIAL_PORT:-auto}"
  [ -z "${BIKE_NAME:-}" ] || set -- "$@" --name "$BIKE_NAME"
  [ -z "${BIKE_CA_FINGERPRINT:-}" ] || set -- "$@" --ca-fingerprint "$BIKE_CA_FINGERPRINT"
  [ "${BIKE_ALLOW_HTTP:-0}" != "1" ] || set -- "$@" --allow-http
  BIKE_ENROLL_CODE="$BIKE_PAIRING_CODE" agent enroll "$@"
fi

if [ -n "${BIKE_MQTT_HOST:-}" ]; then
  set -- --mqtt-host "$BIKE_MQTT_HOST" --mqtt-port "${BIKE_MQTT_PORT:-1883}"
  [ -z "${BIKE_MQTT_USERNAME:-}" ] || set -- "$@" --mqtt-username "$BIKE_MQTT_USERNAME"
  [ "${BIKE_MQTT_TLS:-0}" != "1" ] || set -- "$@" --mqtt-tls
  agent mqtt "$@"   # the password is read from BIKE_MQTT_PASSWORD
fi

exec python -m bikeagent --state-dir "$STATE" run
