#!/bin/bash
# Restart pi-api.service. Needed after server code, pyproject.toml, or
# per-install dependency changes; pida.yaml action edits are picked up
# per request, but `service:` section changes need a restart.
sudo systemctl restart pi-api.service && systemctl is-active pi-api.service
