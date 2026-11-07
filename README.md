<!-- PROJECT: PiDA -->
<!-- TITLE: PiDA Server -->
<!-- KEYWORDS: Raspberry Pi, Home Automation, Server, API, Python, YAML -->
<!-- LANGUAGES: Python, Bash, YAML -->
<!-- TECHNOLOGY: Flask, Gunicorn, systemd -->

<!-- LOGO -->
![PiDA](media/pida-logo.png)

[About](#about) | [Key Features](#key-features) | [Usage](#usage) | [Related](#related) | [License](#license) | [Contribution](#contribution)

## Status

<!-- STATUS -->
**Prototype**

## About
<!-- DESCRIPTION START -->
PiDA is a lightweight home-automation server for a Raspberry Pi. It turns a YAML-defined action tree into HTTP endpoints, so devices, scripts, and grouped workflows can be triggered from a mobile app, browser, CLI, or other automation tooling.
<!-- DESCRIPTION END -->

### Why
<!-- WHY START -->
I wanted a simple and lightweight solution to various 'automation' actions around my place. I didn't want a full platform stack or layers of UI interfaces. I wanted a pipeline like interface that was basically a passthrough for whatever script I ran. I already functionally had something like this but it was directly on my phone through termux.
<!-- WHY END -->

## Key Features

- Grouped action tree with nested endpoints like `group/sub/action`.
- Typed parameters and outputs, validated against a formal JSON Schema.
- Optional action dependencies via `needs` for multi-device workflows.
- Per-install configuration under `~/.pida`, kept out of the repository.
- Device helper scripts in `library/` with virtual environment support.
- CLI access for ad hoc execution and testing.
- `systemd` service management with boot persistence.

## Usage

### Requirements

- A Raspberry Pi or other Linux host.
- Python 3.9 or newer, with `venv`.
- `systemd` for the managed service.

### Building

Build the Debian package on the Pi:

```sh
./package/build-deb.sh [version]
```

The package is written to `pida_<version>_all.deb`.

### Installing

Install the package:

```sh
sudo apt install ./pida_1.0.0_all.deb
```

The package installs the program under `/opt/pida`, seeds `~/.pida/pida.yaml`, creates `~/.pida/icons/` and `~/.pida/library/`, bootstraps a private virtual environment, and installs and starts `pi-api.service`.

To install from a clone instead, run the installer for the current user:

```sh
git clone <repo-url> ~/pida
cd ~/pida && ./scripts/install.sh
```

### Running

```sh
systemctl status pi-api
journalctl -u pi-api -f
```

The service binds to port `9999`, configurable via `~/.pida/env`.

```sh
curl http://<pi>:9999/api/health
```

Trigger actions locally with the CLI, or over HTTP:

```sh
~/.pida/.venv/bin/python scripts/cli.py list
~/.pida/.venv/bin/python scripts/cli.py demo/hello
curl -X POST http://<pi>:9999/api/group/action
```

### Configuration

The main config file is `~/.pida/pida.yaml`. Fresh installs seed an empty mapping; the formal JSON Schema is `src/pida.schema.json`.

```yaml
demo:
- action: hello
  run: echo "Hello from Pi Action API"
```

- `pida.yaml` is personal data and is not stored in the repository.
- `icons/` mirrors action endpoints for UI icons.
- `library/` contains device-specific helper scripts.
- Environment variables can be inherited by groups and overridden per action.
- `needs` lets one action trigger related actions in a best-effort chain.

## Security

Keep the server on a trusted private network or behind authentication. Do not expose an unauthenticated server to the public internet. For remote access, use a VPN or another secure, authenticated channel.

## Related

- [Flask](https://flask.palletsprojects.com/)
- [Gunicorn](https://docs.gunicorn.org/)
- [JSON Schema](https://json-schema.org/)

## License

This project is distributed under the MIT license.
