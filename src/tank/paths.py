"""Resolves on-disk paths under ~/.tank/ (or $TANK_HOME)."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def tank_home() -> Path:
    """Return the tank state directory (~/.tank/ by default, or $TANK_HOME)."""
    override = os.environ.get("TANK_HOME")
    if override:
        return Path(override)
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if not home:
        raise RuntimeError("Cannot resolve home directory (no USERPROFILE/HOME).")
    return Path(home) / ".tank"


def ensure_dirs() -> Path:
    """Create the tank state directory if missing. Returns the path."""
    home = tank_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def first_run_copy(data_dir: Path) -> list[Path]:
    """Copy bundled data files into tank_home() without overwriting user edits.

    Returns the list of files that were actually copied.
    """
    ensure_dirs()
    copied: list[Path] = []
    for src in Path(data_dir).iterdir():
        if not src.is_file():
            continue
        dst = tank_home() / src.name
        if dst.exists():
            continue
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def world_path() -> Path:
    return tank_home() / "world.json"


def graveyard_path() -> Path:
    return tank_home() / "graveyard.jsonl"


def events_path() -> Path:
    return tank_home() / "events.jsonl"


def snapshot_path() -> Path:
    return tank_home() / "tank.txt"


def config_path() -> Path:
    return tank_home() / "default_config.yaml"


def bestiary_path() -> Path:
    return tank_home() / "bestiary.yaml"


def epitaphs_path() -> Path:
    return tank_home() / "epitaphs.yaml"


def log_path() -> Path:
    return tank_home() / "log.txt"


def lock_path() -> Path:
    return tank_home() / "world.lock"


def publish_config_path() -> Path:
    """Optional publish config (JSON). Lets the scheduled task pick up publish
    settings without relying on environment-variable inheritance."""
    return tank_home() / "publish.json"
