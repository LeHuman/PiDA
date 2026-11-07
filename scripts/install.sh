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
# Service settings (port, bind, workers, threads) live in the pida.yaml
# `service:` section; the old env file is migrated on first upgrade.
seed_service() {
    port=9999 bind=0.0.0.0 workers=1 threads=5
    if [ -f "$DATA/env" ]; then
        # shellcheck disable=SC1090
        . "$DATA/env" || true
        port=${PIDA_PORT:-$port} bind=${PIDA_BIND:-$bind}
        workers=${PIDA_WORKERS:-$workers} threads=${PIDA_THREADS:-$threads}
    fi
    # A fresh (empty) file starts with the quick-reference header; an
    # existing user file is never rewritten, only appended to.
    if [ ! -s "$1" ]; then
        cat >> "$1" <<'HEADER'
# Pi Action Pipeline -- per-install action config (personal data; never commit)
#
# Endpoint = group key (slashes OK) + action name:
#   kitchen/fan: - action: on  =>  POST /api/kitchen/fan/on
# run:     bash command, cwd = ~/.pida (relative paths OK)
# env:     VAR -> value; inherited from groups, overridden per action
# needs:   endpoint list fanned out in parallel, best effort
# timeout: per-action seconds (default 60)
# params:  typed contract (src/pida.schema.json); inputs: names accepted in
#          the POST body as a partial delta (unset input = no change, provided
#          inputs injected as INPUT_<NAME> env vars); outputs: names read back
#          from $PIPELINE_OUTPUT key=value lines
# service: server bind/port/workers/threads (systemctl restart pi-api)

HEADER
    fi
    {
        echo "service:"
        echo "  port: $port"
        echo "  bind: $bind"
        echo "  workers: $workers"
        echo "  threads: $threads"
    } >> "$1"
    echo "Seeded service settings into $1."
}
if [ ! -e "$DATA/pida.yaml" ]; then
    if [ -e "$DATA/commands.yaml" ]; then
        mv "$DATA/commands.yaml" "$DATA/pida.yaml"
        echo "Migrated commands.yaml -> pida.yaml."
    fi
fi
if [ ! -e "$DATA/pida.yaml" ]; then
    : > "$DATA/pida.yaml"
    seed_service "$DATA/pida.yaml"
    echo "Seeded $DATA/pida.yaml (service defaults; add action groups to configure devices)."
elif ! grep -qE '^[[:space:]]*service:' "$DATA/pida.yaml"; then
    seed_service "$DATA/pida.yaml"
    echo "Migrated service settings from env file into $DATA/pida.yaml."
fi
rm -f "$DATA/env"
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
# Service settings (port, bind, workers, threads) live in the `service:`
# section of ${HOME}/.pida/pida.yaml; scripts/serve.py reads them and
# execs gunicorn. Edit pida.yaml, then: systemctl restart pi-api
ExecStart=${HOME}/.pida/.venv/bin/python $(pwd)/scripts/serve.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now pi-api.service

echo; echo "Done. Health:"
PIDA_PORT=$(HOME="$HOME" PIDA_SRC="$(pwd)/src" "$DATA/.venv/bin/python" -c \
    'import os, sys; sys.path.insert(0, os.environ["PIDA_SRC"]); import pipeline; print(pipeline.service_settings()["port"])' \
    2>/dev/null) || PIDA_PORT=9999
curl -fsS -m 5 "http://localhost:$PIDA_PORT/api/health" && echo
