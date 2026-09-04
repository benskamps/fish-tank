"""Install/uninstall the scheduled task that runs `tank tick`.

Two backends, chosen by platform: a Windows Scheduled Task, or a systemd user
timer. Anywhere else, `install` refuses and prints the one line you would have
to schedule yourself.

WHY THIS FILE GREW A SECOND BACKEND
-----------------------------------
Until 2026-08-31 this module was Windows-only — both entry points shelled out to
PowerShell — while `tank` itself has always been cross-platform. On a Linux box
that made `tank install` a command which could only fail, and the tank had no
heartbeat at all: it advanced exactly when a human typed `tank tick`.

The observed cost was not an error message. It was a tank that looked alive.
`~/.tank/world.json` and the rendered `tank.txt` are both just files; a world
that stopped ticking three days ago renders identically to one that ticked a
minute ago — plausible mood, plausible fish count, no staleness anywhere on the
frame. A tank adopted on 2026-08-28 was still showing that evening's weather on
2026-08-31 and nothing said so.

So the bug this closes is not "install is unavailable on Linux". It is "a
stopped world is indistinguishable from a running one". Hence
``schedule_status()``, which every backend must answer honestly — including the
case where the answer is "nothing here will ever tick this".

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No cron fallback. cron would work, and it fails into mail nobody reads, which is
the same class of bug as the one above wearing a different hat. If systemd is
absent we say so and hand back the command rather than installing a quieter
version of the problem.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"

#: How often the world advances. The README promises 5-30 minutes; 10 keeps the
#: weather legible without making the observer's ~80-repo git scan constant.
DEFAULT_INTERVAL_MINUTES = 10

SYSTEMD_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_NAME = "tank-tick.service"
TIMER_NAME = "tank-tick.timer"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _have_systemd() -> bool:
    """systemctl on PATH AND a user manager we can actually talk to.

    Both halves matter: systemctl exists in containers and under WSL where there
    is no user bus, and `--user` there fails with a message about a missing
    socket rather than doing anything.
    """
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", "is-system-running"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    out = r.stdout or ""
    # "degraded" is fine — some unrelated unit is failed, not our problem.
    return r.returncode == 0 or "running" in out or "degraded" in out


# ----------------------------------------------------------------------
# systemd backend
# ----------------------------------------------------------------------

def _systemd_units(interval_minutes: int) -> tuple[str, str]:
    """Render the .service and .timer bodies.

    The service runs THIS interpreter, exactly as the Windows backend pins
    pythonw.exe: `tank` is usually installed in a venv, and a bare `tank` on the
    unit's PATH is either missing or a different install.
    """
    python = sys.executable
    service = f"""[Unit]
# Written by `tank install`. Advances the aquarium one tick.
Description=fish-tank — advance the world one tick
Documentation=https://github.com/benskamps/fish-tank

[Service]
Type=oneshot
ExecStart={python} -m tank tick
# A tick that cannot finish inside a minute is wedged (a hung git scan, a stuck
# hardware probe). Kill it; the next tick picks the world up where it is.
TimeoutStartSec=60
Nice=10
"""
    timer = f"""[Unit]
Description=fish-tank — tick every {interval_minutes} minutes
Documentation=https://github.com/benskamps/fish-tank

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval_minutes}min
# Catch up one missed tick after the machine was asleep or off, so a world that
# was not ticking is stale for minutes rather than until someone happens to look.
Persistent=true
AccuracySec=30s
Unit={SERVICE_NAME}

[Install]
WantedBy=timers.target
"""
    return service, timer


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args],
                          capture_output=True, text=True)


def _linger_enabled() -> bool:
    user = os.environ.get("USER") or ""
    if not user or not shutil.which("loginctl"):
        return False
    try:
        r = subprocess.run(["loginctl", "show-user", user, "-p", "Linger", "--value"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return (r.stdout or "").strip() == "yes"


def _install_systemd(interval_minutes: int) -> int:
    service, timer = _systemd_units(interval_minutes)
    SYSTEMD_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    (SYSTEMD_UNIT_DIR / SERVICE_NAME).write_text(service, encoding="utf-8")
    (SYSTEMD_UNIT_DIR / TIMER_NAME).write_text(timer, encoding="utf-8")

    for args in (("daemon-reload",), ("enable", "--now", TIMER_NAME)):
        r = _systemctl(*args)
        if r.returncode != 0:
            sys.stderr.write(r.stderr or f"systemctl {' '.join(args)} failed\n")
            return r.returncode

    sys.stdout.write(
        f"Installed {TIMER_NAME} — ticking every {interval_minutes} minutes.\n"
        f"  status:  systemctl --user list-timers {TIMER_NAME}\n"
        f"  logs:    journalctl --user -u {SERVICE_NAME}\n"
    )
    # Surviving logout needs linger; without it the timer dies with the session
    # and the tank silently stops again, which is the entire bug.
    if not _linger_enabled():
        sys.stdout.write(
            "\nNOTE: user lingering is OFF, so this timer stops when you log out.\n"
            f"  enable it:  sudo loginctl enable-linger {os.environ.get('USER', '$USER')}\n"
        )
    return 0


def _uninstall_systemd() -> int:
    _systemctl("disable", "--now", TIMER_NAME)
    for name in (TIMER_NAME, SERVICE_NAME):
        (SYSTEMD_UNIT_DIR / name).unlink(missing_ok=True)
    _systemctl("daemon-reload")
    sys.stdout.write(f"Removed {TIMER_NAME} and {SERVICE_NAME}.\n")
    return 0


# ----------------------------------------------------------------------
# Windows backend (unchanged behaviour)
# ----------------------------------------------------------------------

def _run_powershell(script_name: str, extra: list[str] | None = None) -> int:
    script = SCRIPTS_DIR / script_name
    if not script.exists():
        logger.error("install script missing: %s", script)
        return 1
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(script), *(extra or [])],
        capture_output=True, text=True,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def _install_windows() -> int:
    # The scheduled task must run THIS interpreter (the venv where `tank` is
    # installed), not whatever pythonw.exe is first on PATH — that's often the
    # Windows Store shim, which has no `tank` and would fail silently.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    pythonw_arg = str(pythonw) if pythonw.exists() else ""
    return _run_powershell("install-scheduled-task.ps1", ["-PythonW", pythonw_arg])


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def _unsupported(action: str) -> int:
    sys.stderr.write(
        f"tank {action}: no supported scheduler on this platform ({sys.platform}).\n"
        f"Schedule this yourself, every {DEFAULT_INTERVAL_MINUTES} minutes:\n"
        f"    {sys.executable} -m tank tick\n"
    )
    return 1


def install_scheduled_task(interval_minutes: int = DEFAULT_INTERVAL_MINUTES) -> int:
    if _is_windows():
        return _install_windows()
    if _is_linux() and _have_systemd():
        return _install_systemd(interval_minutes)
    return _unsupported("install")


def uninstall_scheduled_task() -> int:
    if _is_windows():
        return _run_powershell("uninstall.ps1")
    if _is_linux() and _have_systemd():
        return _uninstall_systemd()
    return _unsupported("uninstall")


def schedule_status() -> dict:
    """Is anything actually going to tick this world?

    Returns ``{"backend": str, "installed": bool, "detail": str}``. Never raises:
    a status call that blows up is worse than one that says "unknown", because
    the caller is asking precisely because they do not trust what they see.
    """
    if _is_windows():
        try:
            r = subprocess.run(["schtasks", "/Query", "/TN", "fish-tank tick"],
                               capture_output=True, text=True, timeout=15)
            blob = (r.stdout or r.stderr or "").strip()
            return {"backend": "schtasks", "installed": r.returncode == 0,
                    "detail": blob.splitlines()[-1] if blob else ""}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"backend": "schtasks", "installed": False, "detail": f"query failed: {exc}"}

    if _is_linux():
        if not _have_systemd():
            return {"backend": "none", "installed": False,
                    "detail": "no systemd user manager — nothing schedules a tick here"}
        r = _systemctl("is-active", TIMER_NAME)
        active = (r.stdout or "").strip()
        return {"backend": "systemd", "installed": active == "active",
                "detail": f"{TIMER_NAME} is {active or 'unknown'}"}

    return {"backend": "none", "installed": False, "detail": f"unsupported platform {sys.platform}"}
