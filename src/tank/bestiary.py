"""Loads species definitions from bestiary.yaml with bundled fallback."""
from __future__ import annotations

import importlib.resources as resources
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Species:
    key: str
    category: str
    glyph_pool: list[str]
    fossil_glyph: str
    base_lifespan_days: tuple[int, int]
    spawn_trigger: str
    spawn_weight: float
    mood_bias: dict[str, float]
    description: str
    zone: str = "mid"       # surface / mid / bottom — vertical habitat
    social: str = "solo"    # solo / school — schools cluster together


_DEFAULTS = {
    "category": "common",
    "glyph_pool": [">°))<"],
    "fossil_glyph": "·",
    "base_lifespan_days": [7, 30],
    "spawn_trigger": "bestiary_roll",
    "spawn_weight": 1.0,
    "mood_bias": {"calm": 1.0},
    "description": "",
    "zone": "mid",
    "social": "solo",
}

_VALID_ZONES = {"surface", "mid", "bottom"}


def _coerce_lifespan(raw) -> tuple[int, int]:
    """Normalize base_lifespan_days from a possibly-malformed user override.

    Accepts a [lo, hi] pair, a single number, or a scalar; always returns a
    sane (lo, hi) with lo <= hi so rng.uniform(*pair) can never raise. A bad
    override should degrade to the default span, not crash the headless tick.
    """
    try:
        if isinstance(raw, (list, tuple)):
            nums = [int(x) for x in raw[:2]]
        else:
            nums = [int(raw)]
    except (TypeError, ValueError):
        nums = []
    if not nums:
        nums = list(_DEFAULTS["base_lifespan_days"])
    if len(nums) == 1:
        nums = [nums[0], nums[0]]
    lo, hi = nums[0], nums[1]
    return (lo, hi) if lo <= hi else (hi, lo)


def _coerce(key: str, raw: dict | None) -> Species:
    merged = {**_DEFAULTS, **(raw or {})}
    zone = merged["zone"] if merged["zone"] in _VALID_ZONES else "mid"
    # A species with an empty mood_bias would make rng.choices([]) raise and
    # crash the whole (headless) tick. An empty glyph_pool would make
    # rng.choice([]) do the same. Backfill both so a sparse user override is
    # always spawn-safe.
    mood_bias = merged["mood_bias"] if merged["mood_bias"] else _DEFAULTS["mood_bias"]
    glyph_pool = list(merged["glyph_pool"]) or list(_DEFAULTS["glyph_pool"])
    return Species(
        key=key,
        category=merged["category"],
        glyph_pool=glyph_pool,
        fossil_glyph=merged["fossil_glyph"],
        base_lifespan_days=_coerce_lifespan(merged["base_lifespan_days"]),
        spawn_trigger=merged["spawn_trigger"],
        spawn_weight=float(merged["spawn_weight"]),
        mood_bias=dict(mood_bias),
        description=merged["description"],
        zone=zone,
        social=merged["social"],
    )


def load_bundled() -> dict[str, Species]:
    """Load the bestiary that ships with the package."""
    text = resources.files("tank").joinpath("data/bestiary.yaml").read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    return {k: _coerce(k, v) for k, v in raw.items()}


def load_bestiary(path: Path | None = None) -> dict[str, Species]:
    """Load bestiary from a YAML path; fall back to bundled if path is bad."""
    if path is None:
        return load_bundled()
    try:
        text = Path(path).read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ValueError("bestiary.yaml must be a mapping")
        if not raw:
            # An empty override file is almost certainly a mistake, not an
            # intentional "zero species" world (which would brick every spawn).
            raise ValueError("bestiary.yaml is empty")
        return {k: _coerce(k, v) for k, v in raw.items()}
    except FileNotFoundError:
        # No override on disk is the normal case — fall back silently.
        return load_bundled()
    except Exception as e:
        logger.warning("bestiary load failed (%s); falling back to bundled", e)
        return load_bundled()


def load() -> dict[str, Species]:
    """The override-aware loader every runtime caller should use.

    Honors an editable ``~/.tank/bestiary.yaml`` (or ``$TANK_HOME``) when present,
    falling back to the bundled bestiary otherwise. This is what makes the
    README promise true: edit the file, and the next tick picks up your changes.
    """
    # Imported here (not at module top) to avoid a paths<->bestiary import cycle.
    from tank import paths
    return load_bestiary(paths.bestiary_path())
