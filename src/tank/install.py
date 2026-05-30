"""Install/uninstall the Windows Scheduled Task that runs `tank tick`."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def install_scheduled_task() -> int:
    script = SCRIPTS_DIR / "install-scheduled-task.ps1"
    if not script.exists():
        logger.error("install script missing: %s", script)
        return 1
    # The scheduled task must run THIS interpreter (the venv where `tank` is
    # installed), not whatever pythonw.exe is first on PATH — that's often the
    # Windows Store shim, which has no `tank` and would fail silently.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    pythonw_arg = str(pythonw) if pythonw.exists() else ""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-PythonW", pythonw_arg],
        capture_output=True, text=True,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def uninstall_scheduled_task() -> int:
    script = SCRIPTS_DIR / "uninstall.ps1"
    if not script.exists():
        logger.error("uninstall script missing: %s", script)
        return 1
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        capture_output=True, text=True,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode
