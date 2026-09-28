#!/usr/bin/env bash
# Self-test for create-lxc.sh without a Proxmox host: fake pct/pveam/pvesh/pvesm/pveversion commands
# record every call; the test checks the container options and the pushed repository bundle.
#   bash deploy/proxmox/selftest.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FAKE="$(mktemp -d)"
trap 'rm -rf "$FAKE"' EXIT
mk() { printf '#!/bin/sh\n%s\n' "$2" > "$FAKE/$1"; chmod +x "$FAKE/$1"; }
mk pveversion 'echo "pve-manager/8.2.4/faaa0 (running kernel: 6.8.12-1-pve)"'
mk pvesh 'echo 105'
mk pvesm "printf 'Name Type Status\nlocal dir active\nlocal-lvm lvmthin active\n'"
mk pveam "echo \"pveam \$*\" >> $FAKE/log
case \"\$1\" in
  list) echo NAME ;;
  available) printf 'system debian-11-standard_11.7-1_amd64.tar.zst\nsystem debian-12-standard_12.2-1_amd64.tar.zst\nsystem debian-12-standard_12.7-1_amd64.tar.zst\n' ;;
esac"
mk pct "echo \"pct \$*\" >> $FAKE/log
case \"\$1\" in
  status) exit 2 ;;
  push) cp \"\$3\" $FAKE/pushed.tar.gz ;;
  exec) case \"\$*\" in *'ip -4'*) echo 192.168.1.77 ;; esac ;;
esac"
PATH="$FAKE:$PATH" bash "$HERE/create-lxc.sh" --ip 192.168.1.50/24 --gw 192.168.1.1 --install-args "--no-ml" >/dev/null
fail() { echo "FAIL: $*" >&2; cat "$FAKE/log" >&2; exit 1; }
grep -q 'pveam download local debian-12-standard_12.7-1_amd64.tar.zst' "$FAKE/log" || fail "newest Debian 12 template not downloaded"
grep -q 'pct create 105 .*--unprivileged 1 --features nesting=1 --onboot 1' "$FAKE/log" || fail "container options"
grep -q 'ip=192.168.1.50/24,gw=192.168.1.1' "$FAKE/log" || fail "static IP"
grep -q 'install-server.sh --no-ml' "$FAKE/log" || fail "install call"
FILES="$(tar tzf "$FAKE/pushed.tar.gz")"
grep -qx './deploy/install-server.sh' <<<"$FILES" || fail "bundle misses install-server.sh"
if grep -qE '(^|/)\.git/|\.db$|\.datakey$' <<<"$FILES"; then fail "bundle contains git data or databases"; fi
PATH="$FAKE:$PATH" bash "$HERE/create-lxc.sh" --ip 10.0.0.5 >/dev/null 2>&1 && fail "invalid --ip accepted"
echo "create-lxc.sh self-test passed"
