#!/bin/sh
# Installiert den Agenten der Smarten Radstation auf einem Raspberry Pi (Raspberry Pi OS / Debian).
#
# Empfohlen (Skript vorher prüfen):
#   curl -fsSLO __BASE_URL__/install/agent.sh
#   sha256sum agent.sh            # mit der Prüfsumme im Portal vergleichen
#   sudo sh agent.sh --code XXXXX-XXXXX
#
# Das Skript lädt das Agent-Paket (Version __VERSION__) von der Plattform, prüft dessen SHA-256,
# legt einen eingeschränkten Systembenutzer an, koppelt das Gerät per Einmal-Code mit der Station
# und richtet einen gehärteten systemd-Dienst ein. Es ist wiederholbar (idempotent).
set -eu

BASE_URL="__BASE_URL__"
VERSION="__VERSION__"
SHA256="__SHA256__"

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

usage() {
  cat <<USAGE
Aufruf: sudo sh agent.sh --code XXXXX-XXXXX [Optionen]
  --code CODE          Kopplungscode aus dem Portal (alternativ Umgebungsvariable BIKE_ENROLL_CODE)
  --source serial|simulator   Datenquelle (Standard: serial = Arduino per USB)
  --serial-port PFAD   z. B. /dev/ttyACM0 (Standard: automatisch erkennen)
  --ca-file DATEI      eigenes CA-Zertifikat der Plattform (selbst signiert)
  --name NAME          Gerätename (Standard: Hostname)
  --prefix DIR         Installationsverzeichnis (Standard: $PREFIX)
  --etc-dir DIR        Konfigurationsverzeichnis (Standard: $ETC_DIR)
  --state-dir DIR      Zustandsverzeichnis (Standard: $STATE_DIR)
  --no-systemd         keinen Dienst einrichten (Tests, Container)
  --allow-http         unverschlüsseltes HTTP erlauben (NUR lokale Entwicklung)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --code) CODE="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --serial-port) SERIAL_PORT="$2"; shift 2 ;;
    --ca-file) CA_FILE="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --etc-dir) ETC_DIR="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --allow-http) ALLOW_HTTP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unbekannte Option: $1" >&2; usage; exit 2 ;;
  esac
done

say() { if [ -t 1 ]; then printf '\033[1m==> %s\033[0m\n' "$*"; else printf '==> %s\n' "$*"; fi; }
die() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }

IS_ROOT=0
[ "$(id -u)" -eq 0 ] && IS_ROOT=1
[ "$NO_SYSTEMD" -eq 1 ] || [ "$IS_ROOT" -eq 1 ] || die "Bitte mit sudo ausführen."

case "$SOURCE" in serial|simulator) ;; *) die "--source muss serial oder simulator sein" ;; esac

case "$BASE_URL" in
  https://*) ;;
  http://127.0.0.1*|http://localhost*) ;;
  http://*) [ "$ALLOW_HTTP" -eq 1 ] || die "Plattform-URL ist nicht HTTPS. Nur für Entwicklung: --allow-http" ;;
  *) die "Ungültige Plattform-URL" ;;
esac

if [ -z "$CODE" ]; then
  if [ -t 0 ]; then
    printf 'Kopplungscode aus dem Portal: '
    read -r CODE
  else
    die "Kein Kopplungscode angegeben (--code)."
  fi
fi

command -v python3 >/dev/null 2>&1 || die "python3 fehlt (sudo apt install python3)."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python >= 3.11 erforderlich (Raspberry Pi OS Bookworm oder neuer)."

if [ "$IS_ROOT" -eq 1 ] && command -v apt-get >/dev/null 2>&1; then
  say "Pakete installieren"
  PKGS="ca-certificates"
  [ "$SOURCE" = "serial" ] && PKGS="$PKGS python3-serial"
  command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || PKGS="$PKGS curl"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q $PKGS >/dev/null
fi

if [ "$IS_ROOT" -eq 1 ] && ! id "$AGENT_USER" >/dev/null 2>&1; then
  say "Systembenutzer $AGENT_USER anlegen"
  useradd --system --no-create-home --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$AGENT_USER"
fi
if [ "$IS_ROOT" -eq 1 ]; then
  usermod -a -G dialout "$AGENT_USER" 2>/dev/null || true
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

say "Agent $VERSION herunterladen"
URL="$BASE_URL/install/agent.tar.gz"
if command -v curl >/dev/null 2>&1; then
  if [ -n "$CA_FILE" ]; then curl -fsSL --proto '=https,http' --cacert "$CA_FILE" -o "$TMP/agent.tar.gz" "$URL"
  else curl -fsSL -o "$TMP/agent.tar.gz" "$URL"; fi
else
  if [ -n "$CA_FILE" ]; then wget -q --ca-certificate="$CA_FILE" -O "$TMP/agent.tar.gz" "$URL"
  else wget -q -O "$TMP/agent.tar.gz" "$URL"; fi
fi

say "Prüfsumme kontrollieren"
ACTUAL=$(sha256sum "$TMP/agent.tar.gz" | cut -d' ' -f1)
[ "$ACTUAL" = "$SHA256" ] || die "Prüfsumme stimmt nicht (erwartet $SHA256, erhalten $ACTUAL). Abbruch."

say "Installieren nach $PREFIX"
REL="$PREFIX/releases/$VERSION"
mkdir -p "$REL" "$ETC_DIR" "$STATE_DIR"
tar -xzf "$TMP/agent.tar.gz" -C "$REL" --no-same-owner
ln -sfn "$REL" "$PREFIX/current.new" && mv -Tf "$PREFIX/current.new" "$PREFIX/current" 2>/dev/null \
  || { rm -f "$PREFIX/current"; ln -s "$REL" "$PREFIX/current"; rm -f "$PREFIX/current.new"; }
[ -n "$CA_FILE" ] && cp "$CA_FILE" "$ETC_DIR/ca.crt"

if [ "$IS_ROOT" -eq 1 ]; then
  # Der Agent darf nur Releases (Selbst-Update) und seinen Zustand schreiben.
  chown -R "$AGENT_USER:$AGENT_USER" "$PREFIX/releases" "$STATE_DIR"
  chown -h "$AGENT_USER:$AGENT_USER" "$PREFIX" "$PREFIX/current"
  chown root:"$AGENT_USER" "$ETC_DIR"
  chmod 750 "$ETC_DIR" "$STATE_DIR"
  cat > /usr/local/bin/bike-agent <<WRAP
#!/bin/sh
exec sudo -u $AGENT_USER python3 $PREFIX/current/agent.py --state-dir $STATE_DIR "\$@"
WRAP
  chmod 755 /usr/local/bin/bike-agent
fi

say "Mit der Station koppeln"
ENROLL_ARGS="--url $BASE_URL --source $SOURCE --serial-port $SERIAL_PORT"
[ -n "$CA_FILE" ] && ENROLL_ARGS="$ENROLL_ARGS --ca-file $ETC_DIR/ca.crt"
[ "$ALLOW_HTTP" -eq 1 ] && ENROLL_ARGS="$ENROLL_ARGS --allow-http"
[ -n "$NAME" ] && ENROLL_ARGS="$ENROLL_ARGS --name $NAME"
RUN_AS=""
[ "$IS_ROOT" -eq 1 ] && RUN_AS="runuser -u $AGENT_USER --"
# Code über die Umgebung statt als Argument übergeben (nicht in der Prozessliste sichtbar).
export BIKE_ENROLL_CODE="$CODE"
# shellcheck disable=SC2086
$RUN_AS python3 "$PREFIX/current/agent.py" --state-dir "$STATE_DIR" enroll $ENROLL_ARGS
unset BIKE_ENROLL_CODE

if [ "$NO_SYSTEMD" -eq 0 ]; then
  say "Dienst einrichten"
  cat > /etc/systemd/system/bike-agent.service <<UNIT
[Unit]
Description=Smarte Radstation - Agent (Gateway)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$AGENT_USER
Group=$AGENT_USER
SupplementaryGroups=dialout
ExecStart=/usr/bin/python3 $PREFIX/current/agent.py --state-dir $STATE_DIR run
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
  systemctl enable --now bike-agent.service
  sleep 2
  systemctl --no-pager --lines=5 status bike-agent.service || true
fi

say "Fertig. Das Gateway erscheint im Portal unter Station → Einstellungen → Gateways."
[ "$IS_ROOT" -eq 1 ] && echo "Status: bike-agent status   Logs: journalctl -u bike-agent -f"
exit 0
