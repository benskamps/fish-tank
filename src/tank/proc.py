"""Subprocess helpers that don't flash console windows on Windows.

When a windowless parent (pythonw.exe, i.e. the Scheduled Task) spawns a console
program like `git` or `nvidia-smi`, Windows pops a console window for each child
— with ~20 git repos scanned per tick, that's a storm of flashing windows every
few minutes. Passing CREATE_NO_WINDOW suppresses it. No-op on non-Windows.
"""
from __future__ import annotations

import os
import subprocess

# 0x08000000 = CREATE_NO_WINDOW. Defined only on Windows; 0 elsewhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def check_output(args, **kwargs):
    """subprocess.check_output that never flashes a console window on Windows."""
    if NO_WINDOW:
        kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.check_output(args, **kwargs)


def run(args, **kwargs):
    """subprocess.run that never flashes a console window on Windows."""
    if NO_WINDOW:
        kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(args, **kwargs)
