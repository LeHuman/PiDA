#!/usr/bin/env python3
"""Launch gunicorn with service settings from pida.yaml.

pi-api.service runs this under the per-install venv; bind, workers, and
threads come from the `service:` section of ~/.pida/pida.yaml (which
replaced the old env file), with packaged defaults for anything unset.
gunicorn then imports the real app (server:app) from src/.
"""
import os
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import pipeline  # noqa: E402
except ImportError:
    # First start on a Python the vendored wheels do not cover (the deb
    # vendors 3.11-3.14): retry the base deps ONLINE here. pip still
    # prefers the vendored wheels via --find-links; PyPI fills the
    # ABI-specific gap. Offline + unsupported python -> ImportError
    # -> systemd restarts until the network is up.
    pip = pathlib.Path.home() / ".pida" / ".venv" / "bin" / "pip"
    wheels = pathlib.Path("/opt/pida/wheels")
    spec = "pi-action-api" if wheels.is_dir() else str(
        pathlib.Path(__file__).resolve().parents[1])
    cmd = [str(pip), "install"]
    if wheels.is_dir():
        cmd += ["--find-links", str(wheels)]
    cmd.append(spec)
    print("serve: deps missing for this python; retrying online: %s" % " ".join(cmd))
    subprocess.run(cmd, check=False)
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