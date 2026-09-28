#!/usr/bin/env bash
# Setzt die LOKALE Entwicklungs-/Demo-Umgebung zurück und startet sie frisch (NUR Entwicklung!):
# löscht backend/dev.db, den Demo-Zugang und den Zustand des lokalen Agenten, dann scripts/dev.sh.
# Die Demo enthält: einen Stellplatz, Demo-Karte 04A1B2C3D4, Parktarif, 7 Tage simulierte Parkhistorie,
# Kiosk- und Stellplatz-Link. Alle Daten sind als "simuliert" gekennzeichnet.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" != "--yes" ]; then
  read -r -p "Lokale Demo-Datenbank und Agent-Zustand löschen? [j/N] " a
  case "$a" in j|J|y|Y) ;; *) echo "Abgebrochen."; exit 1 ;; esac
else
  shift
fi
rm -f backend/dev.db backend/dev.db-wal backend/dev.db-shm backend/.dev-demo.env
rm -rf pi-gateway/state backend/snapshots
echo "Zurückgesetzt."
exec scripts/dev.sh "$@"
