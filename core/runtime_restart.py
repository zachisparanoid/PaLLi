"""Schedule a restart only for a backend started by the PaLLi launcher."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
_restart_process = None


def restart_status() -> dict:
    pending = _restart_process is not None and _restart_process.poll() is None
    return {"available": os.environ.get("PALLI_LAUNCHER_MANAGED") == "1",
            "pending": pending, "instance": str(os.getpid())}


def schedule_restart() -> None:
    global _restart_process
    if not restart_status()["available"]:
        raise RuntimeError("This installation's host manages restarts. Restart it through that host.")
    if restart_status()["pending"]:
        return
    directory = ROOT / "logs" / "launcher"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / "restart.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "ab") as output:
        _restart_process = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "restart_runtime.py")],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
