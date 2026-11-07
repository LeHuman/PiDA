#!/usr/bin/env python3
"""Launch gunicorn with service settings from pida.yaml.

pi-api.service runs this under the per-install venv; bind, workers, and
threads come from the `service:` section of ~/.pida/pida.yaml (which
replaced the old env file), with packaged defaults for anything unset.
gunicorn then imports the real app (server:app) from src/.
"""
import os
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pipeline  # noqa: E402


def main() -> None:
    svc = pipeline.service_settings()
    os.execv(
        str(pipeline.VENV / "bin" / "gunicorn"),
        [
            "gunicorn",
            "-w", str(svc["workers"]),
            "--threads", str(svc["threads"]),
            "-b", f"{svc['bind']}:{svc['port']}",
            "server:app",
        ],
    )


if __name__ == "__main__":
    main()