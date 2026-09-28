#!/usr/bin/env bash
# Löscht PLATTFORMWEIT alle Mess-, Ereignis- und Parkdaten sowie Kamerabilder (nur Demo-Betrieb!).
# Kunden, Stationen, Konten, Karten, Tarife, Lizenzen und Rechnungen bleiben erhalten.
set -euo pipefail
DB=${BIKE_DB_PATH:-/var/lib/bike-station/bike_station.db}
SNAP_DIR="$(dirname "$DB")/snapshots"
read -r -p "Wirklich ALLE Mess-, Ereignis- und Parkdaten sowie Kamerabilder in $DB löschen? (ja/nein) " a
[ "$a" = "ja" ] || exit 1
sqlite3 "$DB" "DELETE FROM snapshot; DELETE FROM parking_session; DELETE FROM measurement; DELETE FROM event; VACUUM;"
[ -d "$SNAP_DIR" ] && find "$SNAP_DIR" -maxdepth 1 -name '*.jpg' -type f -delete
echo "Demodaten gelöscht."
