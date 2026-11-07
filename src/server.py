"""Thin Flask wrapper over pipeline.py. Runs under gunicorn (pi-api.service)."""

import pathlib
import sys
from flask import Flask, jsonify, request, send_file

# Ensure ~/.pida is in sys.path for library imports
ROOT = pathlib.Path.home() / ".pida"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline

app = Flask(__name__)

# Preserve pida.yaml declaration order on the wire. Flask sorts dict
# keys alphabetically by default, which silently breaks order-sensitive
# consumers (e.g. "first bool = toggle, first numeric = fused slider" on
# the Android tiles would follow the alphabet instead of the yaml).
app.json.sort_keys = False


pipeline.attach_file_logs()
dep_status = pipeline.sync_deps()
LOG = pipeline.LOG
LOG.info("server start; dependency sync: %s", dep_status)

try:
    _cfg_check = pipeline.load()
    LOG.info("pida.yaml ok: %d endpoints", len(_cfg_check["actions"]))
except pipeline.PipelineError as e:
    LOG.error("pida.yaml invalid: %s", e)


@app.get("/api/health")
def health():
    try:
        cfg = pipeline.load()
    except pipeline.PipelineError as e:
        LOG.error("health: yaml error: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({
        "ok": True,
        "deps": dep_status,
        "groups": sorted(cfg["groups"]),
        "endpoints": len(cfg["actions"]),
    })


@app.get("/api/schema")
def api_schema():
    """Machine catalog: groups -> actions -> {params, inputs, outputs, timeout}.

    Typed specs only — no run strings, no env internals.
    """
    try:
        cfg = pipeline.load()
    except pipeline.PipelineError as e:
        LOG.error("schema: yaml error: %s", e)
        return jsonify({"ok": False, "error": f"invalid pipeline: {e}"}), 500
    return jsonify(pipeline.schema(cfg))


@app.get("/api/list")
def list_actions():
    try:
        cfg = pipeline.load()
    except pipeline.PipelineError as e:
        LOG.error("list: yaml error: %s", e)
        return jsonify({"ok": False, "error": f"invalid pipeline: {e}"}), 500
    tree = {}
    for endpoint, entry in sorted(cfg["actions"].items()):
        group, name = endpoint.rsplit("/", 1)
        item: dict = {"run": entry["run"]} if entry["run"] else {"needs": entry["needs"]}
        icon = pipeline.icon_rel(endpoint)
        if icon:
            item["icon"] = icon
        if entry["env"]:
            item["env"] = entry["env"]
        if entry["allow-failure"]:
            item["allow-failure"] = True
        if entry["params"]:
            item["params"] = entry["params"]
            item["inputs"] = entry["inputs"]
            if entry["outputs"]:
                item["outputs"] = entry["outputs"]
        tree.setdefault(group, {})[name] = item
    return jsonify({
        "groups": tree,
        "prefixes": sorted(g for g in cfg["group_prefixes"] if g not in tree),
    })


@app.get("/api/endpoints")
def endpoints_text():
    """Plain-text newline list of endpoint paths (shell-friendly, no JSON)."""
    try:
        cfg = pipeline.load()
    except pipeline.PipelineError as e:
        return f"invalid pipeline: {e}\n", 500
    return "\n".join(sorted(cfg["actions"])) + "\n"


@app.get("/api/icon/<path:rest>")
def icon(rest: str):
    """Download the png associated with an action (mirrors icons/ tree)."""
    p = pipeline.icon_path(rest)
    if p is None:
        return jsonify({"ok": False, "error": "no icon", "path": rest}), 404
    return send_file(p, mimetype="image/png")


@app.post("/api/<path:rest>")
@app.get("/api/<path:rest>")
def run_action(rest: str):
    rest = rest.rstrip("/")
    if not rest:
        return jsonify({"ok": False, "error": "empty path"}), 400

    try:
        cfg = pipeline.load()
    except pipeline.PipelineError as e:
        LOG.error("load error for %s: %s", rest, e)
        return jsonify({"ok": False, "error": f"invalid pipeline: {e}"}), 500

    # Action -> dispatch (needs fan out in parallel, then its own run blocks)
    body = None
    if request.method == "POST":
        body = request.get_json(silent=True)
    try:
        result, code = pipeline.dispatch_request(cfg, rest, body)
        return jsonify(result), code
    except pipeline.RequestError as e:
        (LOG.info if e.code < 500 else LOG.warning)(
            "request %s -> %d %s", rest, e.code, e.error)
        if e.code == 400 and rest in cfg["group_prefixes"]:
            return jsonify({"ok": False, "error": e.error,
                            "available": pipeline.children(cfg, rest)}), e.code
        return jsonify({"ok": False, "error": e.error}), e.code
