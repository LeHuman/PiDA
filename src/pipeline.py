"""Shared pipeline logic for the Pi Action API.

Loads ~/.pida/pida.yaml, flattens group keys (including slash keys)
into endpoint paths, and executes runs. Used by both the REST server
(server.py) and the local CLI (cli.py).

Data model (per endpoint):
  run:            shell string executed under bash, cwd = ~/.pida (optional when needs is set)
  env:            inherited from groups, overridable per action
  needs:          optional list of endpoint paths; all run in parallel, the action's own run: blocks until they finish
  allow-failure:  optional bool; failures in this dispatch do not mark the endpoint failed (still logged)
  timeout:        optional seconds; default EXEC_TIMEOUT (60)
  params:         optional typed contract {name: {type, min?, max?, enum?, required?}}
  inputs:         optional list of param names accepted in the request body
  outputs:        optional list of param names the action publishes

Parameterized semantics (additive over legacy no-arg actions):
  - POST body is a PARTIAL DELTA: provided input -> validated/clamped; unset
    input = no change, no defaults ever. GET / POST {} = empty delta.
  - each PROVIDED input is injected as env INPUT_<UPPER_SNAKE>; unset input
    -> the var never exists. The run string composes conditional argv.
  - declaring outputs sets PIPELINE_OUTPUT=<tmpfile>; the run's own pipeline
    writes key=value lines there; values are coerced per the param's type.
  - response: {"ok": true, <declared outputs merged>} (no steps blob).
  - settle: dispatches WITH inputs converge before answering — re-apply
    (absolute, idempotent by the partial-delta contract) until every
    provided input that is also an output is reflected AND the readback
    is stable across two consecutive passes, bounded by SETTLE_BUDGET.
    Reads (empty delta), legacy actions, and slow first passes (budget
    already spent) keep single-pass behavior: readback = device truth.

Queue: every action runs inside its group's device lane (longest matching
group prefix) -> ONE running + ONE queued request per group; a newer request
evicts the queued one (evicted -> RequestError 409 "superseded"); queue wait
> QUEUE_WAIT -> RequestError 503 "busy".

Logging: module logger "pi-api"; the server attaches a 10MB rotating file
handler (~/.pida/logs/pi-api.log). Every dispatch step is logged info/warn/error.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = pathlib.Path.home() / ".pida"
COMMANDS = ROOT / "pida.yaml"
SCHEMA_PATH = pathlib.Path(__file__).with_name("pida.schema.json")
if not SCHEMA_PATH.is_file():
    SCHEMA_PATH = pathlib.Path(sys.prefix) / "share" / "pi-action-api" / "pida.schema.json"
LOG_DIR = ROOT / "logs"
ICONS = ROOT / "icons"
# The venv lives with the user data (both layouts): the .deb bootstraps it
# from vendored wheels; PIDA_VENV overrides for exotic setups.
VENV = pathlib.Path(os.environ.get("PIDA_VENV", str(ROOT / ".venv")))
VENV_PIP = VENV / "bin" / "pip"
VENV_PY = VENV / "bin" / "python"
SHEBANG = f"#!{VENV_PY}\n"
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEB_PROJECT = pathlib.Path("/opt/pida")
DEFAULT_PROJECT = (DEB_PROJECT if (DEB_PROJECT / "pyproject.toml").is_file()
                   else PROJECT_ROOT)
BASE_PROJECT = pathlib.Path(os.environ.get("PIDA_BASE_PROJECT", str(DEFAULT_PROJECT)))
BASE_METADATA = BASE_PROJECT / "pyproject.toml"
BASE_SPEC = "pi-action-api" if BASE_PROJECT == pathlib.Path("/opt/pida") else str(BASE_PROJECT)
# Personal ADDITIVE deps (one per line, for the user's library scripts):
# ~/.pida/requirements.txt. These are extras for local device scripts;
# application dependencies are declared in pyproject.toml.
LOCAL_REQ = ROOT / "requirements.txt"
# Vendored wheels from the package: preferred source for sync_deps installs
# (offline when what's needed is present; personal extras reach PyPI else).
WHEELS = pathlib.Path("/opt/pida/wheels")
# Per-venv hash: two venvs (the git-layout venv and the deb-layout venv) must not share one "already installed" marker — a fresh
# venv with a matched hash would skip its dependency install entirely.
HASH_FILE = VENV / ".requirements-hash"
LIBRARY = ROOT / "library"
BASH = "/bin/bash"
EXEC_TIMEOUT = 60  # seconds
QUEUE_WAIT = 30  # max seconds a request may sit in its group's queue slot
SETTLE_BUDGET = 4.0  # extra seconds a dispatch with inputs may take to
                     # converge (re-apply + confirm) after its first pass
CONFIRM_MAX = 1.0  # a first pass slower than this skips the stability
                   # confirm: slow protocols change state atomically (the
                   # readback already reflects the request), and doubling
                   # an already-slow dispatch buys nothing. Fast protocols
                   # (ramping dimmers) are the ones that need confirming.

PARAM_TYPES = ("bool", "int", "float", "enum", "string")

LOG = logging.getLogger("pi-api")


class PipelineError(Exception):
    """Invalid pipeline config or execution environment."""


with SCHEMA_PATH.open(encoding="utf-8") as schema_file:
    _PIPELINE_SCHEMA = json.load(schema_file)
Draft202012Validator.check_schema(_PIPELINE_SCHEMA)
_PIPELINE_VALIDATOR = Draft202012Validator(_PIPELINE_SCHEMA)


def validate_config(tree: Any) -> None:
    errors = sorted(
        _PIPELINE_VALIDATOR.iter_errors(tree),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise PipelineError(f"pida.yaml schema error at {location}: {error.message}")


class RequestError(Exception):
    """Request-level failure with a uniform payload and HTTP code.

    Raised (not returned) so the server handler and needs fan-out share one
    shape: {"ok": False, "error": error} + HTTP code. Codes: 400 validation,
    409 superseded (evicted from a queue slot), 503 busy (queue wait > 30s),
    500 = execution/coercion failure surfaced at the top-level dispatch.
    """

    def __init__(self, error: str, code: int = 500):
        super().__init__(error)
        self.error = error
        self.code = code


def attach_file_logs() -> pathlib.Path:
    """10MB rotating file log at ~/.pida/logs/pi-api.log (one backup)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    from logging.handlers import RotatingFileHandler

    path = LOG_DIR / "pi-api.log"
    handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=1)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    # MemoryHandler-eq: ensure rotation nuance: rotated file overwrites previous backup
    return path


def ensure_library_executables() -> list:
    """Make every library/**/*.py directly callable: venv shebang + exec bit.

    Idempotent; patches only what's missing. So `run:` strings can invoke
    them directly (./library/foo.py) and they auto-run under ~/.pida/.venv.
    """
    patched: list = []
    if not LIBRARY.is_dir():
        return patched
    x_bit = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    for py in LIBRARY.rglob("*.py"):
        if "__pycache__" in py.parts or py.name.startswith("_"):
            continue
        try:
            text = py.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if not text.strip():
            continue
        needs_shebang = not text.startswith("#!")
        # A shebang pointing at ANOTHER venv (e.g. the legacy ~/.script
        # layout before a deb takeover) must be re-pinned to this venv,
        # or the script dies with "bad interpreter" once that venv is gone.
        stale_shebang = (
            not needs_shebang
            and text.partition("\n")[0] != SHEBANG.rstrip("\n")
        )
        needs_exec = not (py.stat().st_mode & stat.S_IXUSR)
        if not (needs_shebang or stale_shebang or needs_exec):
            continue
        # Fix venv script not present — leave file alone but warn.
        if not VENV_PY.is_file():
            LOG.warning("library: %s not runnable, venv python missing", py)
            continue
        try:
            if needs_shebang:
                py.write_text(SHEBANG + text)
                LOG.info("library: pinned venv shebang in %s", py)
            elif stale_shebang:
                py.write_text(SHEBANG + text.partition("\n")[2])
                LOG.info("library: re-pinned stale venv shebang in %s", py)
            if needs_exec:
                py.chmod(py.stat().st_mode | x_bit)
                LOG.info("library: made %s executable", py)
            patched.append(str(py))
        except OSError as e:
            LOG.error("library: could not patch %s: %s", py, e)
    return patched


def sync_deps() -> str:
    """Install project and local-extra dependencies when metadata changes.

    Project dependencies come from BASE_METADATA; LOCAL_REQ contains only
    per-install extras for device scripts. Vendored wheels are preferred
    when present, with PyPI as a fallback for new local extras.
    """
    base = BASE_METADATA if BASE_METADATA.is_file() else None
    files = [p for p in (base, LOCAL_REQ) if p is not None and p.is_file()]
    if not files:
        return "no-requirements"
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()
    if HASH_FILE.is_file() and HASH_FILE.read_text().strip() == digest:
        return "unchanged"
    if not VENV_PIP.is_file():
        raise PipelineError(f"missing venv pip at {VENV_PIP}")
    LOG.info("requirements changed -> pip install %s", ", ".join(str(p) for p in files))
    cmd = [str(VENV_PIP), "install"]
    if WHEELS.is_dir():
        cmd += ["--find-links", str(WHEELS)]
    cmd.append(BASE_SPEC)
    if LOCAL_REQ.is_file():
        cmd += ["-r", str(LOCAL_REQ)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        # Degrade, don't crash-loop: the server starts with the previously
        # installed set; new/unresolved deps surface as dispatch failures.
        # (A personal line re-pinning a base package is a pip conflict ->
        # lands here, visible in the log, instead of reverting the base.)
        LOG.error("pip install failed: %s", proc.stderr[-500:])
        return "install-failed"
    HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(digest)
    return "installed"


# --------------------------------------------------------------------------
# parameter parsing / validation
# --------------------------------------------------------------------------

def _param_map(raw: Any, endpoint: str) -> dict:
    """Validate a `params:` mapping into {name: {type, min?, max?, enum?, required?}}."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PipelineError(f"{endpoint}: 'params' must be a mapping of name -> spec")
    out: dict = {}
    for name, spec in raw.items():
        name = str(name)
        if not isinstance(spec, dict):
            raise PipelineError(f"{endpoint}: param '{name}' spec must be a mapping")
        t = spec.get("type")
        if t not in PARAM_TYPES:
            raise PipelineError(
                f"{endpoint}: param '{name}': type must be one of {list(PARAM_TYPES)}")
        s: dict = {"type": t}
        if "min" in spec or "max" in spec:
            lo, hi = spec.get("min"), spec.get("max")
            if t not in ("int", "float"):
                raise PipelineError(f"{endpoint}: param '{name}': min/max need a numeric type")
            if lo is not None and not isinstance(lo, (int, float)):
                raise PipelineError(f"{endpoint}: param '{name}: min must be a number")
            if hi is not None and not isinstance(hi, (int, float)):
                raise PipelineError(f"{endpoint}: param '{name}': max must be a number")
            if lo is not None:
                s["min"] = lo
            if hi is not None:
                s["max"] = hi
        if spec.get("enum") is not None:
            if t != "enum":
                raise PipelineError(f"{endpoint}: param '{name}': enum only valid with type enum")
            s["enum"] = list(spec["enum"])
        if spec.get("required"):
            s["required"] = True
        out[name] = s
    return out


def _name_list(raw: Any, key: str, params: dict, endpoint: str) -> list:
    """Validate inputs:/outputs: as a list of param names (subset of params:)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PipelineError(f"{endpoint}: '{key}' must be a list of param names")
    names = [str(n) for n in raw]
    if len(set(names)) != len(names):
        raise PipelineError(f"{endpoint}: duplicate names in '{key}'")
    for n in names:
        if n not in params:
            raise PipelineError(f"{endpoint}: {key} name '{n}' is not declared in params")
    return names


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _coerce_value(name: str, spec: dict, value: Any, clamp: bool) -> Any:
    """Coerce one value per the param spec.

    clamp=True  -> request input: numeric strings and bools rejected per type,
                   numbers clamped into min/max before dispatch.
    clamp=False -> published output: parse the key=value string (or json
                   value), type-coerce; out-of-range only logs (readback is
                   the device's truth, never silently rewritten).
    Raises RequestError(400) on input mismatch, RequestError(500) via caller
    for output mismatches.
    """
    t = spec["type"]
    if t == "bool":
        if clamp:
            if isinstance(value, bool):
                return value
            raise RequestError(f"{name}: expected bool, got {type(value).__name__}", 400)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise RequestError(f"{name}: expected bool, got {value!r}", 400)
    if t == "string":
        if isinstance(value, str):
            return value
        raise RequestError(f"{name}: expected string, got {type(value).__name__}", 400)
    if t == "enum":
        if value in spec.get("enum", []):
            return value
        raise RequestError(f"{name}: allowed values: {spec.get('enum')}", 400)
    # numeric: int | float
    if isinstance(value, str) and not clamp:
        try:
            value = float(value)
        except ValueError:
            raise RequestError(f"{name}: expected {t}, got {value!r}", 400)
    if not _is_number(value):
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                raise RequestError(f"{name}: expected {t}, got {value!r}", 400)
        else:
            raise RequestError(f"{name}: expected {t}, got {type(value).__name__}", 400)
    num = float(value)
    if t == "int":
        if num != int(num):
            raise RequestError(f"{name}: expected integer, got {value!r}", 400)
        num = int(num)
    lo, hi = spec.get("min"), spec.get("max")
    if clamp:
        if lo is not None and num < lo:
            LOG.info("param %s clamped %s -> %s (min)", name, num, lo)
            num = lo
        if hi is not None and num > hi:
            LOG.info("param %s clamped %s -> %s (max)", name, num, hi)
            num = hi
    else:
        if (lo is not None and num < lo) or (hi is not None and num > hi):
            LOG.info("param %s readback %s outside declared range [%s, %s]",
                     name, num, lo, hi)
    return num


def validate_request(entry: dict, body: Any) -> dict:
    """Validate a request body against an action's inputs; return the delta.

    Partial-delta semantics: unknown input -> 400 (no dispatch); bad type ->
    400; missing required -> 400; numbers clamped into min/max.
    """
    params, inputs = entry["params"], entry["inputs"]
    if params is None:
        return {}
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise RequestError("request body must be a JSON object", 400)
    delta: dict = {}
    for name, value in body.items():
        name = str(name)
        if name not in params or name not in inputs:
            raise RequestError(f"unknown input: {name}", 400)
        delta[name] = _coerce_value(name, params[name], value, clamp=True)
    for name in inputs:
        if params[name].get("required") and name not in delta:
            raise RequestError(f"missing required input: {name}", 400)
    return delta


def _env_var(name: str) -> str:
    return "INPUT_" + re.sub(r"\W+", "_", name).upper()


def _env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# --------------------------------------------------------------------------
# per-group device lanes: 1 running + 1 queued, latest-wins eviction
# --------------------------------------------------------------------------

class _Lane:
    """One device lane. lock: held by the running request; queued: one slot.

    All lock ownership changes happen under `ql` so handoff (runner release
    -> queued grant) and eviction cannot interleave.
    """

    __slots__ = ("lock", "ql", "queued")

    def __init__(self):
        self.lock = threading.Lock()
        self.ql = threading.Lock()
        self.queued: dict | None = None


_LANES: dict[str, _Lane] = {}
_LANES_CL = threading.Lock()


def _lane_for(key: str) -> _Lane:
    with _LANES_CL:
        return _LANES.setdefault(key, _Lane())


def _lane_key(cfg: dict, path: str) -> str | None:
    """Longest matching group prefix: group/sub/action -> group/sub."""
    parts = path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        cand = "/".join(parts[:i])
        if cand in cfg["group_prefixes"]:
            return cand
    return None


def _lane_acquire(lane: _Lane) -> dict:
    """Take the lane: run now, take the queue slot, or fail fast.

    Latest wins: a new request arriving while ANOTHER request already sits
    queued replaces it (the evicted one gets event+superseded). Returns a
    ticket; raises RequestError(409) when evicted, RequestError(503) when
    the queue wait exceeds QUEUE_WAIT.
    """
    with lane.ql:
        if lane.lock.acquire(timeout=0):
            return {"holder": True}
        ticket = {"event": threading.Event(), "superseded": False}
        old = lane.queued
        if old is not None:
            old["superseded"] = True
            old["event"].set()
        lane.queued = ticket
    ticket["event"].wait(QUEUE_WAIT)
    if ticket["superseded"]:
        raise RequestError("superseded by a newer request in this group", 409)
    if not ticket["event"].is_set():
        with lane.ql:
            if lane.queued is ticket:
                lane.queued = None
        raise RequestError(f"group busy: queued wait exceeded {QUEUE_WAIT}s", 503)
    return {"holder": True}


def _lane_release(lane: _Lane, ticket: dict | None) -> None:
    if ticket is None:
        return
    with lane.ql:
        nxt = lane.queued
        lane.queued = None
        if nxt is not None:
            nxt["event"].set()  # hand the lock to the queued request
        else:
            lane.lock.release()


# --------------------------------------------------------------------------
# yaml loading
# --------------------------------------------------------------------------

def _is_action(item: Any) -> bool:
    return isinstance(item, dict) and "action" in item and ("run" in item or "needs" in item)


def _allow_failure(raw: Any) -> bool:
    if raw is None:
        return False
    return bool(raw)


def _env_map(raw: Any) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PipelineError("'env' must be a mapping of VAR -> value")
    return {str(k): str(v) for k, v in raw.items()}


def _needs_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise PipelineError("'needs' must be a list of endpoint paths")
    return [str(n) for n in raw]


def _expand_list(items: list) -> tuple[list, dict, dict]:
    """A group value (a list) may mix actions, env specs, and subgroup maps."""
    actions: list = []
    subs: dict = {}
    env_updates: dict = {}
    for item in items:
        if isinstance(item, dict) and "env" in item:
            if set(item) - {"env"}:
                raise PipelineError(
                    f"env item must contain only 'env' (indent the variables "
                    f"under it, not beside it): {item!r}"
                )
            env_updates.update(_env_map(item["env"]))
        elif _is_action(item):
            if "run" not in item and "needs" not in item:
                raise PipelineError(
                    f"action '{item.get('action')}' needs a 'run:' or 'needs:'"
                )
            actions.append(item)
        elif isinstance(item, dict) and len(item) == 1:
            key, val = next(iter(item.items()))
            if key == "description":
                continue
            subs[key] = val
        else:
            raise PipelineError(f"invalid group item: {item!r}")
    return actions, subs, env_updates


def load() -> dict:
    """Flatten pida.yaml into endpoint mappings.

    Returns:
        actions: {endpoint_path: entry}; entry keys: run, env, needs,
                 allow-failure, timeout, params, inputs, outputs
        groups: {group_path: {action_name: run-line}}
        group_prefixes: set of every intermediate group path
    """
    if not COMMANDS.is_file():
        raise PipelineError(f"{COMMANDS} not found")
    try:
        with open(COMMANDS, "r") as f:
            tree = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise PipelineError(f"yaml parse error: {e}")
    validate_config(tree)

    actions: dict = {}
    groups: dict = {}
    prefixes: set = set()

    def register(prefix: list, action_name: str, env: dict, needs: list,
                 run: str | None, allow_failure: bool, act: dict) -> None:
        endpoint = "/".join([*prefix, action_name])
        if endpoint in actions:
            raise PipelineError(f"duplicate endpoint: {endpoint}")
        params = _param_map(act.get("params"), endpoint)
        inputs = _name_list(act.get("inputs"), "inputs", params, endpoint)
        outputs = _name_list(act.get("outputs"), "outputs", params, endpoint)
        raw_timeout = act.get("timeout")
        timeout: float | None = None
        if raw_timeout is not None:
            if not _is_number(raw_timeout) or raw_timeout <= 0:
                raise PipelineError(f"{endpoint}: 'timeout' must be a positive number")
            timeout = raw_timeout
        if params and not inputs:
            LOG.warning("%s: declares params but no inputs (read-only state)", endpoint)
        actions[endpoint] = {
            "run": run, "env": env, "needs": needs,
            "allow-failure": allow_failure,
            "timeout": timeout, "params": params or None,
            "inputs": inputs, "outputs": outputs,
        }
        groups.setdefault("/".join(prefix), {})[action_name] = (
            run or f"needs: {needs}")

    def walk(prefix: list, node: Any, scope_env: dict) -> None:
        if isinstance(node, dict):
            env = dict(scope_env)
            env.update(_env_map(node.get("env")))
            for key, val in node.items():
                if key in ("description", "env"):
                    continue
                bits = str(key).split("/")
                for i in range(1, len(bits) + 1):
                    prefixes.add("/".join([*prefix, *bits[:i]]))
                walk(prefix + bits, val, env)
        elif isinstance(node, list):
            acts, subs, env_updates = _expand_list(node)
            env = dict(scope_env)
            env.update(env_updates)
            if not prefix and acts:
                raise PipelineError("actions cannot be defined at top level")
            for act in acts:
                env_a = dict(env)
                env_a.update(_env_map(act.get("env")))
                needs = _needs_list(act.get("needs"))
                allow = _allow_failure(act.get("allow-failure"))
                if act.get("allow_failure") is not None:
                    allow = _allow_failure(act.get("allow_failure"))
                register(prefix, str(act["action"]), env_a, needs, act.get("run"),
                         allow, act)
            for key, val in subs.items():
                bits = str(key).split("/")
                for i in range(1, len(bits) + 1):
                    prefixes.add("/".join([*prefix, *bits[:i]]))
                walk(prefix + bits, val, env)
        else:
            raise PipelineError(f"unexpected value at {'/'.join(prefix) or '<root>'}: {node!r}")

    walk([], tree, {})
    ensure_library_executables()
    return {"actions": actions, "groups": groups, "group_prefixes": prefixes}


def execute(run: str, timeout: int = EXEC_TIMEOUT, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a `run:` string under bash with cwd anchored at ~/.pida."""
    full_env = os.environ.copy()
    # Prefer console scripts from the per-install venv over tools from the
    # system PATH, so action behavior does not depend on global pip state.
    venv_bin = str(VENV / "bin")
    full_env["PATH"] = venv_bin + os.pathsep + full_env.get("PATH", "")
    if env:
        full_env.update(env)
    return subprocess.run(
        [BASH, "-c", run],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


def _tail(text: str | None, limit: int = 800) -> str | None:
    text = text or ""
    if not text.strip():
        return None
    return text[-limit:] if len(text) > limit else text


def _resolve_need(path: str, cfg: dict) -> str:
    p = path.strip().rstrip("/")
    if p in cfg["actions"]:
        return p
    alt = p.replace(".", "/")
    if alt in cfg["actions"]:
        return alt
    if p in cfg["group_prefixes"]:
        raise LookupError(f"{p} is a group, not an action")
    raise LookupError(f"unknown endpoint: {p}")


def _fan_out(cfg: dict, need_paths: list, timeout: int | None, seen: set,
             held: set, cause: str) -> list:
    """Run sibling needs in parallel (best effort), merge their step records."""
    steps: list = []
    if len(need_paths) == 1:
        return _run_need(cfg, need_paths[0], timeout, seen, held, cause)
    with ThreadPoolExecutor(max_workers=len(need_paths)) as pool:
        futures = [pool.submit(_run_need, cfg, np, timeout, seen, held, cause)
                   for np in need_paths]
        for fut in futures:
            steps += fut.result()
    return steps


def _run_need(cfg: dict, np: str, timeout: int | None, seen: set,
              held: set, cause: str) -> list:
    """One need dispatch; RequestError (superseded/busy) degrades to a failed step."""
    try:
        return run_path(cfg, np, timeout, set(seen), cause, _held=set(held))
    except RequestError as e:
        LOG.warning("need %s: %s (%d)", np, e.error, e.code)
        return [{"path": np, "error": e.error}]


def run_path(cfg: dict, path: str, timeout: int | None = None,
             _seen: set | None = None, _cause: str | None = None,
             _held: set | None = None, delta: dict | None = None) -> list:
    """Dispatch an endpoint: serialize on its lane, needs in parallel, run.

    Legacy actions return their usual step records. Parameterized actions
    additionally build INPUT_* env from the delta (provided inputs only),
    expose PIPELINE_OUTPUT when outputs are declared, and attach parsed
    outputs to the action's own step ("outputs" key).

    Raises RequestError for request-level failures (superseded 409, busy 503,
    output coercion 500). Callers that treat this as a best-effort need catch
    it; the top-level handler maps it straight to the HTTP response.
    """
    steps: list = []
    if _seen is None:
        _seen = set()
    if _held is None:
        _held = set()
    path = str(path).strip().rstrip("/")
    if not path:
        steps.append({"path": "", "error": "empty path"})
        return steps

    if path in _seen:
        LOG.warning("dispatch %s skipped: cycle (from %s)", path, _cause or "request")
        steps.append({"path": path, "skipped": f"already ran (cycle from {_cause})"})
        return steps
    _seen.add(path)

    entry = cfg["actions"].get(path)
    if entry is None:
        LOG.error("dispatch %s failed: unknown endpoint (from %s)", path, _cause or "request")
        steps.append({"path": path, "error": "unknown endpoint"})
        return steps

    action_timeout = entry["timeout"]
    if action_timeout is None:
        action_timeout = timeout if timeout is not None else EXEC_TIMEOUT

    lane_key = _lane_key(cfg, path)
    ticket = None
    lane = _lane_for(lane_key) if lane_key else None
    if lane is not None and lane_key not in _held:
        ticket = _lane_acquire(lane)  # raises RequestError 409/503
        _held.add(lane_key)
    try:
        steps += _dispatch_held(cfg, path, entry, action_timeout, _seen,
                                _cause, _held, delta)
    finally:
        if lane is not None and ticket is not None:
            _lane_release(lane, ticket)
    return steps


def _dispatch_held(cfg: dict, path: str, entry: dict, timeout: float,
                   _seen: set, _cause: str | None, _held: set,
                   delta: dict | None) -> list:
    """Lane == held here: fan needs out, then the action's own run."""
    steps: list = []

    need_paths: list = []
    for need in entry["needs"]:
        try:
            need_path = _resolve_need(str(need), cfg)
        except LookupError as e:
            LOG.error("dispatch %s: bad need %r: %s", path, need, e)
            steps.append({"path": path, "need": str(need), "error": str(e)})
            continue
        if need_path in need_paths:
            LOG.warning("dispatch %s: duplicate need %r ignored", path, need_path)
            continue
        need_paths.append(need_path)

    # Fan needs out in parallel; each blocks until all complete.
    if need_paths:
        steps += _fan_out(cfg, need_paths, None, _seen, _held, path)

    run = entry["run"]
    if run is None:
        LOG.info("dispatch %s ok (%d need(s) fanned out)", path, len(need_paths))
        return steps

    if entry["params"] is not None:
        # Parameterized: bounded settle loop — the response's readback is
        # converged truth (see _converge_dispatch).
        steps += _converge_dispatch(path, entry, run, timeout, delta)
        return steps

    # Legacy one-shot (no typed contract).
    run_env = dict(entry["env"] or {})
    t0 = time.monotonic()
    try:
        proc = execute(run, timeout=timeout, env=run_env or None)
    except subprocess.TimeoutExpired:
        LOG.warning("dispatch %s timeout after %.1fs: %r", path,
                    time.monotonic() - t0, run)
        steps.append({"path": path, "run": run, "error": "timeout"})
        return steps

    dt = time.monotonic() - t0
    if proc.returncode == 0:
        LOG.info("dispatch %s ok exit=0 %.2fs%s", path, dt,
                 f" env={run_env}" if run_env else "")
    else:
        LOG.warning("dispatch %s failed exit=%s %.2fs run=%r stderr=%s",
                    path, proc.returncode, dt, run, (_tail(proc.stderr) or "").strip())
    step = {"path": path, "run": run, "exit": proc.returncode}
    output = _tail(proc.stdout)
    if output:
        step["output"] = output
    if proc.returncode != 0:
        if _tail(proc.stdout):
            step["stdout"] = _tail(proc.stdout)
        if _tail(proc.stderr):
            step["stderr"] = _tail(proc.stderr)
    steps.append(step)
    return steps


def _outputs_match(outputs: dict, checked: list, delta: dict, params: dict) -> bool:
    """True when every provided input that is also a declared output is
    reflected in the readback (bool/int/enum/string exact, float within 0.5)."""
    for name in checked:
        got = outputs.get(name)
        if got is None:
            return False
        want = delta[name]
        t = params[name]["type"]
        if t == "bool":
            if got is not want:
                return False
        elif t == "float":
            try:
                if abs(float(got) - float(want)) > 0.5:
                    return False
            except (TypeError, ValueError):
                return False
        else:
            if str(got) != str(want):
                return False
    return True


def _converge_dispatch(path: str, entry: dict, run: str, timeout: float,
                       delta: dict | None) -> list:
    """Parameterized dispatch with a bounded settle loop.

    Contract: the response readback is settled truth — every provided
    input that is also a declared output is reflected in it, and the full
    readback held still across two consecutive passes. Set actions are
    absolute (partial-delta semantics), so re-applying the same delta is
    always safe; devices settle at different speeds (dimmers ramp,
    displays are instant), so convergence is schema-driven and bounded:

    - pass 1 is a plain apply+read; extra passes re-apply the same delta
      and only start while the settle budget lasts — a slow first pass
      (waking display, asleep plug) spends the budget and keeps the
      single-pass behavior. A first pass slower than CONFIRM_MAX settles
      without the stability confirm: slow protocols flip atomically, so
      the confirm would only double the latency.
    - a pass that crashes or publishes an unparseable readback retries
      once; a flaky pass AFTER a good readback is tolerated so a
      confirmation blip cannot fail an otherwise good dispatch.
    - reads (empty delta) and actions without outputs never loop.
    - on budget exhaustion the last good readback wins: the readback is
      the device's truth and is never rewritten.
    """
    steps: list = []
    delta = delta or {}
    checked = [n for n in delta if n in entry["outputs"]]
    deadline = time.monotonic() + SETTLE_BUDGET
    prev: dict | None = None   # previous pass's readback
    last: dict | None = None   # last good readback
    fail: dict | None = None   # last failed pass's step record
    fails = 0                  # failed passes so far (retry budget: one)
    last_dt = 0.0              # elapsed seconds of the most recent pass

    while True:
        run_env = dict(entry["env"] or {})
        for name, value in delta.items():
            run_env[_env_var(name)] = _env_value(value)
        tmp_path = None
        if entry["outputs"]:
            with tempfile.NamedTemporaryFile(prefix="piapi-out-", delete=False) as tmp:
                tmp_path = tmp.name
            run_env["PIPELINE_OUTPUT"] = tmp_path
        t0 = time.monotonic()
        try:
            try:
                proc = execute(run, timeout=timeout, env=run_env or None)
            except subprocess.TimeoutExpired:
                LOG.warning("dispatch %s timeout after %.1fs: %r", path,
                            time.monotonic() - t0, run)
                fail = {"path": path, "run": run, "error": "timeout"}
                break
            dt = time.monotonic() - t0
            last_dt = dt
            if proc.returncode == 0:
                LOG.info("dispatch %s ok exit=0 %.2fs%s", path, dt,
                         f" env={run_env}" if run_env else "")
            else:
                LOG.warning("dispatch %s failed exit=%s %.2fs run=%r stderr=%s",
                            path, proc.returncode, dt, run,
                            (_tail(proc.stderr) or "").strip())
            if proc.returncode != 0:
                fail = {"path": path, "run": run, "exit": proc.returncode}
                if _tail(proc.stdout):
                    fail["stdout"] = _tail(proc.stdout)
                if _tail(proc.stderr):
                    fail["stderr"] = _tail(proc.stderr)
            elif entry["outputs"]:
                try:
                    outs = _parse_outputs(tmp_path, entry)
                except RequestError as e:
                    LOG.error("dispatch %s: %s", path, e.error)
                    fail = {"path": path, "run": run, "exit": 0, "error": e.error}
                else:
                    prev, last = last, outs
                    fail = None
            else:
                prev, last = last, {}
                fail = None
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        if fail is None:
            if not checked:
                break  # read-only dispatch: nothing to converge
            if not _outputs_match(last, checked, delta, entry["params"]):
                LOG.info("dispatch %s: readback not yet reflecting requested "
                         "fields, re-applying", path)
            elif prev is not None and prev == last:
                LOG.info("dispatch %s: settled (stable readback)", path)
                break
            elif prev is None and last_dt > CONFIRM_MAX:
                # Slow protocol: the readback already reflects the request;
                # a second pass would double an already-slow dispatch for
                # no accuracy gain (slow protocols change atomically).
                LOG.info("dispatch %s: settled (reflected; slow first pass, "
                         "skipping confirm)", path)
                break
            if time.monotonic() >= deadline:
                LOG.warning("dispatch %s: settle budget exhausted, returning "
                            "last readback", path)
                break
            continue

        # Failed pass: tolerate once a good readback exists, else retry once.
        if last is not None:
            LOG.warning("dispatch %s: pass failed after a good readback "
                        "(tolerated): %s", path,
                        fail.get("error") or f"exit {fail.get('exit')}")
            break
        fails += 1
        if fails >= 2 or time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if last is not None:
        steps.append({"path": path, "run": run, "exit": 0, "outputs": last})
        return steps
    steps.append(fail or {"path": path, "run": run, "error": "dispatch failed"})
    return steps


def _parse_outputs(tmp_path: str | None, entry: dict) -> dict:
    """Read key=value lines from PIPELINE_OUTPUT; coerce per the param types."""
    outs: dict = {}
    text = ""
    if tmp_path is not None:
        try:
            text = pathlib.Path(tmp_path).read_text()
        except OSError:
            pass
    declared = {name: entry["params"][name] for name in entry["outputs"]}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            LOG.warning("output line has no '=' (dropped): %r", line)
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in declared:
            LOG.info("undeclared output dropped: %s=%r", key, value)
            continue
        try:
            outs[key] = _coerce_value(key, declared[key], value, clamp=False)
        except RequestError as e:
            raise RequestError(f"output coercion failed for '{key}': {value!r}", 500)
    for key in declared:
        if key not in outs:
            raise RequestError(f"missing output: {key}", 500)
    return outs


def steps_ok(steps: list) -> bool:
    if not steps:
        return True  # explicit needs: [] + no run
    return all(
        "exit" in s and s["exit"] == 0
        for s in steps
    ) and all("error" not in s and "skipped" not in s for s in steps)


def _first_failure(steps: list) -> dict | None:
    for s in steps:
        if "error" in s or "skipped" in s or (s.get("exit", 0) != 0):
            return s
    return None


def dispatch_result(entry: dict, steps: list) -> dict:
    """Compute overall dispatch result; applies allow-failure if the step set failed."""
    ok = steps_ok(steps)
    failures = [s for s in steps
                if "error" in s or "skipped" in s or (s.get("exit", 0) != 0)]
    result = {"ok": ok, "steps": steps}
    if failures and entry.get("allow-failure"):
        LOG.warning("dispatch: %d failed step(s) tolerated (allow-failure)", len(failures))
        result["ok"] = True
        result["tolerated_failures"] = len(failures)
    return result


def dispatch_request(cfg: dict, path: str, body: Any,
                     log_cause: str = "http") -> tuple[dict, int]:
    """Top-level parameterized dispatch -> (response payload, http code).

    Uniform success shape: {"ok": true, <declared outputs merged>} (no
    steps). Uniform failure shape: {"ok": false, "error": "...", steps}.
    """
    path = str(path).strip().rstrip("/")
    if not path:
        raise RequestError("empty path", 400)
    entry = cfg["actions"].get(path)
    if entry is None:
        if path in cfg["group_prefixes"]:
            raise RequestError(f"{path} is a group, not an action", 400)
        LOG.warning("%s request %s: not found", log_cause, path)
        raise RequestError(f"not found: {path}", 404)

    delta = validate_request(entry, body)  # 400 on any validation failure
    t0 = time.monotonic()
    steps = run_path(cfg, path, _seen=set(), delta=delta)
    ok = steps_ok(steps)
    (LOG.info if ok else LOG.warning)(
        "%s request %s -> %s over %d step(s) in %.2fs", log_cause, path,
        "ok" if ok else "FAILED", len(steps), time.monotonic() - t0)

    if entry["params"] is None:
        # Legacy action: keep the historical ok/steps response.
        result = dispatch_result(entry, steps)
        result["path"] = path
        return result, 200 if result["ok"] else 500

    if not ok:
        fail = _first_failure(steps) or {}
        error = fail.get("error") or f"exit {fail.get('exit', 1)}"
        raise RequestError(error, 500)
    outs = {}
    for s in reversed(steps):
        if s["path"] == path and "outputs" in s:
            outs = s["outputs"]
            break
    response = {"ok": True, **outs}
    if entry.get("allow-failure"):
        tolerated = [s for s in steps
                     if "error" in s or "skipped" in s or (s.get("exit", 0) != 0)]
        if tolerated:
            response["tolerated_failures"] = len(tolerated)
    return response, 200


def schema(cfg: dict) -> dict:
    """Machine catalog: typed specs only — no run strings, no env internals."""
    groups: dict = {}
    for path, entry in cfg["actions"].items():
        g, _, name = str(path).rpartition("/")
        info: dict = {}
        if entry.get("params"):
            info["params"] = entry["params"]
        if entry.get("inputs"):
            info["inputs"] = entry["inputs"]
        if entry.get("outputs"):
            info["outputs"] = entry["outputs"]
        if entry.get("timeout") is not None:
            info["timeout"] = entry["timeout"]
        groups.setdefault(g, {}).setdefault("actions", {})[name] = info
    return {"groups": groups}


def icon_path(endpoint: str) -> pathlib.Path | None:
    """Existing png for an endpoint: icons/<endpoint path>.png (mirrored).

    The phone/web can fetch one per action via /api/icon/<path>. Returns
    None when absent or when the url-encoded path escapes icons/.
    """
    p = (ICONS / f"{endpoint.strip('/')}.png").resolve()
    root = ICONS.resolve()
    if not p.is_relative_to(root):
        return None
    return p if p.is_file() else None


def icon_rel(endpoint: str) -> str | None:
    """Relative-to-ROOT icon path for /api/list display (or None)."""
    p = icon_path(endpoint)
    return None if p is None else p.relative_to(ROOT).as_posix()


def children(cfg: dict, prefix: str) -> list:
    """Names of the next level under a group path (actions + sub-groups)."""
    out = set()
    base = prefix + "/"
    for endpoint in cfg["actions"]:
        if endpoint.startswith(base):
            out.add(endpoint[len(base):].split("/", 1)[0])
    for grp in cfg["group_prefixes"]:
        if grp.startswith(base):
            out.add(grp[len(base):].split("/", 1)[0])
    return sorted(out)
