#!/bin/bash
# Restart pi-api.service. Needed after server code, pyproject.toml, or
# per-install dependency changes; pida.yaml edits are picked up per request.
sudo systemctl restart pi-api.service && systemctl is-active pi-api.service
