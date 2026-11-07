#!/bin/bash
# Git-layout installer: run from a clone of the repo (any location, e.g.
# ~/pida). The .deb path does NOT use this script.
#   git clone <repo-url> ~/pida && cd ~/pida && ./scripts/install.sh
set -e
cd "$(dirname "$0")/.."

# Per-install data lives in ~/.pida regardless of where the clone lives.
DATA="$HOME/.pida"

# seed per-install data, never overwrite
install -d -m 755 "$DATA/logs" "$DATA/icons" "$DATA/library"
# Service config (port, bind, workers, threads) — read by the systemd unit.
if [ ! -e "$DATA/env" ]; then
    cat > "$DATA/env" <<'ENVDATA'
# Pi Action API service config (read by pi-api.service).
# Edit, then: systemctl restart pi-api
PIDA_PORT=9999
PIDA_BIND=0.0.0.0
PIDA_WORKERS=1
PIDA_THREADS=5
ENVDATA
    echo "Seeded $DATA/env (service config: port, bind, workers, threads)."
fi
if [ ! -e "$DATA/pida.yaml" ]; then
    if [ -e "$DATA/commands.yaml" ]; then
        mv "$DATA/commands.yaml" "$DATA/pida.yaml"
        echo "Migrated commands.yaml -> pida.yaml."
    else
        printf '{}\n' > "$DATA/pida.yaml"
        chmod 644 "$DATA/pida.yaml"
        echo "Seeded empty $DATA/pida.yaml; add action groups to configure devices."
    fi
fi
if [ ! -e "$DATA/requirements.txt" ]; then
    if [ -e "$DATA/requirements.local.txt" ]; then
        mv "$DATA/requirements.local.txt" "$DATA/requirements.txt"
        echo "Migrated requirements.local.txt -> requirements.txt."
    else
        : > "$DATA/requirements.txt"
        echo "Seeded empty additive $DATA/requirements.txt (personal deps: one per line)."
    fi
fi

# venv (sync_deps() also self-installs on hash change at startup)
if [ ! -x "$DATA/.venv/bin/pip" ]; then
    python3 -m venv "$DATA/.venv"
fi
"$DATA/.venv/bin/pip" install -q -e . -r "$DATA/requirements.txt"

# systemd unit (matches the packaged one), adapted to the running user
USER_NAME=$(id -un)
sudo tee /etc/systemd/system/pi-api.service > /dev/null <<UNIT
[Unit]
Description=Pi Action API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=$(pwd)
Environment=PYTHONPATH=$(pwd)/src
# Service config (port, bind, workers, threads) is user-owned: ~/.pida/env
EnvironmentFile=${HOME}/.pida/env
ExecStart=${HOME}/.pida/.venv/bin/gunicorn -w \${PIDA_WORKERS} --threads \${PIDA_THREADS} -b \${PIDA_BIND}:\${PIDA_PORT} server:app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now pi-api.service

echo; echo "Done. Health:"
PIDA_PORT=9999
[ -f "$DATA/env" ] && . "$DATA/env"
curl -fsS -m 5 "http://localhost:$PIDA_PORT/api/health" && echo
