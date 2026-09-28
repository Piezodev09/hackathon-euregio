#!/usr/bin/env bash
# Alle schnellen Prüfungen, wie sie auch vor einem Commit laufen sollten.
set -euo pipefail
cd "$(dirname "$0")/.."
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*"; exit 1; }

(cd backend && python3 -m pytest -q -p no:cacheprovider) || fail "Backend-Tests"
ok "Backend-Tests"
(cd pi-gateway && python3 -m pytest -q -p no:cacheprovider tests) || fail "Gateway-Tests"
ok "Gateway-Tests"

if command -v node >/dev/null 2>&1; then
  for f in web/static/js/*.js; do node --input-type=module --check < "$f" || fail "JS-Syntax: $f"; done
  ok "JS-Syntax"
  node --input-type=module -e '
    globalThis.localStorage = { getItem() { return null; }, setItem() {} };
    globalThis.document = { documentElement: {} };
    const { STRINGS } = await import("./web/static/js/i18n.js");
    const langs = Object.keys(STRINGS), all = new Set(langs.flatMap((l) => Object.keys(STRINGS[l])));
    const miss = langs.flatMap((l) => [...all].filter((k) => !(k in STRINGS[l])).map((k) => `${l}:${k}`));
    if (miss.length) { console.error("Fehlende Übersetzungen:", miss.join(" ")); process.exit(1); }
  ' || fail "Übersetzungen (DE/NL/EN)"
  ok "Übersetzungen vollständig (DE/NL/EN)"
else
  echo "(node fehlt – JS-Prüfungen übersprungen)"
fi

for s in scripts/*.sh deploy/*.sh backend/app/templates/install-agent.sh; do bash -n "$s" 2>/dev/null || sh -n "$s" || fail "Shell-Syntax: $s"; done
ok "Shell-Syntax"
command -v shellcheck >/dev/null 2>&1 && shellcheck -S warning scripts/*.sh deploy/*.sh && ok "shellcheck"
echo "Alles in Ordnung."
