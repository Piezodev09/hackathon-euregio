#!/bin/sh
# Container entry point of the platform: data key, own CA + certificate, configuration, uvicorn on :8443.
#   BIKE_PUBLIC_HOSTS  comma-separated IPs / host names the platform is reached by (first = URL), e.g. 192.168.1.50,bikestation.local
#   BIKE_PUBLIC_PORT   external HTTPS port (default 443)
#   BIKE_EXTRA_HOSTS   further names only used inside Docker networks (e.g. "platform" for the demo agent)
#   BIKE_TLS_CERT / BIKE_TLS_KEY   own certificate files instead of the generated CA (optional)
# All other BIKE_* settings (mail, sign-up, operator details, ...) are passed through unchanged.
# "bike-station-entrypoint cli <command>" (alias: bike-station <command>) runs the platform CLI with the
# same configuration, e.g.  docker compose exec platform bike-station demo --reset
set -eu
DATA=/data
TLS="$DATA/tls"
HOSTS="${BIKE_PUBLIC_HOSTS:-localhost}"
PORT="${BIKE_PUBLIC_PORT:-443}"
FIRST="${HOSTS%%,*}"
ALL="$HOSTS${BIKE_EXTRA_HOSTS:+,$BIKE_EXTRA_HOSTS},127.0.0.1,localhost"
mkdir -p "$TLS"
umask 077

# Data key: generated once, kept in the volume (back it up separately!).
if [ -z "${BIKE_DATA_KEY:-}" ]; then
  [ -s "$DATA/datakey" ] || python -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())' > "$DATA/datakey"
  BIKE_DATA_KEY="$(cat "$DATA/datakey")"
  export BIKE_DATA_KEY
fi

if [ -n "${BIKE_TLS_CERT:-}" ] && [ -n "${BIKE_TLS_KEY:-}" ]; then
  CERT="$BIKE_TLS_CERT"; KEY="$BIKE_TLS_KEY"
else
  CERT="$TLS/server.crt"; KEY="$TLS/server.key"
  [ "${1:-}" = "cli" ] || python /opt/bike-station/deploy/certs.py --dir "$TLS" --hosts "$ALL"
  export BIKE_CA_FILE="${BIKE_CA_FILE:-$TLS/ca.crt}"
fi

SUFFIX=""
[ "$PORT" = "443" ] || SUFFIX=":$PORT"
export BIKE_ENV="${BIKE_ENV:-production}"
export BIKE_DB_PATH="${BIKE_DB_PATH:-$DATA/bike_station.db}"
export BIKE_BASE_URL="${BIKE_BASE_URL:-https://$FIRST$SUFFIX}"
export BIKE_ALLOWED_HOSTS="${BIKE_ALLOWED_HOSTS:-$ALL}"
export BIKE_MAIL_BACKEND="${BIKE_MAIL_BACKEND:-none}"
export BIKE_WEBHOOK_ALLOW_PRIVATE="${BIKE_WEBHOOK_ALLOW_PRIVATE:-1}"
umask 022
if [ "${1:-}" = "cli" ]; then
  shift
  exec python -m app.cli "$@"
fi
exec uvicorn app.main:_app_factory --factory --host 0.0.0.0 --port 8443 \
  --ssl-keyfile "$KEY" --ssl-certfile "$CERT" --no-server-header --no-access-log
