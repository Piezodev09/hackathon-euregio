#!/usr/bin/env bash
# Deletes ALL measurement and event data platform-wide (demo operation only!).
# Customers, stations and accounts are kept. Stop the service first: systemctl stop bike-station
set -euo pipefail
DB=${BIKE_DB_PATH:-/var/lib/bike-station/bike_station.db}
read -r -p "Really delete ALL measurement and event data in $DB? (yes/no) " a
[ "$a" = "yes" ] || exit 1
python3 - "$DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.executescript("DELETE FROM measurement; DELETE FROM event; VACUUM;")
PY
echo "Demo data deleted."
