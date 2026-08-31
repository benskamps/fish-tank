"""Scheduler install/uninstall, per platform.

Every test here pins `sys.platform` explicitly. The originals did not, and passed
on Linux only because `subprocess.run` was monkeypatched — they asserted the
PowerShell path was taken on a box where PowerShell does not exist, which is how
a Windows-only installer sat under a green suite while the tank never ticked.
"""
import subprocess

import pytest

from tank import install as install_mod


class _R:
    """Stand-in for CompletedProcess."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def calls(monkeypatch):
    """Record every subprocess.run and answer plausibly."""
    seen = []

    def fake_run(args, **kw):
        seen.append(list(args))
        if "is-system-running" in args:
            return _R(0, "running\n")
        if "show-user" in args:
            return _R(0, "yes\n")
        if "is-active" in args:
            return _R(0, "active\n")
        return _R(0)

    monkeypatch.setattr(install_mod.subprocess, "run", fake_run)
    return seen


# ---------------------------------------------------------------- Windows

def test_install_calls_powershell_on_windows(monkeypatch, calls):
    monkeypatch.setattr(install_mod.sys, "platform", "win32")
    assert install_mod.install_scheduled_task() == 0
    assert any("install-scheduled-task.ps1" in " ".join(c) for c in calls)


def test_uninstall_calls_powershell_on_windows(monkeypatch, calls):
    monkeypatch.setattr(install_mod.sys, "platform", "win32")
    assert install_mod.uninstall_scheduled_task() == 0
    assert any("uninstall.ps1" in " ".join(c) for c in calls)


# ---------------------------------------------------------------- Linux

def test_install_writes_units_and_enables_timer(monkeypatch, calls, tmp_path):
    monkeypatch.setattr(install_mod.sys, "platform", "linux")
    monkeypatch.setattr(install_mod, "SYSTEMD_UNIT_DIR", tmp_path)
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: "/usr/bin/systemctl")

    assert install_mod.install_scheduled_task() == 0

    service = (tmp_path / install_mod.SERVICE_NAME).read_text()
    timer = (tmp_path / install_mod.TIMER_NAME).read_text()
    # The unit must pin THIS interpreter, not a bare `tank` off the unit's PATH.
    assert f"ExecStart={install_mod.sys.executable} -m tank tick" in service
    assert "OnUnitActiveSec=10min" in timer
    assert "Persistent=true" in timer
    assert ["systemctl", "--user", "enable", "--now", install_mod.TIMER_NAME] in calls
    # PowerShell must never be reached on Linux.
    assert not any("powershell" in " ".join(c) for c in calls)


def test_install_honours_interval(monkeypatch, calls, tmp_path):
    monkeypatch.setattr(install_mod.sys, "platform", "linux")
    monkeypatch.setattr(install_mod, "SYSTEMD_UNIT_DIR", tmp_path)
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: "/usr/bin/systemctl")

    install_mod.install_scheduled_task(interval_minutes=5)
    assert "OnUnitActiveSec=5min" in (tmp_path / install_mod.TIMER_NAME).read_text()


def test_uninstall_removes_units(monkeypatch, calls, tmp_path):
    monkeypatch.setattr(install_mod.sys, "platform", "linux")
    monkeypatch.setattr(install_mod, "SYSTEMD_UNIT_DIR", tmp_path)
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: "/usr/bin/systemctl")
    (tmp_path / install_mod.SERVICE_NAME).write_text("x")
    (tmp_path / install_mod.TIMER_NAME).write_text("x")

    assert install_mod.uninstall_scheduled_task() == 0
    assert not (tmp_path / install_mod.SERVICE_NAME).exists()
    assert not (tmp_path / install_mod.TIMER_NAME).exists()


def test_install_refuses_without_systemd(monkeypatch, capsys):
    """No systemd -> say so and hand back the command. Never a silent cron."""
    monkeypatch.setattr(install_mod.sys, "platform", "linux")
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: None)

    assert install_mod.install_scheduled_task() == 1
    err = capsys.readouterr().err
    assert "no supported scheduler" in err
    assert "-m tank tick" in err
    assert "cron" not in err.lower()


def test_no_user_bus_is_not_systemd(monkeypatch):
    """systemctl exists but there is no user manager (containers, WSL)."""
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: "/usr/bin/systemctl")

    def boom(args, **kw):
        raise OSError("Failed to connect to bus")

    monkeypatch.setattr(install_mod.subprocess, "run", boom)
    assert install_mod._have_systemd() is False


def test_timeout_is_not_systemd(monkeypatch):
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: "/usr/bin/systemctl")

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, 10)

    monkeypatch.setattr(install_mod.subprocess, "run", slow)
    assert install_mod._have_systemd() is False


# ---------------------------------------------------------------- status

def test_status_reports_active_timer(monkeypatch, calls):
    monkeypatch.setattr(install_mod.sys, "platform", "linux")
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: "/usr/bin/systemctl")
    st = install_mod.schedule_status()
    assert st == {"backend": "systemd", "installed": True,
                  "detail": f"{install_mod.TIMER_NAME} is active"}


def test_status_says_nothing_ticks_here(monkeypatch):
    """The whole point: a world nothing schedules must SAY nothing schedules it."""
    monkeypatch.setattr(install_mod.sys, "platform", "linux")
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: None)
    st = install_mod.schedule_status()
    assert st["installed"] is False
    assert st["backend"] == "none"
    assert "nothing schedules" in st["detail"]


def test_status_never_raises(monkeypatch):
    monkeypatch.setattr(install_mod.sys, "platform", "linux")
    monkeypatch.setattr(install_mod.shutil, "which", lambda _: "/usr/bin/systemctl")

    def boom(args, **kw):
        raise OSError("bus is gone")

    monkeypatch.setattr(install_mod.subprocess, "run", boom)
    st = install_mod.schedule_status()  # must not raise
    assert st["installed"] is False
