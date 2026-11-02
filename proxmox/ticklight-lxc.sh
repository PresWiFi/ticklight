#!/usr/bin/env bash
# Ticklight – Proxmox VE helper script. Run on the Proxmox host as root.
#
# Create a new LXC:
#   bash ticklight-lxc.sh
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/PresWiFi/ticklight/main/proxmox/ticklight-lxc.sh)"
#
# Update an existing container (keeps config + data):
#   bash ticklight-lxc.sh --update 123
#
# Every setting can be preset through environment variables to skip the prompts, e.g.
#   CTID=150 BRIDGE=vmbr1 IP=192.168.1.50/24 GW=192.168.1.1 TELEGRAM_BOT_TOKEN=... bash ticklight-lxc.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/PresWiFi/ticklight.git}"
BRANCH="${BRANCH:-main}"

info() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
ok()   { printf '\e[1;32m✔\e[0m %s\n' "$*"; }
die()  { printf '\e[1;31m✘ %s\e[0m\n' "$*" >&2; exit 1; }

ask() { # ask VAR "Prompt" default
  local var=$1 prompt=$2 def=${3:-} cur=${!1:-}
  if [[ -n "$cur" ]]; then return; fi
  if [[ -t 0 ]]; then
    read -rp "$prompt [${def}]: " cur
  fi
  printf -v "$var" '%s' "${cur:-$def}"
}

command -v pct >/dev/null || die "This script must run on a Proxmox VE host."
[[ $EUID -eq 0 ]] || die "Run as root."

# Where is the app source? Prefer a local checkout next to this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
LOCAL_SRC=""
if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/../app/main.py" ]]; then
  LOCAL_SRC="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
RAW_BASE="${REPO_URL%.git}"
RAW_BASE="${RAW_BASE/github.com/raw.githubusercontent.com}/$BRANCH"

push_and_install() { # ctid
  local ctid=$1 tmp
  tmp=$(mktemp -d)
  if [[ -n "$LOCAL_SRC" ]]; then
    info "Packing local source from $LOCAL_SRC"
    tar -czf "$tmp/src.tar.gz" --exclude=__pycache__ -C "$LOCAL_SRC" app proxmox requirements.txt .env.example
    pct push "$ctid" "$tmp/src.tar.gz" /root/ticklight.tar.gz
    cp "$LOCAL_SRC/proxmox/install.sh" "$tmp/install.sh"
    INSTALL_ARGS=(--src /root/ticklight.tar.gz)
  else
    curl -fsSL "$RAW_BASE/proxmox/install.sh" -o "$tmp/install.sh" || die "Could not download install.sh"
    INSTALL_ARGS=()
  fi
  [[ -f "$tmp/seed.env" ]] || touch "$tmp/seed.env"
  [[ -s "${SEED:-}" ]] && cp "$SEED" "$tmp/seed.env"
  pct push "$ctid" "$tmp/seed.env" /root/ticklight.seed.env --perms 600
  pct push "$ctid" "$tmp/install.sh" /root/ticklight-install.sh --perms 755
  rm -rf "$tmp"
  info "Running installer inside CT $ctid"
  pct exec "$ctid" -- env REPO_URL="$REPO_URL" BRANCH="$BRANCH" \
    bash /root/ticklight-install.sh "${INSTALL_ARGS[@]}"
  pct exec "$ctid" -- rm -f /root/ticklight-install.sh
}

ct_ip() {
  pct exec "$1" -- sh -c "hostname -I 2>/dev/null | awk '{print \$1}'" 2>/dev/null || true
}

# ---------------- update mode ----------------
if [[ "${1:-}" == "--update" ]]; then
  CTID="${2:-}"
  [[ -n "$CTID" ]] || die "Usage: $0 --update <ctid>"
  pct status "$CTID" | grep -q running || pct start "$CTID"
  SEED=""
  push_and_install "$CTID"
  ok "Updated. Open http://$(ct_ip "$CTID"):8080"
  exit 0
fi

# ---------------- gather settings ----------------
echo
echo "  ✅  Ticklight – Proxmox LXC installer"
echo

NEXTID=$(pvesh get /cluster/nextid)
HOST_TZ=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)
DEF_STORAGE=$(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}' | grep -m1 -E 'local-lvm|local-zfs' \
  || pvesm status -content rootdir | awk 'NR>1 && $3=="active" {print $1; exit}')
DEF_TSTORAGE=$(pvesm status -content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}' | grep -m1 -x local \
  || pvesm status -content vztmpl | awk 'NR>1 && $3=="active" {print $1; exit}')

ask CTID             "Container ID"                     "$NEXTID"
ask CT_HOSTNAME      "Hostname"                         "ticklight"
ask STORAGE          "Disk storage"                     "$DEF_STORAGE"
ask TEMPLATE_STORAGE "Template storage"                 "$DEF_TSTORAGE"
ask DISK_GB          "Disk size (GB)"                   "2"
ask RAM_MB           "Memory (MB)"                      "512"
ask CORES            "CPU cores"                        "1"
ask BRIDGE           "Network bridge"                   "vmbr0"
ask IP               "IPv4 (dhcp or CIDR, e.g. 192.168.1.50/24)" "dhcp"
if [[ "$IP" != "dhcp" ]]; then ask GW "Gateway" ""; fi
ask VLAN             "VLAN tag (blank for none)"        ""
ask APP_TZ           "Timezone"                         "$HOST_TZ"
echo
echo "Telegram is optional: leave blank to skip (you can add it later in /etc/ticklight/ticklight.env)."
ask TELEGRAM_BOT_TOKEN     "Telegram bot token from @BotFather" ""
if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
  ask TELEGRAM_ALLOWED_USERS "Your Telegram user id (blank = find it later by sending /id to the bot)" ""
  ask SUMMARY_TIME           "Morning summary time (HH:MM, blank to disable)" "08:00"
fi

pct status "$CTID" &>/dev/null && die "CT $CTID already exists."
[[ -n "$STORAGE" ]] || die "No storage for container disks found."

# ---------------- template ----------------
info "Finding Debian template"
pveam update >/dev/null || true
TEMPLATE=""
for ver in 13 12; do
  TEMPLATE=$(pveam available --section system | awk -v v="debian-$ver-standard" '$2 ~ v {print $2}' | sort -V | tail -1)
  [[ -n "$TEMPLATE" ]] && break
done
[[ -n "$TEMPLATE" ]] || die "No Debian template available from pveam."
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  info "Downloading $TEMPLATE"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE" >/dev/null
fi
ok "Template $TEMPLATE"

# ---------------- create container ----------------
NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[[ "$IP" != "dhcp" && -n "${GW:-}" ]] && NET+=",gw=$GW"
[[ -n "${VLAN:-}" ]] && NET+=",tag=$VLAN"

info "Creating CT $CTID"
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$CT_HOSTNAME" \
  --cores "$CORES" --memory "$RAM_MB" --swap 256 \
  --rootfs "$STORAGE:$DISK_GB" \
  --net0 "$NET" \
  --unprivileged 1 --features nesting=1 \
  --onboot 1 --tags ticklight \
  --description "Ticklight – http://<ip>:8080" >/dev/null
pct start "$CTID"
ok "CT $CTID started"

info "Waiting for network"
for _ in $(seq 1 30); do
  pct exec "$CTID" -- getent hosts deb.debian.org &>/dev/null && break
  sleep 2
done
pct exec "$CTID" -- getent hosts deb.debian.org &>/dev/null || die "Container has no network/DNS."

# ---------------- install ----------------
SEED=$(mktemp)
{
  echo "TZ=$APP_TZ"
  echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}"
  echo "TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS:-}"
  echo "SUMMARY_TIME=${SUMMARY_TIME:-08:00}"
} > "$SEED"
push_and_install "$CTID"
rm -f "$SEED"

ADDR=$(ct_ip "$CTID")
pct set "$CTID" --description "Ticklight – http://${ADDR}:8080" >/dev/null || true
echo
ok "Done!  Open  http://${ADDR}:8080  on your desk monitor."
echo "   Config:  pct enter $CTID  →  nano /etc/ticklight/ticklight.env  →  systemctl restart ticklight"
echo "   Logs:    pct exec $CTID -- journalctl -u ticklight -f"
echo "   Update:  re-run this script with: --update $CTID"
