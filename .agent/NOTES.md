# Project Notes

## Architecture

The service loads `~/.pida/pida.yaml` and turns action paths into HTTP
endpoints. Each configured action runs its shell command with the working
directory set to `~/.pida`. `scripts/cli.py` can list or trigger the same actions.
The flat `src/` modules own the API and pipeline; device-specific scripts live
under `library/`. Adding a control should normally be a YAML edit, not a new
server route.

Repository layout:

- `src/`: flat `pipeline.py`, `server.py`, and `pida.schema.json` files.
- `scripts/`: CLI, git installer, and service restart helper.
- `pyproject.toml`: all pinned project dependencies.
- `library/`: per-install device helpers invoked by pipeline commands.
- `icons/`: optional action icons mirroring endpoint paths.
- `~/.pida/`: commands, personal dependencies, icons, drivers, and runtime
  logs; do not commit this data.

## Pipeline Configuration

- Top-level group keys may be plain or contain slashes. An endpoint is the
  group path plus the action name.
- Each group contains action entries with a `run:` shell command. Commands
  run under bash with `~/.pida` as the working directory.
- `env:` values can be inherited from a group or overridden per action.
- `needs:` runs listed endpoints in parallel as best-effort dependencies;
  `allow-failure:` prevents those failures from failing the action.
- `timeout:` is an optional per-action timeout; the global default is 60s.

Legacy actions are no-argument triggers. Optional `params`, `inputs`, and
`outputs` define typed request and response data for state-capable actions.
Inputs are validated before dispatch and are provided to commands through
`INPUT_*` environment variables. Declared outputs are written to the
per-dispatch `PIPELINE_OUTPUT` file and coerced to their declared types.
Numeric inputs support `min` and `max` clamping; enum values have an explicit
allow-list, and `required` defaults to false. Requests are partial deltas:
omitted inputs are unchanged and defaults are not applied. Unknown or invalid
inputs fail before dispatch.

For output-producing actions, commands write declared `key=value` lines to
`$PIPELINE_OUTPUT`; standard output remains available for human-readable
logs. Undeclared output keys are dropped, and values are coerced to each
parameter's declared type.

## API and Runtime

- Gunicorn runs under systemd on port `9999` (configurable via `~/.pida/env`), with one worker and three
  threads. The local service is configured for a trusted LAN; it has no
  authentication layer.
- `GET /api/health` reports service health.
- `GET /api/endpoints` lists configured endpoint paths.
- `GET /api/schema` exposes typed action contracts without command strings.
- `GET /api/icon/<group>/<action>` serves configured action icons.
- `GET /api/list` returns configured actions and optional relative icon paths.
- `GET` and `POST /api/<group>/<action>` dispatch configured actions.
- `POST` to a group path returns its available actions. Parameterized GETs
  may be used for empty-delta state reads; browser prefetch can trigger GETs,
  so toggles should use POST.
- Failures use JSON responses: 400 validation, 404 unknown endpoint, 409
  superseded queued request, 500 dispatch/timeout/output failure, 502
  device-reported failure, and 503 queue wait timeout.
- Actions in the same group are serialized with one running slot and one
  latest-wins queue slot; different groups may run in parallel. A queued
  request can be superseded, and a queue wait over 30 seconds returns busy.
- Logs are stored under `~/.pida/logs/`. Configuration edits apply without
  a restart; code or dependency changes require `scripts/restart.sh`.

## CLI and Operations

Run `~/.pida/.venv/bin/python scripts/cli.py list` to list endpoints or
`~/.pida/.venv/bin/python scripts/cli.py <group/action>` to trigger one
locally. Inspect the service with `systemctl status pi-api`
and `journalctl -u pi-api -f`; health is checked at
`http://<pi>:9999/api/health`.

## Installation and Personal Data

The Debian package installs program files under `/opt/pida` and keeps
per-install data under `~/.pida`. The clone installer targets the current
user. Device definitions, personal dependency additions, icons, and driver
scripts belong in the per-install data directory and must not be committed.
All project dependencies are declared in `pyproject.toml`. The optional
`~/.pida/requirements.txt` is only for private device-script extras.

The public [README](../README.md) stays at the repository root and contains
installation steps. Package-specific guides stay in `package/` because the
Debian package installs them into the per-install `icons/` and `library/`
directories.

## Migration TODOs

The maintained work list is in [TODO.md](TODO.md). The inherited items have
not been revalidated against the current implementation.
