#!/usr/bin/env bash
# Runs the whole platform locally WITHOUT hardware (development and demos only!):
#   platform + portal on http://127.0.0.1:8000, a demo customer and a paired agent.
#
#   scripts/dev.sh                 agent (exactly as on the Pi) with its built-in simulator
#   scripts/dev.sh --interactive   keyboard-driven simulator piped into the agent (shake, sensor fault ...)
#
# All simulator data is labelled "simulated" in the portal and on the kiosk display.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
MODE=agent
if [ "${1:-}" = "--interactive" ]; then MODE=interactive; shift; fi

export BIKE_DB_PATH="${BIKE_DB_PATH:-$ROOT/server/dev.db}"
DEMO_EMAIL="${DEMO_EMAIL:-demo@example.org}"
DEMO_PASSWORD="${DEMO_PASSWORD:-Bike-Parking-Euregio-2026!}"
DEMO_ENV="$ROOT/server/.dev-demo.env"
AGENT_STATE="$ROOT/agent/state/dev"
URL="http://127.0.0.1:8000"

if [ ! -f "$DEMO_ENV" ] || [ ! -f "$BIKE_DB_PATH" ]; then
  rm -f "$DEMO_ENV"
  rm -rf "$AGENT_STATE"
  (cd server && BIKE_CLI_PASSWORD="$DEMO_PASSWORD" python3 -m app.cli create-demo --email "$DEMO_EMAIL") > "$DEMO_ENV"
  chmod 600 "$DEMO_ENV"
fi
# shellcheck disable=SC1090
source "$DEMO_ENV"

(cd server && exec python3 -m uvicorn app.main:_app_factory --factory --host 127.0.0.1 --port 8000 --no-server-header) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  curl -fsS "$URL/health" >/dev/null 2>&1 && break
  sleep 0.2
done

cat <<INFO
────────────────────────────────────────────────────────────
 Portal:          $URL/app
 Sign-in:         $DEMO_EMAIL / $DEMO_PASSWORD
 Kiosk display:   $DISPLAY_URL
 Sign-up / reset e-mails appear in this log (mail backend "console").
────────────────────────────────────────────────────────────
INFO

agent() { PYTHONPATH="$ROOT/agent" python3 -m bikeagent --state-dir "$AGENT_STATE" "$@"; }
if [ ! -f "$AGENT_STATE/agent.json" ]; then
  CODE=$(cd server && python3 -m app.cli enrollment-code --station "$STATION_ID")
  BIKE_ENROLL_CODE="$CODE" agent enroll --url "$URL" --source simulator --name "Dev agent"
fi

if [ "$MODE" = "agent" ]; then
  echo " Agent running (built-in simulator). Portal: Gateways. Keyboard control: scripts/dev.sh --interactive"
  agent run
else
  echo " Simulator commands: p A (park/remove), b A (bump), s A (shake), e A (sensor fault), q (quit)"
  PYTHONPATH="$ROOT/agent" python3 -m bikeagent.simulator "$@" | agent run --source stdin
fi
