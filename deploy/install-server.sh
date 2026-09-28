#!/usr/bin/env bash
# Installs (or updates) the Smart Bike Station platform on Debian 12 - in a Proxmox LXC container,
# a VM or on bare metal. Designed for self-hosting WITHOUT a domain and WITHOUT a mail server:
#
#   * own small certificate authority + server certificate for all IPs, the host name and
#     bikestation.local (agents pin the CA, browsers show a one-time warning or import the CA)
#   * HTTPS on port 443, HTTP on port 80 only redirects
#   * mail backend "none": invitations and password resets are handed over as link / QR code,
#     new organisations need approval by the platform admin
#   * first admin via a one-time setup link (no shell needed)
#   * daily database backup (cron)
#
# Run as root from a checkout of the repository:
#   sudo deploy/install-server.sh [options]
# Running it again updates the code, keeps data, keys and certificates (renews the server
# certificate when IPs change) and restarts the service.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="/opt/bike-station"
ETC="/etc/bike-station"
DATA="/var/lib/bike-station"
BACKUPS="/var/backups/bike-station"
SVC_USER="bikestation"
HTTPS_PORT=443
HTTP_PORT=80
PRIMARY_IP=""
MDNS_NAME="bikestation.local"
WITH_ML=1
DEMO_MODEL=1
WITH_AVAHI=1
WITH_SYSTEMD=1
SKIP_APT=0
WHEELHOUSE=""

usage() {
  cat <<USAGE
Usage: sudo $0 [options]
  --ip ADDRESS         primary IPv4 address for the URL (default: first global IPv4 address)
  --https-port PORT    HTTPS port (default 443)
  --http-port PORT     HTTP redirect port, 0 = none (default 80)
  --prefix DIR         installation directory (default $PREFIX)
  --data-dir DIR       database and state (default $DATA)
  --etc-dir DIR        configuration, keys, certificates (default $ETC)
  --mdns-name NAME     additional mDNS name (default $MDNS_NAME)
  --no-avahi           do not install avahi (no https://$MDNS_NAME)
  --no-ml              skip the AI dependencies (scikit-learn); occupancy etc. keep working
  --no-demo-model      do not train the demo AI model on simulated data
  --wheelhouse DIR     install Python packages offline from pre-downloaded wheels
  --skip-apt           do not install Debian packages (they must already be present)
  --no-systemd         only install and configure; print how to start (tests, containers)
USAGE
}

need_arg() { [ $# -ge 2 ] || { echo "Option $1 needs a value" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    --ip) need_arg "$@"; PRIMARY_IP="$2"; shift 2 ;;
    --https-port) need_arg "$@"; HTTPS_PORT="$2"; shift 2 ;;
    --http-port) need_arg "$@"; HTTP_PORT="$2"; shift 2 ;;
    --prefix) need_arg "$@"; PREFIX="$2"; shift 2 ;;
    --data-dir) need_arg "$@"; DATA="$2"; shift 2 ;;
    --etc-dir) need_arg "$@"; ETC="$2"; shift 2 ;;
    --mdns-name) need_arg "$@"; MDNS_NAME="$2"; shift 2 ;;
    --no-avahi) WITH_AVAHI=0; shift ;;
    --no-ml) WITH_ML=0; DEMO_MODEL=0; shift ;;
    --no-demo-model) DEMO_MODEL=0; shift ;;
    --wheelhouse) need_arg "$@"; WHEELHOUSE="$2"; shift 2 ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --no-systemd) WITH_SYSTEMD=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

say() { if [ -t 1 ]; then printf '\033[1m==> %s\033[0m\n' "$*"; else printf '==> %s\n' "$*"; fi; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
is_port() { case "$1" in ''|*[!0-9]*) return 1 ;; *) [ "$1" -le 65535 ] ;; esac; }

[ "$(id -u)" -eq 0 ] || die "Please run as root (sudo)."
[ -f "$SRC/server/app/main.py" ] || die "Run this script from a checkout of the repository ($SRC)."
is_port "$HTTPS_PORT" && [ "$HTTPS_PORT" -gt 0 ] || die "--https-port must be a port number"
is_port "$HTTP_PORT" || die "--http-port must be a port number or 0"
[ "$HTTP_PORT" != "$HTTPS_PORT" ] || die "HTTP and HTTPS port must differ"

# ---------------------------------------------------------------------- packages
if [ "$SKIP_APT" -eq 0 ]; then
  command -v apt-get >/dev/null 2>&1 || die "apt-get not found - Debian 12 expected (or use --skip-apt)."
  say "Installing Debian packages"
  PKGS="python3 python3-venv python3-pip openssl ca-certificates curl cron"
  [ "$WITH_AVAHI" -eq 1 ] && PKGS="$PKGS avahi-daemon avahi-utils"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q >/dev/null
  # shellcheck disable=SC2086
  apt-get install -y -q $PKGS >/dev/null
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "Python >= 3.11 required (Debian 12)."
command -v openssl >/dev/null 2>&1 || die "openssl missing"

# ---------------------------------------------------------------------- addresses
mapfile -t IPS < <(ip -4 -o addr show scope global 2>/dev/null | awk '{split($4, a, "/"); print a[1]}' | sort -u)
if [ ${#IPS[@]} -eq 0 ]; then
  mapfile -t IPS < <(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | grep -v '^127\.' || true)
fi
[ -n "$PRIMARY_IP" ] || PRIMARY_IP="${IPS[0]:-127.0.0.1}"
[[ "$PRIMARY_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "--ip must be an IPv4 address"
ALL_IPS=("$PRIMARY_IP")
for ip in "${IPS[@]}" 127.0.0.1; do
  [[ " ${ALL_IPS[*]} " == *" $ip "* ]] || ALL_IPS+=("$ip")
done
HOST_SHORT="$(hostname -s 2>/dev/null || hostname)"
NAMES=("$HOST_SHORT" "$HOST_SHORT.local" "localhost")
if [ -n "$MDNS_NAME" ] && [[ " ${NAMES[*]} " != *" $MDNS_NAME "* ]]; then NAMES+=("$MDNS_NAME"); fi
PORT_SUFFIX=""
[ "$HTTPS_PORT" = "443" ] || PORT_SUFFIX=":$HTTPS_PORT"
BASE_URL="https://$PRIMARY_IP$PORT_SUFFIX"
ALLOWED_HOSTS="$(IFS=,; echo "${ALL_IPS[*]},${NAMES[*]}")"

# ---------------------------------------------------------------------- user and directories
if ! id "$SVC_USER" >/dev/null 2>&1; then
  say "Creating system user $SVC_USER"
  useradd --system --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi
install -d -m 755 "$PREFIX"
install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$DATA"
install -d -m 750 -o root -g "$SVC_USER" "$ETC" "$ETC/tls"
install -d -m 700 -o "$SVC_USER" -g "$SVC_USER" "$BACKUPS"

# ---------------------------------------------------------------------- code
say "Installing code to $PREFIX"
if [ "$(cd "$SRC" && pwd -P)" != "$(cd "$PREFIX" && pwd -P)" ]; then
  for d in server web agent deploy; do rm -rf "${PREFIX:?}/$d"; done
  mkdir -p "$PREFIX/ml/models" "$PREFIX/ml/data"
  (cd "$SRC" && tar cf - --exclude='__pycache__' --exclude='*.pyc' --exclude='*.db' --exclude='*.db-*' \
      --exclude='*.datakey' --exclude='.setup-token' --exclude='.dev-demo.env' --exclude='agent/state' \
      --exclude='ml/models/*.joblib' --exclude='ml/data/*.csv' \
      server web agent deploy ml README.md) | (cd "$PREFIX" && tar xf - --no-same-owner)
fi
chown -R root:root "$PREFIX"
chmod -R go-w "$PREFIX"

say "Creating the Python environment"
[ -x "$PREFIX/venv/bin/python" ] || python3 -m venv "$PREFIX/venv"
PIP=("$PREFIX/venv/bin/pip" install -q --disable-pip-version-check)
[ -n "$WHEELHOUSE" ] && PIP+=(--no-index --find-links "$WHEELHOUSE")
"${PIP[@]}" -r "$PREFIX/server/requirements.txt"
if [ "$WITH_ML" -eq 1 ]; then
  "${PIP[@]}" -r "$PREFIX/server/requirements-ml.txt" || { echo "AI dependencies could not be installed - continuing without AI" >&2; DEMO_MODEL=0; }
fi

if [ "$DEMO_MODEL" -eq 1 ] && ! ls "$PREFIX"/ml/models/*.joblib >/dev/null 2>&1; then
  say "Training the demo AI model on SIMULATED data (labelled as such in the portal)"
  (cd "$PREFIX" && "$PREFIX/venv/bin/python" ml/generate_synthetic.py >/dev/null \
    && "$PREFIX/venv/bin/python" ml/train.py ml/data/synthetic.csv >/dev/null) \
    || echo "Demo model training failed - the AI shows 'not available'" >&2
fi

# ---------------------------------------------------------------------- certificates
TLS="$ETC/tls"
if [ ! -s "$TLS/ca.key" ] || [ ! -s "$TLS/ca.crt" ]; then
  say "Creating the local certificate authority"
  (umask 077; openssl ecparam -name prime256v1 -genkey -noout -out "$TLS/ca.key")
  openssl req -x509 -new -key "$TLS/ca.key" -sha256 -days 3650 -out "$TLS/ca.crt" \
    -subj "/O=Smart Bike Station/CN=Smart Bike Station local CA ($HOST_SHORT)" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash"
  rm -f "$TLS/server.crt"
fi
SAN=""
for ip in "${ALL_IPS[@]}"; do SAN="$SAN,IP:$ip"; done
for n in "${NAMES[@]}"; do SAN="$SAN,DNS:$n"; done
SAN="${SAN#,}"
renew=0
[ -s "$TLS/server.crt" ] && [ -s "$TLS/server.key" ] || renew=1
[ "$(cat "$TLS/server.san" 2>/dev/null)" = "$SAN" ] || renew=1
openssl x509 -checkend $((30 * 86400)) -noout -in "$TLS/server.crt" >/dev/null 2>&1 || renew=1
if [ "$renew" -eq 1 ]; then
  say "Issuing the server certificate for: ${SAN//,/ }"
  (umask 077; openssl ecparam -name prime256v1 -genkey -noout -out "$TLS/server.key")
  EXT="$(mktemp)"
  trap 'rm -f "$EXT"' EXIT
  printf 'subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid\n' "$SAN" > "$EXT"
  openssl req -new -key "$TLS/server.key" -subj "/O=Smart Bike Station/CN=$PRIMARY_IP" \
    | openssl x509 -req -CA "$TLS/ca.crt" -CAkey "$TLS/ca.key" -CAcreateserial -days 825 -sha256 \
        -extfile "$EXT" -out "$TLS/server.crt" 2>/dev/null
  printf '%s' "$SAN" > "$TLS/server.san"
fi
chown root:root "$TLS/ca.key" && chmod 600 "$TLS/ca.key"
chown root:"$SVC_USER" "$TLS/server.key" && chmod 640 "$TLS/server.key"
chmod 644 "$TLS/ca.crt" "$TLS/server.crt"
FINGERPRINT="$(openssl x509 -in "$TLS/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)"

# ---------------------------------------------------------------------- configuration
say "Writing $ETC/server.env"
DATA_KEY=""
[ -f "$ETC/server.env" ] && DATA_KEY="$(sed -n 's/^BIKE_DATA_KEY=//p' "$ETC/server.env" | head -n1)"
if [ -z "$DATA_KEY" ]; then
  DATA_KEY="$(python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
  NEW_KEY=1
else
  NEW_KEY=0
fi
(umask 027
cat > "$ETC/server.env.new" <<ENV
# Managed by deploy/install-server.sh - changes are overwritten on the next run.
# Put your own settings (e.g. SMTP) into $ETC/server.env.local, it is loaded afterwards.
BIKE_ENV=production
BIKE_CONFIG=$PREFIX/server/config.toml
BIKE_DB_PATH=$DATA/bike_station.db
BIKE_BASE_URL=$BASE_URL
BIKE_ALLOWED_HOSTS=$ALLOWED_HOSTS
BIKE_MAIL_BACKEND=none
BIKE_WEBHOOK_ALLOW_PRIVATE=1
BIKE_CA_FILE=$TLS/ca.crt
BIKE_DATA_KEY=$DATA_KEY
ENV
)
mv "$ETC/server.env.new" "$ETC/server.env"
if [ ! -f "$ETC/server.env.local" ]; then
  cat > "$ETC/server.env.local" <<'ENV'
# Local overrides (loaded after server.env). Examples:
# BIKE_MAIL_BACKEND=smtp
# BIKE_SMTP_HOST=smtp.example.org
# BIKE_SMTP_USER=bikes@example.org
# BIKE_SMTP_PASSWORD=...
# BIKE_SIGNUP=closed            # open | approval | closed
# Operator details for /legal/imprint and /legal/privacy:
# BIKE_OPERATOR_NAME=City of Example
# BIKE_OPERATOR_ADDRESS=Town hall\nMarket 1\n12345 Example
# BIKE_CONTACT_EMAIL=bikes@example.org
ENV
fi
chown root:"$SVC_USER" "$ETC/server.env" "$ETC/server.env.local"
chmod 640 "$ETC/server.env" "$ETC/server.env.local"

# Admin helper: run the CLI with the service configuration.
cat > /usr/local/sbin/bike-station <<WRAP
#!/bin/sh
# Smart Bike Station CLI, e.g.: bike-station create-platform-admin --email ops@example.org
set -a
. $ETC/server.env
[ -f $ETC/server.env.local ] && . $ETC/server.env.local
set +a
cd $PREFIX/server && exec runuser -u $SVC_USER -- $PREFIX/venv/bin/python -m app.cli "\$@"
WRAP
chmod 750 /usr/local/sbin/bike-station

# Quick configuration check with the same settings the service will use.
# shellcheck disable=SC1091
( set -a; . "$ETC/server.env"; . "$ETC/server.env.local"; set +a
  cd "$PREFIX/server" && runuser -u "$SVC_USER" -- "$PREFIX/venv/bin/python" -c 'from app.config import load_settings; load_settings()' ) \
  || die "Configuration check failed (see above)."

# ---------------------------------------------------------------------- backups
cat > /etc/cron.d/bike-station <<CRON
# Daily consistent SQLite backup of the Smart Bike Station (keeps 14 copies).
17 3 * * * $SVC_USER BIKE_DB_PATH=$DATA/bike_station.db $PREFIX/deploy/backup.sh $BACKUPS >/dev/null
CRON
chmod 644 /etc/cron.d/bike-station

# ---------------------------------------------------------------------- services
render_unit() {
  sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@ETC@|$ETC|g" -e "s|@DATA@|$DATA|g" -e "s|@USER@|$SVC_USER|g" \
      -e "s|@PORT@|$HTTPS_PORT|g" -e "s|@HTTP_PORT@|$HTTP_PORT|g" "$PREFIX/deploy/systemd/$1"
}
if [ "$WITH_SYSTEMD" -eq 1 ]; then
  say "Setting up systemd services"
  render_unit bike-station.service > /etc/systemd/system/bike-station.service
  if [ "$HTTP_PORT" != "0" ]; then
    render_unit bike-station-redirect.service > /etc/systemd/system/bike-station-redirect.service
  fi
  if [ "$WITH_AVAHI" -eq 1 ] && command -v avahi-publish >/dev/null 2>&1 && [ "$MDNS_NAME" != "$HOST_SHORT.local" ]; then
    cat > /etc/systemd/system/bike-station-mdns.service <<UNIT
[Unit]
Description=Smart Bike Station - publish $MDNS_NAME via mDNS
After=avahi-daemon.service
Requires=avahi-daemon.service

[Service]
ExecStart=/usr/bin/avahi-publish -a -R $MDNS_NAME $PRIMARY_IP
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
  fi
  systemctl daemon-reload
  systemctl enable -q bike-station.service
  systemctl restart bike-station.service
  if [ "$HTTP_PORT" != "0" ]; then
    systemctl enable -q bike-station-redirect.service && systemctl restart bike-station-redirect.service
  fi
  if [ "$WITH_AVAHI" -eq 1 ] && [ -f /etc/avahi/avahi-daemon.conf ]; then
    # avahi refuses to start in unprivileged LXC containers because of its process limit.
    sed -i 's/^rlimit-nproc=/#rlimit-nproc=/' /etc/avahi/avahi-daemon.conf
    systemctl restart avahi-daemon 2>/dev/null || echo "avahi-daemon did not start - https://$MDNS_NAME will not resolve" >&2
  fi
  if [ -f /etc/systemd/system/bike-station-mdns.service ]; then
    systemctl enable -q bike-station-mdns.service && systemctl restart bike-station-mdns.service || true
  fi
  systemctl enable -q cron 2>/dev/null || true
  say "Waiting for the platform"
  for _ in $(seq 1 60); do
    curl -fsS --cacert "$TLS/ca.crt" "https://127.0.0.1:$HTTPS_PORT/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS --cacert "$TLS/ca.crt" "https://127.0.0.1:$HTTPS_PORT/health" >/dev/null \
    || { journalctl -u bike-station -n 30 --no-pager || true; die "The platform did not start."; }
  for _ in $(seq 1 10); do [ -s "$DATA/.setup-token" ] && break; sleep 1; done
fi

# ---------------------------------------------------------------------- summary
echo
echo "================================================================================"
echo " Smart Bike Station is installed."
echo
echo "   Portal:            $BASE_URL/app"
for n in "${NAMES[@]}"; do
  case "$n" in *.local) [ "$WITH_AVAHI" -eq 1 ] && echo "   Also reachable:    https://$n$PORT_SUFFIX (mDNS, in the LAN)" ;; esac
done
echo "   CA certificate:    $BASE_URL/install/ca.crt"
echo "   CA fingerprint:    SHA-256 $FINGERPRINT"
echo "                      (compare it before trusting the certificate in your browser)"
if [ -s "$DATA/.setup-token" ]; then
  echo
  echo "   FIRST STEP - create the admin account and your organisation:"
  echo "   $BASE_URL/app#/setup?token=$(cat "$DATA/.setup-token")"
fi
if [ "$NEW_KEY" -eq 1 ]; then
  echo
  echo "   A new data key was created in $ETC/server.env. Keep a copy offline:"
  echo "   without it, 2FA secrets cannot be decrypted after restoring a backup."
fi
echo
echo "   Backups:  daily to $BACKUPS (cron). Proxmox: also snapshot / vzdump the container."
echo "   Logs:     journalctl -u bike-station -f        CLI: bike-station --help"
if [ "$WITH_SYSTEMD" -eq 0 ]; then
  echo
  echo "   Not started (--no-systemd). Start manually:"
  echo "   set -a; . $ETC/server.env; set +a; cd $PREFIX/server && \\"
  echo "     $PREFIX/venv/bin/uvicorn app.main:_app_factory --factory --host 0.0.0.0 --port $HTTPS_PORT \\"
  echo "       --ssl-keyfile $TLS/server.key --ssl-certfile $TLS/server.crt --no-server-header"
fi
echo "   If the IP address changes, run this script again (use a DHCP reservation or a static IP)."
echo "================================================================================"
