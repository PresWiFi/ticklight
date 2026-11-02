#!/usr/bin/env bash
# Installs or updates Ticklight on Debian/Ubuntu as a systemd service.
# Runs inside the LXC created by ticklight-lxc.sh, but works on any Debian box or VM.
#
#   install.sh --src /root/ticklight.tar.gz   # install from a local tarball
#   install.sh                                 # install/update from git (REPO_URL, BRANCH)
#
# Config lives in /etc/ticklight/ticklight.env and data in /var/lib/ticklight.
# Both are preserved on updates.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/PresWiFi/ticklight.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/ticklight
DATA_DIR=/var/lib/ticklight
CONF_DIR=/etc/ticklight
ENV_FILE=$CONF_DIR/ticklight.env
SEED_FILE="${SEED_FILE:-/root/ticklight.seed.env}"
SRC=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src) SRC="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

msg() { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

msg "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv ca-certificates curl git >/dev/null

id ticklight &>/dev/null || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin ticklight
mkdir -p "$APP_DIR" "$DATA_DIR" "$CONF_DIR"
chown ticklight:ticklight "$DATA_DIR"

msg "Fetching application code"
if [[ -n "$SRC" ]]; then
  rm -rf "$APP_DIR/app"
  tar -xzf "$SRC" -C "$APP_DIR"
  rm -f "$SRC"
elif [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH"
  git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  rm -rf "$APP_DIR/app"
  git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR.tmp"
  cp -a "$APP_DIR.tmp/." "$APP_DIR/"
  rm -rf "$APP_DIR.tmp"
fi

msg "Installing Python dependencies"
[[ -x "$APP_DIR/venv/bin/python" ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  msg "Writing config to $ENV_FILE"
  if [[ -f "$APP_DIR/.env.example" ]]; then
    cp "$APP_DIR/.env.example" "$ENV_FILE"
  else
    : > "$ENV_FILE"
  fi
  # Apply values chosen during setup
  if [[ -f "$SEED_FILE" ]]; then
    while IFS='=' read -r key value; do
      [[ -z "$key" || "$key" == \#* ]] && continue
      if grep -q "^${key}=" "$ENV_FILE"; then
        python3 - "$ENV_FILE" "$key" "$value" <<'PY'
import sys
path, key, value = sys.argv[1:]
lines = open(path).read().splitlines()
lines = [f"{key}={value}" if l.startswith(key + "=") else l for l in lines]
open(path, "w").write("\n".join(lines) + "\n")
PY
      else
        echo "${key}=${value}" >> "$ENV_FILE"
      fi
    done < "$SEED_FILE"
  fi
fi
rm -f "$SEED_FILE"
grep -q '^DATA_DIR=' "$ENV_FILE" || echo "DATA_DIR=$DATA_DIR" >> "$ENV_FILE"
chown root:ticklight "$ENV_FILE"
chmod 640 "$ENV_FILE"

msg "Installing systemd service"
cat > /etc/systemd/system/ticklight.service <<EOF
[Unit]
Description=Ticklight
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ticklight
Group=ticklight
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Helper so updating from inside the container is one command
cat > /usr/local/bin/ticklight-update <<EOF
#!/usr/bin/env bash
# Pulls the latest code from git and restarts. (Tarball installs: re-run
# ticklight-lxc.sh --update <ctid> on the Proxmox host instead.)
REPO_URL="$REPO_URL" BRANCH="$BRANCH" exec bash $APP_DIR/proxmox/install.sh "\$@"
EOF
chmod +x /usr/local/bin/ticklight-update

systemctl daemon-reload
systemctl enable -q ticklight
systemctl restart ticklight

PORT=$(grep -E '^PORT=' "$ENV_FILE" | cut -d= -f2); PORT=${PORT:-8080}
for _ in $(seq 1 20); do
  curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null && break
  sleep 1
done
if curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null; then
  msg "Ticklight is running on port $PORT"
else
  echo "Service did not come up; check: journalctl -u ticklight -n 50" >&2
  exit 1
fi
