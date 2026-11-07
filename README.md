<!-- TITLE: Pi Action API -->
<!-- KEYWORDS: raspberry pi, home automation, api, python, yaml -->
<!-- LANGUAGES: Python, Bash, YAML -->
<!-- TECHNOLOGY: Flask, Gunicorn, systemd -->

<!-- LOGO -->
![PiDA](media/pida-logo.png)

[About](#about) - [Features](#features) - [Install](#install) - [Usage](#usage) - [Configuration](#configuration) - [Project Layout](#project-layout) - [License](#license)

## Status

**Prototype**

## About

Pi Action API is a lightweight home-automation controller for a Raspberry Pi. It turns a YAML-defined action tree into HTTP endpoints, so you can trigger devices, scripts, and grouped workflows from a mobile app, browser, CLI, or other automation tooling.

The project is designed around a simple principle: keep your device mapping and runtime commands in a per-install config directory under `~/.pida`, while the packaged program remains generic and upgrade-friendly.

This repo ships with:

- a Python web service that exposes actions over HTTP
- a local command-line helper to list or trigger actions
- a generic demo config and formal JSON Schema for per-install pipelines
- a library directory for tool-specific device scripts
- a systemd service so it starts automatically on boot

## Why

Most home automation setups either hardcode device logic into code or require a full platform and database stack. Pi Action API keeps things intentionally small and local.

Instead of writing a custom app for each device, define an action such as
`demo/hello` in YAML and map it to a shell command or helper script. That
makes the system easy to reuse and tailor to your own hardware.

## Features

- Grouped action tree with nested endpoints like `group/sub/action`
- Optional action dependencies via `needs` for multi-device workflows
- Typed parameters and outputs for structured requests
- Local per-install config under `~/.pida` so personal data stays out of the repo
- Device scripts in `library/` with their own virtual environment support
- Boot-persistent service management with `systemd`
- CLI access for ad hoc execution and testing

## Install

Two supported installation paths are provided.

### Option 1: Debian package (recommended)

This installs the packaged program under `/opt/pida` and keeps your runtime data in `~/.pida`.

```bash
scp pida_1.0.0_all.deb pi@<pi>:~
ssh pi@<pi> sudo apt install ./pida_1.0.0_all.deb
```

The package will:

- seed `~/.pida/pida.yaml` if it does not already exist
- create `~/.pida/icons/` and `~/.pida/library/`
- bootstrap a private Python virtual environment
- install and start `pi-api.service`
- verify the API health endpoint

### Option 2: Git clone

```bash
git clone <repo-url> ~/pida
cd ~/pida && ./scripts/install.sh
```

The git-clone installer works from any clone location and configures the same service layout for the current user.

### Upgrade and uninstall

```bash
sudo apt install ./pida_<newer>_all.deb
# or
cd ~/pida && git pull && sudo systemctl restart pi-api
```

```bash
sudo apt remove pida
sudo apt purge pida
```

## Usage

### Health check

```bash
curl http://<pi>:9999/api/health
```

### List available endpoints

```bash
~/.pida/.venv/bin/python scripts/cli.py list
```

### Trigger an action locally

```bash
~/.pida/.venv/bin/python scripts/cli.py demo/hello
~/.pida/.venv/bin/python scripts/cli.py group/action
```

### Trigger through the HTTP API

The service binds to port `9999` (configurable via `~/.pida/env`) and exposes actions like:

```bash
curl -X POST http://<pi>:9999/api/group/action
curl -X POST http://<pi>:9999/api/group/sub/param \
  -H 'Content-Type: application/json' \
  -d '{"value": 55}'
```

The exact shapes depend on your `pida.yaml` definitions and any parameter contracts you declare there.

## Configuration

The main config file is `~/.pida/pida.yaml`. Fresh installs seed an empty
mapping; the formal JSON Schema is `src/pida.schema.json`.

Typical configuration looks like this:

```yaml
demo:
- action: hello
  run: echo "Hello from Pi Action API"
```

Important details:

- `pida.yaml` is personal data and should not be stored in the source repo
- `icons/` mirrors action endpoints for UI icons
- `library/` contains device-specific helper scripts
- environment variables can be inherited by groups and overridden per action
- `needs` lets one action trigger related actions in a best-effort chain

## Project Layout

```text
repo (~/pida)                  per-install data (~/.pida, git-ignored)
├── README.md                 # this file
├── .agent/                   # project, agent, and installation docs
├── package/                  # Debian builder, metadata, and service files
├── pyproject.toml            # Python project metadata
├── scripts/                  # CLI, git installer, and restart helper
├── src/                      # flat pipeline.py, server.py, and JSON Schema
└── tests/                    # schema and config validation tests

pida.yaml                  # action definitions (your devices)
icons/                         # action icons and UI assets
library/                       # device helper scripts and drivers
~/.pida/requirements.txt       # optional private script dependencies only
logs/                          # runtime logs
.venv/                         # Python virtual environment
```

## Service Management

```bash
systemctl status pi-api
journalctl -u pi-api -f
```

The service runs Gunicorn in the background and binds to port `9999` (configurable via `~/.pida/env`; see [.agent/NOTES.md](.agent/NOTES.md) for service details).

## Example Devices and Flows

The generic config demonstrates a simple action without assuming a device,
network, or home-automation platform. Add your own groups and commands to
`~/.pida/pida.yaml`.

## License

This project is distributed under the MIT license.

See the license text in the repository or the packaging metadata for full details.
