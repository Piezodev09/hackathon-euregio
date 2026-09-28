#!/usr/bin/env bash
# Copies the agent code into the Home Assistant add-on and sets the add-on version to agent/VERSION.
# Home Assistant builds the add-on from its own folder only, so the code has to live there as a copy.
#   scripts/sync-ha-addon.sh           update the copy
#   scripts/sync-ha-addon.sh --check   exit 1 if the copy or the version is out of date (tests / CI)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/agent"
DST="$ROOT/integrations/home-assistant/bike-station-agent"
VERSION="$(tr -d '[:space:]' < "$SRC/VERSION")"

if [ "${1:-}" = "--check" ]; then
  status=0
  diff -rq -x __pycache__ "$SRC/bikeagent" "$DST/bikeagent" || status=1
  cmp -s "$SRC/VERSION" "$DST/VERSION" || { echo "VERSION differs"; status=1; }
  grep -qx "version: \"$VERSION\"" "$DST/config.yaml" || { echo "config.yaml version is not \"$VERSION\""; status=1; }
  [ "$status" -eq 0 ] && echo "Home Assistant add-on is in sync (agent $VERSION)" \
    || echo "Out of date - run scripts/sync-ha-addon.sh" >&2
  exit "$status"
fi

rm -rf "$DST/bikeagent"
mkdir -p "$DST/bikeagent"
cp "$SRC"/bikeagent/*.py "$DST/bikeagent/"
cp "$SRC/VERSION" "$DST/VERSION"
sed -i.bak "s/^version: .*/version: \"$VERSION\"/" "$DST/config.yaml" && rm -f "$DST/config.yaml.bak"
echo "Synced agent $VERSION into ${DST#"$ROOT"/}"
