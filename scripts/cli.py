#!/usr/bin/env python3
"""Trigger pipeline actions locally without the HTTP server.

Usage:
    scripts/cli.py list          list every endpoint defined in pida.yaml
    scripts/cli.py <path>        run an endpoint, e.g. group/action
"""
import logging
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import pipeline

logging.basicConfig(level=logging.WARNING)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    arg = sys.argv[1]

    try:
        cfg = pipeline.load()
    except pipeline.PipelineError as e:
        print(f"pipeline error: {e}", file=sys.stderr)
        return 1

    if arg == "list":
        for endpoint in sorted(cfg["actions"]):
            print(endpoint)
        return 0

    path = arg.rstrip("/")
    if path not in cfg["actions"]:
        if path in cfg["group_prefixes"]:
            kids = pipeline.children(cfg, path)
            print(f"'{path}' is a group, use one of:")
            for kid in kids:
                print(f"  {path}/{kid}")
            return 1
        print(f"unknown path: {path}", file=sys.stderr)
        print("try: python scripts/cli.py list", file=sys.stderr)
        return 1

    steps = pipeline.run_path(cfg, path)
    result = pipeline.dispatch_result(cfg["actions"][path], steps)
    ok = result["ok"]
    for step in steps:
        mark = "ok" if (step.get("exit", 0) == 0 and "error" not in step and "skipped" not in step) else "!!"
        line = f"[{mark}] {step['path']}"
        if "run" in step:
            line += f": {step['run']}"
        detail = step.get("error") or step.get("skipped")
        if detail:
            line += f" ({detail})"
        output = step.get("stderr") or step.get("output")
        if isinstance(output, str) and output.strip():
            line += f" | {output.strip()[:400]}"
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
