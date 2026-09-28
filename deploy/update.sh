#!/usr/bin/env bash
# Aktualisiert die Plattform auf der VM (als root). Bei fehlgeschlagenem Health-Check wird automatisch
# auf den vorherigen Stand zurückgesetzt. Die Datenbank wird vorher gesichert (deploy/backup.sh).
#
#   sudo /opt/smart-bike-station/deploy/update.sh            # neuester Stand des aktuellen Branches
#   sudo /opt/smart-bike-station/deploy/update.sh --tests    # vorher Tests ausführen
set -euo pipefail
APP=/opt/smart-bike-station
PORT="${BIKE_PORT:-8443}"
RUN_TESTS=0
[ "${1:-}" = "--tests" ] && RUN_TESTS=1
cd "$APP"

PREV=$(git rev-parse HEAD)
echo "==> Sicherung"
[ -x deploy/backup.sh ] && deploy/backup.sh || echo "   (Sicherung übersprungen – deploy/backup.sh prüfen)"
echo "==> Neuen Stand holen"
git fetch --quiet
git merge --ff-only --quiet '@{u}' || { echo "Kein Fast-Forward möglich – lokale Änderungen? Abbruch."; exit 1; }
NEW=$(git rev-parse HEAD)
[ "$PREV" = "$NEW" ] && { echo "Bereits aktuell ($NEW)."; exit 0; }
"$APP/venv/bin/pip" install --quiet -r backend/requirements.txt
if [ "$RUN_TESTS" -eq 1 ]; then
  "$APP/venv/bin/pip" install --quiet -r backend/requirements-dev.txt
  (cd backend && "$APP/venv/bin/python" -m pytest -q -p no:cacheprovider) || { echo "Tests fehlgeschlagen – Rücksetzen."; git reset --quiet --hard "$PREV"; exit 1; }
fi
echo "==> Neustart"
systemctl restart bike-api
for _ in $(seq 1 20); do
  if curl -fsk "https://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "Aktualisiert: ${PREV:0:8} -> ${NEW:0:8}. Health-Check OK."
    exit 0
  fi
  sleep 1
done
echo "Health-Check fehlgeschlagen – zurück auf ${PREV:0:8}."
git reset --quiet --hard "$PREV"
systemctl restart bike-api
exit 1
