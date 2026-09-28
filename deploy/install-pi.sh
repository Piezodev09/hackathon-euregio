#!/usr/bin/env bash
# MANUELLE Alternative zur Agent-Installation (ohne Portal-Kopplung, ohne Fernwartung/Updates/Kamera).
# Empfohlen ist stattdessen: Portal -> Stellplatz -> Einstellungen -> "Gateway einrichten" (docs/agent.md).
# Einrichtung des Raspberry Pi. Als root ausführen, Repo liegt unter /opt/smart-bike-station.
set -euo pipefail
APP=/opt/smart-bike-station
apt-get update
apt-get install -y python3 python3-serial
id bikegw >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin -G dialout bikegw
install -d -m 750 -g bikegw /etc/bike-gateway
[ -f /etc/bike-gateway/config.toml ] || install -m 640 -g bikegw "$APP/pi-gateway/config.example.toml" /etc/bike-gateway/config.toml
[ -f /etc/bike-gateway/gateway.env ] || (umask 077; cp "$APP/deploy/gateway.env.example" /etc/bike-gateway/gateway.env)
install -m 644 "$APP/deploy/systemd/bike-gateway.service" /etc/systemd/system/
systemctl daemon-reload
echo "Jetzt /etc/bike-gateway/config.toml (API-URL, CA, Port) und gateway.env (Token) anpassen,"
echo "danach:  systemctl enable --now bike-gateway   und   journalctl -u bike-gateway -f"
