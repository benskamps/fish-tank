"""Subprocess helpers must suppress console windows on Windows (no flashing)."""
from __future__ import annotations

import os
import subprocess

from tank import proc


def test_no_window_constant():
    if os.name == "nt":
        assert proc.NO_WINDOW == subprocess.CREATE_NO_WINDOW
    else:
        assert proc.NO_WINDOW == 0


def test_check_output_injects_creationflags(monkeypatch):
    captured = {}

    def fake(args, **kw):
        captured.update(kw)
        return ""

    monkeypatch.setattr(subprocess, "check_output", fake)
    proc.check_output(["git", "--version"])
    if os.name == "nt":
        assert captured.get("creationflags") == subprocess.CREATE_NO_WINDOW
    else:
        assert "creationflags" not in captured


def test_run_injects_creationflags(monkeypatch):
    captured = {}

    def fake(args, **kw):
        captured.update(kw)
        return None

    monkeypatch.setattr(subprocess, "run", fake)
    proc.run(["git", "--version"])
    if os.name == "nt":
        assert captured.get("creationflags") == subprocess.CREATE_NO_WINDOW
    else:
        assert "creationflags" not in captured


def test_caller_creationflags_not_overridden(monkeypatch):
    # setdefault: an explicit creationflags from the caller wins.
    captured = {}
    monkeypatch.setattr(subprocess, "check_output", lambda a, **kw: captured.update(kw) or "")
    proc.check_output(["x"], creationflags=123)
    assert captured["creationflags"] == 123
