#!/usr/bin/env bash
# Startet die komplette Plattform lokal OHNE Hardware (NUR für die Entwicklung!):
#   API + Portal (http://127.0.0.1:8000) und einen Demo-Kunden.
#
#   scripts/dev.sh                 Agent (wie auf dem Pi) mit eingebautem Simulator – im Portal verwaltbar
#   scripts/dev.sh --interactive   Simulator mit Tastatursteuerung -> Gateway (Rütteln, Sensorfehler …)
#
# Alle Simulatordaten sind als "simuliert" gekennzeichnet.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=agent
if [ "${1:-}" = "--interactive" ]; then MODE=interactive; shift; fi

export BIKE_DB_PATH="${BIKE_DB_PATH:-$PWD/backend/dev.db}"
DEMO_EMAIL="${DEMO_EMAIL:-demo@example.org}"
DEMO_PASSWORD="${DEMO_PASSWORD:-Fahrradplatz-Euregio-2026!}"
DEMO_ENV="$PWD/backend/.dev-demo.env"
GW_CFG="$PWD/pi-gateway/state/config.dev.toml"

if [ ! -f "$DEMO_ENV" ] || [ ! -f "$BIKE_DB_PATH" ]; then
  rm -f "$DEMO_ENV"
  (cd backend && BIKE_CLI_PASSWORD="$DEMO_PASSWORD" python3 -m app.cli create-demo --email "$DEMO_EMAIL") > "$DEMO_ENV"
  chmod 600 "$DEMO_ENV"
fi
# shellcheck disable=SC1090
source "$DEMO_ENV"

mkdir -p "$(dirname "$GW_CFG")"
cat > "$GW_CFG" <<CFG
state_dir = "state"
[api]
url = "http://127.0.0.1:8000"
timeout_s = 3
[station]
id = "$STATION_ID"
CFG

(cd backend && exec python3 -m uvicorn app.main:_app_factory --factory --host 127.0.0.1 --port 8000 --no-server-header) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT
sleep 2

cat <<INFO
────────────────────────────────────────────────────────────
 Portal:        http://127.0.0.1:8000/app
 Login:         $DEMO_EMAIL / $DEMO_PASSWORD
 Kiosk-Anzeige: $DISPLAY_URL
 Registrierungs-/Reset-Mails erscheinen im Log (mail.backend = console).
────────────────────────────────────────────────────────────
INFO
if [ "$MODE" = "agent" ]; then
  AGENT_STATE="$PWD/pi-gateway/state/agent"
  if [ ! -f "$AGENT_STATE/agent.json" ]; then
    CODE=$(cd backend && python3 -m app.cli enrollment-code --station "$STATION_ID")
    BIKE_ENROLL_CODE="$CODE" python3 pi-gateway/agent.py --state-dir "$AGENT_STATE" enroll --url http://127.0.0.1:8000 --source simulator --name "Dev-Agent"
  fi
  echo " Agent läuft (Simulator). Im Portal: Gateways. Tastatursteuerung: scripts/dev.sh --interactive"
  python3 pi-gateway/agent.py --state-dir "$AGENT_STATE" run
  exit $?
fi
cd pi-gateway
BIKE_DEVICE_TOKEN="$DEVICE_TOKEN" python3 simulator.py "$@" | BIKE_DEVICE_TOKEN="$DEVICE_TOKEN" python3 gateway.py --config "$GW_CFG" --stdin --simulated
