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
  KEY=$(python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
  (umask 077; sed -e "s|REPLACE_WITH_DATA_KEY|$KEY|" "$APP/deploy/api.env.example" > /etc/bike-station/api.env)
  echo "Datenschlüssel erzeugt. JETZT /etc/bike-station/api.env anpassen (BASE_URL, Hosts, SMTP)"
  echo "und den Datenschlüssel zusätzlich sicher offline sichern (ohne ihn sind 2FA-Geheimnisse verloren)."
fi

# Selbst signiertes Zertifikat mit Hostname UND IP-Adressen (für Pis, die per IP verbinden).
# Eigene IP/Namen ergänzen: sudo deploy/make-cert.sh --force 192.168.0.114 bike.schule.lan
# Für den Schulbetrieb besser ein Zertifikat der IT nach /etc/bike-station/tls/server.{crt,key} legen.
bash "$APP/deploy/make-cert.sh" "$@"
chgrp bikestation /etc/bike-station /etc/bike-station/tls /etc/bike-station/tls/server.key /etc/bike-station/api.env
chmod 640 /etc/bike-station/tls/server.key /etc/bike-station/api.env

install -m 644 "$APP/deploy/systemd/bike-api.service" /etc/systemd/system/
install -m 644 "$APP/deploy/nftables.conf.example" /etc/nftables.conf.bike-example
systemctl daemon-reload
systemctl enable --now bike-api
echo "Zertifikat: $(openssl x509 -in /etc/bike-station/tls/server.crt -noout -fingerprint -sha256)"
echo "Fertig. Firewall-Beispiel: /etc/nftables.conf.bike-example (anpassen, dann aktivieren)."
echo "Plattform-Admin anlegen:"
echo "  cd $APP/backend && sudo -u bikestation env \$(cat /etc/bike-station/api.env | xargs) $APP/venv/bin/python -m app.cli create-platform-admin --email ops@example.org"
