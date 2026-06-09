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


# Only these bundled files are user-editable knobs worth seeding into ~/.tank/.
# (default_config.yaml is a sample, not loaded — keep it bundled-only.)
_SEED_FILES = ("bestiary.yaml", "epitaphs.yaml")


def seed_user_data() -> list[Path]:
    """Seed editable copies of the bundled bestiary/epitaphs into ~/.tank/.

    The README tells users to edit ``~/.tank/bestiary.yaml`` and
    ``~/.tank/epitaphs.yaml``; without this they'd have to hand-create those
    files before the override loaders (bestiary.load / mortality templates)
    could pick them up. Best-effort and idempotent: existing user edits are
    never overwritten, and a missing/odd package layout never blocks a tick.
    Returns the list of files actually written.
    """
    import importlib.resources as resources

    ensure_dirs()
    copied: list[Path] = []
    try:
        data_root = resources.files("tank").joinpath("data")
        for name in _SEED_FILES:
            dst = tank_home() / name
            if dst.exists():
                continue
            src = data_root.joinpath(name)
            text = src.read_text(encoding="utf-8")
            dst.write_text(text, encoding="utf-8")
            copied.append(dst)
    except Exception:
        # Seeding is a convenience, never load-bearing — a packaging quirk
        # must not break the tick. The override loaders fall back to bundled.
        return copied
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


def config_yaml_path() -> Path:
    """Optional user config (YAML) at ~/.tank/config.yaml (or $TANK_HOME).

    Holds the observer allow-list and path overrides. See Observer.from_config().
    """
    return tank_home() / "config.yaml"


def epitaphs_path() -> Path:
    return tank_home() / "epitaphs.yaml"


def last_crash_path() -> Path:
    """Dedup marker for the Windows crash detector (see tank.crashsense).

    Holds the UTC-ISO timestamp of the most-recent machine crash already turned
    into a kernel_error event, so a crash is never re-spawned across ticks. This
    is a DEDICATED file deliberately kept OUT of the world.json serdes schema —
    a prior schema change caused a quarantine incident, so crash dedup state
    lives on its own here instead of in the persisted World."""
    return tank_home() / "last_crash"


def log_path() -> Path:
    return tank_home() / "log.txt"


def lock_path() -> Path:
    return tank_home() / "world.lock"


def publish_config_path() -> Path:
    """Optional publish config (JSON). Lets the scheduled task pick up publish
    settings without relying on environment-variable inheritance."""
    return tank_home() / "publish.json"
