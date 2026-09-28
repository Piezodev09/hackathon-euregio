#!/usr/bin/env bash
# Creates a Debian 12 LXC container on a Proxmox VE host and installs the complete
# Smart Bike Station platform in it (in the style of the Proxmox helper scripts).
#
# Run on the Proxmox host as root, from a checkout (or copy) of the repository:
#   bash deploy/proxmox/create-lxc.sh                       # DHCP, next free CT ID
#   bash deploy/proxmox/create-lxc.sh --ip 192.168.1.50/24 --gw 192.168.1.1
#   bash deploy/proxmox/create-lxc.sh --dry-run             # only print what would happen
#
# The repository is copied into the container with "pct push" (works with a private repository
# and without git in the container). The container needs internet access for apt and pip,
# unless you pass --install-args "--wheelhouse /path" with pre-downloaded wheels.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
CTID=""
CT_HOSTNAME="bikestation"
IP_CFG="dhcp"
GATEWAY=""
BRIDGE="vmbr0"
VLAN=""
STORAGE=""
TEMPLATE_STORAGE="local"
CORES=2
MEMORY=1024
SWAP=512
DISK=8
SSH_KEY=""
INSTALL_ARGS=""
DRY_RUN=0

usage() {
  cat <<USAGE
Usage: bash $0 [options]
  --ctid ID              container ID (default: next free ID)
  --hostname NAME        host name (default: $CT_HOSTNAME -> https://$CT_HOSTNAME.local)
  --ip dhcp|CIDR         e.g. 192.168.1.50/24 (default: dhcp - better use a DHCP reservation)
  --gw ADDRESS           gateway for a static IP
  --bridge NAME          network bridge (default: $BRIDGE)
  --vlan TAG             VLAN tag
  --storage NAME         storage for the root disk (default: local-lvm, else local)
  --template-storage N   storage for the Debian template (default: $TEMPLATE_STORAGE)
  --cores N              vCPUs (default: $CORES)
  --memory MB            RAM (default: $MEMORY)
  --disk GB              root disk (default: $DISK)
  --ssh-key FILE         public SSH key for root in the container
  --install-args "..."   extra options for deploy/install-server.sh (e.g. "--no-ml")
  --dry-run              print the commands instead of running them
USAGE
}

need_arg() { [ $# -ge 2 ] || { echo "Option $1 needs a value" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    --ctid) need_arg "$@"; CTID="$2"; shift 2 ;;
    --hostname) need_arg "$@"; CT_HOSTNAME="$2"; shift 2 ;;
    --ip) need_arg "$@"; IP_CFG="$2"; shift 2 ;;
    --gw) need_arg "$@"; GATEWAY="$2"; shift 2 ;;
    --bridge) need_arg "$@"; BRIDGE="$2"; shift 2 ;;
    --vlan) need_arg "$@"; VLAN="$2"; shift 2 ;;
    --storage) need_arg "$@"; STORAGE="$2"; shift 2 ;;
    --template-storage) need_arg "$@"; TEMPLATE_STORAGE="$2"; shift 2 ;;
    --cores) need_arg "$@"; CORES="$2"; shift 2 ;;
    --memory) need_arg "$@"; MEMORY="$2"; shift 2 ;;
    --disk) need_arg "$@"; DISK="$2"; shift 2 ;;
    --ssh-key) need_arg "$@"; SSH_KEY="$2"; shift 2 ;;
    --install-args) need_arg "$@"; INSTALL_ARGS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

say() { if [ -t 1 ]; then printf '\033[1;32m==> %s\033[0m\n' "$*"; else printf '==> %s\n' "$*"; fi; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '[dry-run]'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}
is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

# ---------------------------------------------------------------------- checks
[ -f "$SRC/deploy/install-server.sh" ] || die "Repository not found at $SRC"
for v in CORES MEMORY DISK; do is_int "${!v}" || die "--${v,,} must be a number"; done
[ -z "$VLAN" ] || is_int "$VLAN" || die "--vlan must be a number"
[[ "$CT_HOSTNAME" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$ ]] || die "invalid --hostname"
if [ "$IP_CFG" != "dhcp" ]; then
  [[ "$IP_CFG" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || die "--ip must be dhcp or an address in CIDR form (192.168.1.50/24)"
  [ -n "$GATEWAY" ] || die "--gw is required with a static --ip"
fi
[ -z "$SSH_KEY" ] || [ -r "$SSH_KEY" ] || die "SSH key not readable: $SSH_KEY"

if command -v pveversion >/dev/null 2>&1; then
  say "Proxmox VE: $(pveversion | head -n1)"
  [ "$(id -u)" -eq 0 ] || die "Please run as root on the Proxmox host."
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "(pveversion not found - not a Proxmox host; continuing because of --dry-run)"
else
  die "This script must run on a Proxmox VE host (pveversion not found)."
fi

if [ -z "$CTID" ]; then
  if [ "$DRY_RUN" -eq 1 ] && ! command -v pvesh >/dev/null 2>&1; then CTID=100; else CTID="$(pvesh get /cluster/nextid)"; fi
fi
is_int "$CTID" || die "--ctid must be a number"
if [ "$DRY_RUN" -eq 0 ] && pct status "$CTID" >/dev/null 2>&1; then die "Container $CTID already exists."; fi

if [ -z "$STORAGE" ]; then
  if [ "$DRY_RUN" -eq 1 ] && ! command -v pvesm >/dev/null 2>&1; then STORAGE="local-lvm"
  elif pvesm status 2>/dev/null | awk 'NR > 1 {print $1}' | grep -qx local-lvm; then STORAGE="local-lvm"
  else STORAGE="local"; fi
fi

# ---------------------------------------------------------------------- template
say "Looking for the Debian 12 template"
TEMPLATE=""
if [ "$DRY_RUN" -eq 1 ] && ! command -v pveam >/dev/null 2>&1; then
  TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
else
  run pveam update >/dev/null || true
  TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | sed -n 's|.*vztmpl/||p' \
    | grep -E '^debian-12-standard_.*\.tar\.(zst|gz|xz)$' | sort -V | tail -n1 || true)"
  if [ -z "$TEMPLATE" ]; then
    TEMPLATE="$(pveam available --section system | awk '{print $2}' | grep -E '^debian-12-standard_' | sort -V | tail -n1)"
    [ -n "$TEMPLATE" ] || die "No Debian 12 template available (pveam available --section system)."
    say "Downloading $TEMPLATE"
    run pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
  fi
fi
say "Template: $TEMPLATE"

# ---------------------------------------------------------------------- container
NET="name=eth0,bridge=$BRIDGE,ip=$IP_CFG"
[ -z "$GATEWAY" ] || NET="$NET,gw=$GATEWAY"
[ -z "$VLAN" ] || NET="$NET,tag=$VLAN"
CREATE=(pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE"
  --hostname "$CT_HOSTNAME" --cores "$CORES" --memory "$MEMORY" --swap "$SWAP"
  --rootfs "$STORAGE:$DISK" --net0 "$NET" --ostype debian
  --unprivileged 1 --features nesting=1 --onboot 1
  --description "Smart Bike Station platform - https://$CT_HOSTNAME.local")
[ -z "$SSH_KEY" ] || CREATE+=(--ssh-public-keys "$SSH_KEY")
say "Creating container $CTID ($CORES vCPU, $MEMORY MB RAM, $DISK GB, $NET)"
run "${CREATE[@]}"
run pct start "$CTID"

say "Waiting for the network in the container"
if [ "$DRY_RUN" -eq 0 ]; then
  CT_IP=""
  for _ in $(seq 1 60); do
    CT_IP="$(pct exec "$CTID" -- sh -c "ip -4 -o addr show scope global | awk '{split(\$4,a,\"/\"); print a[1]; exit}'" 2>/dev/null || true)"
    [ -n "$CT_IP" ] && break
    sleep 2
  done
  [ -n "$CT_IP" ] || die "The container got no IPv4 address (check bridge/DHCP)."
  say "Container address: $CT_IP"
fi

# ---------------------------------------------------------------------- copy + install
BUNDLE="$(mktemp /tmp/bike-station-XXXXXX.tar.gz)"
trap 'rm -f "$BUNDLE"' EXIT
say "Packing the repository"
run tar czf "$BUNDLE" -C "$SRC" --exclude=.git --exclude='__pycache__' --exclude='*.pyc' --exclude='*.db' \
  --exclude='*.db-*' --exclude='*.datakey' --exclude='.setup-token' --exclude='.dev-demo.env' --exclude='agent/state' \
  --exclude='node_modules' --exclude='.venv' --exclude='venv' .
run pct push "$CTID" "$BUNDLE" /root/bike-station-src.tar.gz
say "Installing the platform inside the container (this takes a few minutes)"
# shellcheck disable=SC2086  # INSTALL_ARGS is intentionally split into options
run pct exec "$CTID" -- bash -c "set -e; rm -rf /root/bike-station-src; mkdir -p /root/bike-station-src; \
  tar xzf /root/bike-station-src.tar.gz -C /root/bike-station-src; rm /root/bike-station-src.tar.gz; \
  bash /root/bike-station-src/deploy/install-server.sh $INSTALL_ARGS"

[ "$DRY_RUN" -eq 0 ] || { echo; echo "Dry run finished - nothing was changed."; exit 0; }
cat <<DONE

Container $CTID is ready. Useful commands on the Proxmox host:
  pct enter $CTID                                  # shell in the container
  pct snapshot $CTID before-demo                   # snapshot before the demo (LVM-thin/ZFS storage)
  vzdump $CTID --mode snapshot --compress zstd     # full backup
  pct exec $CTID -- bike-station --help            # platform CLI
DONE
