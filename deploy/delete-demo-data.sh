#!/usr/bin/env bash
# Löscht PLATTFORMWEIT alle Mess- und Ereignisdaten (nur Demo-Betrieb!). Kunden, Stationen und Konten bleiben erhalten.
set -euo pipefail
DB=${BIKE_DB_PATH:-/var/lib/bike-station/bike_station.db}
read -r -p "Wirklich ALLE Mess- und Ereignisdaten in $DB löschen? (ja/nein) " a
[ "$a" = "ja" ] || exit 1
sqlite3 "$DB" "DELETE FROM measurement; DELETE FROM event; VACUUM;"
echo "Demodaten gelöscht."
