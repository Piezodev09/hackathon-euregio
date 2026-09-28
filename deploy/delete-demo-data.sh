#!/usr/bin/env bash
# Löscht alle Mess- und Ereignisdaten nach Demo-Ende (Plan 7.3). Station/Plätze bleiben erhalten.
set -euo pipefail
DB=${BIKE_DB_PATH:-/var/lib/bike-station/bike_station.db}
read -r -p "Wirklich ALLE Mess- und Ereignisdaten in $DB löschen? (ja/nein) " a
[ "$a" = "ja" ] || exit 1
sqlite3 "$DB" "DELETE FROM measurement; DELETE FROM event; DELETE FROM audit_log; VACUUM;"
echo "Demodaten gelöscht."
