#!/usr/bin/env bash
# Konsistente Sicherung der SQLite-Datenbank (zusätzlich zum Proxmox-Snapshot/vzdump der VM).
# Wiederherstellen: systemctl stop bike-api; cp <backup> /var/lib/bike-station/bike_station.db;
#                   chown bikestation: /var/lib/bike-station/bike_station.db; systemctl start bike-api
set -euo pipefail
DB=${BIKE_DB_PATH:-/var/lib/bike-station/bike_station.db}
DEST=${1:-/var/backups/bike-station}
mkdir -p "$DEST"
OUT="$DEST/bike_station-$(date +%Y%m%d-%H%M%S).db"
sqlite3 "$DB" ".backup '$OUT'"
sqlite3 "$OUT" "PRAGMA integrity_check;" | grep -qx ok
echo "Sicherung: $OUT"
