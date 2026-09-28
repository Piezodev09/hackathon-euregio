#!/usr/bin/env bash
# Einrichtung der Debian-VM (Proxmox). Als root ausführen, Repo liegt unter /opt/smart-bike-station.
# Vorher mit der IT klären: VLAN/IP, DNS-Name, Firewall-Freigaben, Zertifikat (docs/betrieb.md).
set -euo pipefail
APP=/opt/smart-bike-station

apt-get update
apt-get install -y python3 python3-venv sqlite3 nftables openssl

id bikestation >/dev/null 2>&1 || useradd --system --home /var/lib/bike-station --shell /usr/sbin/nologin bikestation
install -d -o bikestation -g bikestation -m 750 /var/lib/bike-station
install -d -m 750 /etc/bike-station /etc/bike-station/tls

python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -r "$APP/backend/requirements.txt"

if [ ! -f /etc/bike-station/api.env ]; then
  DEV=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
  ADM=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
  (umask 077; sed -e "s/REPLACE_WITH_DEVICE_TOKEN/$DEV/" -e "s/REPLACE_WITH_ADMIN_TOKEN/$ADM/" \
      "$APP/deploy/api.env.example" > /etc/bike-station/api.env)
  echo "Tokens erzeugt in /etc/bike-station/api.env – Geräte-Token sicher auf den Pi übertragen."
fi

if [ ! -f /etc/bike-station/tls/server.key ]; then
  echo "Kein Zertifikat gefunden – selbst signiertes Demo-Zertifikat wird erzeugt."
  echo "Für den Schulbetrieb ein vertrauenswürdiges Zertifikat der IT verwenden!"
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
    -keyout /etc/bike-station/tls/server.key -out /etc/bike-station/tls/server.crt \
    -subj "/CN=$(hostname -f)" -addext "subjectAltName=DNS:$(hostname -f),DNS:$(hostname)"
fi
chgrp bikestation /etc/bike-station /etc/bike-station/tls /etc/bike-station/tls/server.key /etc/bike-station/api.env
chmod 640 /etc/bike-station/tls/server.key /etc/bike-station/api.env

install -m 644 "$APP/deploy/systemd/bike-api.service" /etc/systemd/system/
install -m 644 "$APP/deploy/nftables.conf.example" /etc/nftables.conf.bike-example
systemctl daemon-reload
systemctl enable --now bike-api
echo "Fertig. Firewall-Beispiel: /etc/nftables.conf.bike-example (anpassen, dann aktivieren)."
