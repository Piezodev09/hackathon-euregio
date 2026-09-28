#!/usr/bin/env bash
# Consistent backup of the SQLite database (in addition to Proxmox snapshots / vzdump of the container).
#   deploy/backup.sh [target-dir]        default target: /var/backups/bike-station, keeps 14 backups
# Restore: systemctl stop bike-station; cp <backup> /var/lib/bike-station/bike_station.db;
#          chown bikestation: /var/lib/bike-station/bike_station.db; systemctl start bike-station
set -euo pipefail
DB=${BIKE_DB_PATH:-/var/lib/bike-station/bike_station.db}
DEST=${1:-/var/backups/bike-station}
KEEP=${BIKE_BACKUP_KEEP:-14}
[ -f "$DB" ] || { echo "Database not found: $DB" >&2; exit 1; }
mkdir -p "$DEST"
chmod 700 "$DEST"
OUT="$DEST/bike_station-$(date +%Y%m%d-%H%M%S).db"
python3 - "$DB" "$OUT" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()
sys.exit(0 if ok == "ok" else 1)
PY
chmod 600 "$OUT"
# Keep only the newest $KEEP backups.
find "$DEST" -maxdepth 1 -name 'bike_station-*.db' -printf '%T@ %p\n' | sort -rn | tail -n +"$((KEEP + 1))" | cut -d' ' -f2- | xargs -r rm -f
echo "Backup: $OUT"
