#!/bin/sh
# Installs the Smart Bike Station agent on a Raspberry Pi (Raspberry Pi OS / Debian).
#
# Recommended (verify the script before running it - the portal shows these exact commands):
#   curl -fsSLO __BASE_URL__/install/agent.sh
#   echo '<checksum from the portal>  agent.sh' | sha256sum -c -
#   sudo sh agent.sh --code XXXXX-XXXXX
#
# The script downloads agent package __VERSION__ from the platform, verifies its SHA-256, creates a
# restricted system user, pairs the device with its station using the one-time code and sets up a
# hardened systemd service. If the platform uses its own certificate authority (self-hosted, no
# domain), that CA is embedded below and pinned for every connection. Safe to run repeatedly.
set -eu

BASE_URL="__BASE_URL__"
VERSION="__VERSION__"
SHA256="__SHA256__"
# CA certificate of a self-hosted platform (empty = public CA / system trust store).
EMBEDDED_CA=$(cat <<'CA_PEM_END'
__CA_PEM__
CA_PEM_END
)

CODE="${BIKE_ENROLL_CODE:-}"
SOURCE="serial"
SERIAL_PORT="auto"
CA_FILE=""
PREFIX="/opt/bike-agent"
ETC_DIR="/etc/bike-agent"
STATE_DIR="/var/lib/bike-agent"
AGENT_USER="bike-agent"
NO_SYSTEMD=0
ALLOW_HTTP=0
NAME=""
MQTT_HOST=""
MQTT_PORT="1883"
MQTT_USER=""
MQTT_TLS=0

usage() {
  cat <<USAGE
Usage: sudo sh agent.sh --code XXXXX-XXXXX [options]
  --code CODE                 pairing code from the portal (or environment variable BIKE_ENROLL_CODE)
  --source serial|simulator   data source (default: serial = Arduino over USB)
  --serial-port PATH          e.g. /dev/ttyACM0 (default: detect automatically)
  --ca-file FILE              CA certificate of the platform (default: the CA embedded in this script)
  --name NAME                 device name (default: hostname)
  --mqtt-host HOST            also publish sensors to a local MQTT broker (Home Assistant discovery)
  --mqtt-port PORT            default 1883
  --mqtt-user USER            MQTT user; password via environment variable BIKE_MQTT_PASSWORD
  --mqtt-tls                  connect to the broker with TLS
  --prefix DIR                install directory (default: $PREFIX)
  --etc-dir DIR               configuration directory (default: $ETC_DIR)
  --state-dir DIR             state directory (default: $STATE_DIR)
  --no-systemd                do not create a service (tests, containers)
  --allow-http                allow unencrypted HTTP (ONLY for local development)
USAGE
}

need_arg() { [ $# -ge 2 ] || { echo "Option $1 needs a value" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    --code) need_arg "$@"; CODE="$2"; shift 2 ;;
    --source) need_arg "$@"; SOURCE="$2"; shift 2 ;;
    --serial-port) need_arg "$@"; SERIAL_PORT="$2"; shift 2 ;;
    --ca-file) need_arg "$@"; CA_FILE="$2"; shift 2 ;;
    --name) need_arg "$@"; NAME="$2"; shift 2 ;;
    --mqtt-host) need_arg "$@"; MQTT_HOST="$2"; shift 2 ;;
    --mqtt-port) need_arg "$@"; MQTT_PORT="$2"; shift 2 ;;
    --mqtt-user) need_arg "$@"; MQTT_USER="$2"; shift 2 ;;
    --mqtt-tls) MQTT_TLS=1; shift ;;
    --prefix) need_arg "$@"; PREFIX="$2"; shift 2 ;;
    --etc-dir) need_arg "$@"; ETC_DIR="$2"; shift 2 ;;
    --state-dir) need_arg "$@"; STATE_DIR="$2"; shift 2 ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --allow-http) ALLOW_HTTP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

say() { if [ -t 1 ]; then printf '\033[1m==> %s\033[0m\n' "$*"; else printf '==> %s\n' "$*"; fi; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

IS_ROOT=0
[ "$(id -u)" -eq 0 ] && IS_ROOT=1
[ "$NO_SYSTEMD" -eq 1 ] || [ "$IS_ROOT" -eq 1 ] || die "Please run with sudo."

case "$SOURCE" in serial|simulator) ;; *) die "--source must be serial or simulator" ;; esac
case "$MQTT_PORT" in ''|*[!0-9]*) die "--mqtt-port must be a number" ;; esac

case "$BASE_URL" in
  https://*) ;;
  http://127.0.0.1*|http://localhost*) ;;
  http://*) [ "$ALLOW_HTTP" -eq 1 ] || die "Platform URL is not HTTPS. Development only: --allow-http" ;;
  *) die "Invalid platform URL" ;;
esac

if [ -z "$CODE" ]; then
  if [ -t 0 ]; then
    printf 'Pairing code from the portal: '
    read -r CODE
  else
    die "No pairing code given (--code)."
  fi
fi

command -v python3 >/dev/null 2>&1 || die "python3 is missing (sudo apt install python3)."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python >= 3.11 required (Raspberry Pi OS Bookworm or newer)."

if [ "$IS_ROOT" -eq 1 ] && command -v apt-get >/dev/null 2>&1; then
  say "Installing packages"
  PKGS="ca-certificates"
  [ "$SOURCE" = "serial" ] && PKGS="$PKGS python3-serial"
  [ -n "$MQTT_HOST" ] && PKGS="$PKGS python3-paho-mqtt"
  command -v curl >/dev/null 2>&1 || PKGS="$PKGS curl"
  # shellcheck disable=SC2086
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q $PKGS >/dev/null
fi
command -v curl >/dev/null 2>&1 || die "curl is missing (sudo apt install curl)."

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

# Certificate pinning: an explicit --ca-file wins, otherwise the CA embedded in this script.
if [ -z "$CA_FILE" ] && [ -n "$EMBEDDED_CA" ]; then
  printf '%s\n' "$EMBEDDED_CA" > "$TMP/ca.crt"
  CA_FILE="$TMP/ca.crt"
fi
[ -z "$CA_FILE" ] || [ -r "$CA_FILE" ] || die "CA file not readable: $CA_FILE"

if [ "$IS_ROOT" -eq 1 ] && ! id "$AGENT_USER" >/dev/null 2>&1; then
  say "Creating system user $AGENT_USER"
  useradd --system --no-create-home --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$AGENT_USER"
fi
if [ "$IS_ROOT" -eq 1 ]; then
  usermod -a -G dialout "$AGENT_USER" 2>/dev/null || true
fi

say "Downloading agent $VERSION"
URL="$BASE_URL/install/agent.tar.gz"
if [ -n "$CA_FILE" ]; then
  curl -fsSL --proto '=https,http' --cacert "$CA_FILE" -o "$TMP/agent.tar.gz" "$URL"
else
  curl -fsSL --proto '=https,http' -o "$TMP/agent.tar.gz" "$URL"
fi

say "Verifying checksum"
ACTUAL=$(sha256sum "$TMP/agent.tar.gz" | cut -d' ' -f1)
[ "$ACTUAL" = "$SHA256" ] || die "Checksum mismatch (expected $SHA256, got $ACTUAL). Aborting."

say "Installing to $PREFIX"
REL="$PREFIX/releases/$VERSION"
mkdir -p "$REL" "$ETC_DIR" "$STATE_DIR"
tar -xzf "$TMP/agent.tar.gz" -C "$REL" --no-same-owner
[ -f "$REL/VERSION" ] && [ -f "$REL/bikeagent/agent.py" ] || die "Package layout unexpected"
# Switch the "current" symlink atomically (fallback for systems without mv -T).
ln -sfn "$REL" "$PREFIX/current.new"
if ! mv -Tf "$PREFIX/current.new" "$PREFIX/current" 2>/dev/null; then
  rm -f "$PREFIX/current" "$PREFIX/current.new"
  ln -s "$REL" "$PREFIX/current"
fi
AGENT_CA=""
if [ -n "$CA_FILE" ]; then
  cp "$CA_FILE" "$ETC_DIR/ca.crt"
  chmod 644 "$ETC_DIR/ca.crt"
  AGENT_CA="$ETC_DIR/ca.crt"
fi

if [ "$IS_ROOT" -eq 1 ]; then
  # The agent may only write its releases (self-update) and its state.
  chown -R "$AGENT_USER:$AGENT_USER" "$PREFIX/releases" "$STATE_DIR"
  chown -h "$AGENT_USER:$AGENT_USER" "$PREFIX" "$PREFIX/current"
  chown root:"$AGENT_USER" "$ETC_DIR"
  chmod 750 "$ETC_DIR" "$STATE_DIR"
  cat > /usr/local/bin/bike-agent <<WRAP
#!/bin/sh
# Helper: bike-agent status | rollback | mqtt --mqtt-host HOST | version
[ "\$(id -u)" -eq 0 ] || { echo "Please run with sudo." >&2; exit 1; }
exec runuser -u $AGENT_USER -- env PYTHONPATH=$PREFIX/current python3 -m bikeagent --state-dir $STATE_DIR "\$@"
WRAP
  chmod 755 /usr/local/bin/bike-agent
fi

say "Pairing with the station"
set -- --url "$BASE_URL" --source "$SOURCE" --serial-port "$SERIAL_PORT"
[ -n "$AGENT_CA" ] && set -- "$@" --ca-file "$AGENT_CA"
[ "$ALLOW_HTTP" -eq 1 ] && set -- "$@" --allow-http
[ -n "$NAME" ] && set -- "$@" --name "$NAME"
if [ -n "$MQTT_HOST" ]; then
  set -- "$@" --mqtt-host "$MQTT_HOST" --mqtt-port "$MQTT_PORT"
  [ -n "$MQTT_USER" ] && set -- "$@" --mqtt-username "$MQTT_USER"
  [ "$MQTT_TLS" -eq 1 ] && set -- "$@" --mqtt-tls
fi
# Secrets travel through the environment, not as arguments (not visible in the process list).
export BIKE_ENROLL_CODE="$CODE"
export BIKE_MQTT_PASSWORD="${BIKE_MQTT_PASSWORD:-}"
if [ "$IS_ROOT" -eq 1 ]; then
  runuser -u "$AGENT_USER" -- env BIKE_ENROLL_CODE="$CODE" BIKE_MQTT_PASSWORD="$BIKE_MQTT_PASSWORD" \
    PYTHONPATH="$PREFIX/current" python3 -m bikeagent --state-dir "$STATE_DIR" enroll "$@"
else
  PYTHONPATH="$PREFIX/current" python3 -m bikeagent --state-dir "$STATE_DIR" enroll "$@"
fi
unset BIKE_ENROLL_CODE BIKE_MQTT_PASSWORD

if [ "$NO_SYSTEMD" -eq 0 ]; then
  say "Setting up the service"
  cat > /etc/systemd/system/bike-agent.service <<UNIT
[Unit]
Description=Smart Bike Station - agent (gateway)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$AGENT_USER
Group=$AGENT_USER
SupplementaryGroups=dialout
Environment=PYTHONPATH=$PREFIX/current
ExecStart=/usr/bin/python3 -m bikeagent --state-dir $STATE_DIR run
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths=$STATE_DIR $PREFIX
CapabilityBoundingSet=
DeviceAllow=char-ttyACM rw
DeviceAllow=char-ttyUSB rw

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable bike-agent.service >/dev/null
  systemctl restart bike-agent.service
  sleep 2
  systemctl --no-pager --lines=5 status bike-agent.service || true
fi

say "Done. The gateway appears in the portal under Station -> Settings -> Gateways."
if [ "$IS_ROOT" -eq 1 ]; then
  echo "Status: sudo bike-agent status   Logs: journalctl -u bike-agent -f"
fi
exit 0
