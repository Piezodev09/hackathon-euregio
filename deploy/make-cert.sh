#!/usr/bin/env bash
# Erzeugt ein selbst signiertes Zertifikat für die Plattform – MIT IP-Adressen im Zertifikat,
# damit Raspberry Pis die Plattform auch per IP (z. B. https://192.168.0.114) prüfen können.
#
#   sudo deploy/make-cert.sh                       # Hostname + alle lokalen IPs
#   sudo deploy/make-cert.sh 192.168.0.114 bike.schule.lan
#   sudo deploy/make-cert.sh --force 192.168.0.114 # vorhandenes Zertifikat ersetzen
#
# Danach: systemctl restart bike-api. Das Portal zeigt beim Einrichten eines Gateways den
# Fingerabdruck an; der Pi vertraut genau diesem Zertifikat (Pinning), keine Zertifizierungsstelle nötig.
# Für den Dauerbetrieb besser ein Zertifikat der Schul-IT verwenden.
set -euo pipefail

DIR="${BIKE_TLS_DIR:-/etc/bike-station/tls}"
DAYS="${BIKE_TLS_DAYS:-825}"
FORCE=0
EXTRA=()
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    -h|--help) sed -n 2,12p "$0"; exit 0 ;;
    *) EXTRA+=("$a") ;;
  esac
done

if [ -f "$DIR/server.crt" ] && [ "$FORCE" -eq 0 ]; then
  if openssl x509 -in "$DIR/server.crt" -noout -ext subjectAltName 2>/dev/null | grep -q "IP Address"; then
    echo "Zertifikat vorhanden ($DIR/server.crt) – nichts zu tun. Erneuern mit --force."
  else
    echo "WARNUNG: $DIR/server.crt enthält keine IP-Adresse. Pis, die per IP verbinden, lehnen es ab."
    echo "         Erneuern mit: $0 --force <IP-Adresse>"
  fi
  exit 0
fi

HOST_FQDN=$(hostname -f 2>/dev/null || hostname)
HOST_SHORT=$(hostname)
SAN="DNS:$HOST_FQDN,DNS:$HOST_SHORT,DNS:localhost,IP:127.0.0.1"
for ip in $(hostname -I 2>/dev/null || true); do
  case "$ip" in *:*) SAN="$SAN,IP:$ip" ;; *) SAN="$SAN,IP:$ip" ;; esac
done
for e in "${EXTRA[@]+"${EXTRA[@]}"}"; do
  if [[ "$e" =~ ^[0-9.]+$ || "$e" == *:* ]]; then SAN="$SAN,IP:$e"; else SAN="$SAN,DNS:$e"; fi
done
# Doppelte Einträge entfernen
SAN=$(echo "$SAN" | tr ',' '\n' | awk '!seen[$0]++' | paste -sd, -)

install -d -m 750 "$DIR"
umask 077
openssl req -x509 -newkey rsa:2048 -nodes -days "$DAYS" \
  -keyout "$DIR/server.key.new" -out "$DIR/server.crt.new" \
  -subj "/CN=${EXTRA[0]:-$HOST_FQDN}/O=Smart Bicycle Box" \
  -addext "subjectAltName=$SAN" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign" \
  -addext "extendedKeyUsage=serverAuth" 2>/dev/null
mv -f "$DIR/server.key.new" "$DIR/server.key"
mv -f "$DIR/server.crt.new" "$DIR/server.crt"
chmod 640 "$DIR/server.key"; chmod 644 "$DIR/server.crt"
getent group bikestation >/dev/null 2>&1 && chgrp bikestation "$DIR" "$DIR/server.key" || true

echo "Zertifikat erstellt: $DIR/server.crt"
echo "Enthaltene Namen/IPs: $SAN"
openssl x509 -in "$DIR/server.crt" -noout -fingerprint -sha256
PIN=$(openssl x509 -in "$DIR/server.crt" -noout -pubkey | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64)
echo "Schlüssel-Pin: sha256//$PIN"
echo "Jetzt: systemctl restart bike-api"
