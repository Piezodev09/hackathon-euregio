#!/usr/bin/env bash
# Startet die komplette Kette lokal OHNE Hardware:
#   Simulator -> Gateway -> API/Dashboard (http://127.0.0.1:8000)
# Alle Daten sind als "simuliert" gekennzeichnet.
set -euo pipefail
cd "$(dirname "$0")/.."

export BIKE_DEVICE_TOKENS="${BIKE_DEVICE_TOKENS:-dev-device-token-change-me}"
export BIKE_ADMIN_TOKENS="${BIKE_ADMIN_TOKENS:-dev-admin-token-change-me}"
export BIKE_DEVICE_TOKEN="${BIKE_DEVICE_TOKENS%%,*}"
export BIKE_DB_PATH="${BIKE_DB_PATH:-$PWD/backend/dev.db}"

(cd backend && exec python3 -m uvicorn app.main:_app_factory --factory --host 127.0.0.1 --port 8000) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT
sleep 2

echo "Dashboard: http://127.0.0.1:8000   Admin-Token: $BIKE_ADMIN_TOKENS"
cd pi-gateway
python3 simulator.py "$@" | python3 gateway.py --config config.dev.toml --stdin --simulated
