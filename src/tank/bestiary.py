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


_DEFAULTS = {
    "category": "common",
    "glyph_pool": [">°))<"],
    "fossil_glyph": "·",
    "base_lifespan_days": [7, 30],
    "spawn_trigger": "bestiary_roll",
    "spawn_weight": 1.0,
    "mood_bias": {"calm": 1.0},
    "description": "",
}


def _coerce(key: str, raw: dict | None) -> Species:
    merged = {**_DEFAULTS, **(raw or {})}
    lifespan = merged["base_lifespan_days"]
    return Species(
        key=key,
        category=merged["category"],
        glyph_pool=list(merged["glyph_pool"]),
        fossil_glyph=merged["fossil_glyph"],
        base_lifespan_days=(int(lifespan[0]), int(lifespan[1])),
        spawn_trigger=merged["spawn_trigger"],
        spawn_weight=float(merged["spawn_weight"]),
        mood_bias=dict(merged["mood_bias"]),
        description=merged["description"],
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
        return {k: _coerce(k, v) for k, v in raw.items()}
    except Exception as e:
        logger.warning("bestiary load failed (%s); falling back to bundled", e)
        return load_bundled()
