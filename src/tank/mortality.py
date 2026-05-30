"""Mortality logic: age, cause-weighted rolls, epitaph rendering."""
from __future__ import annotations

import datetime as dt
import importlib.resources as resources
import logging
from pathlib import Path

import yaml

from tank.bestiary import Species
from tank.models import Death, Event, Fish, HardwareSample, World
from tank.rng import seeded

logger = logging.getLogger(__name__)

CARRYING_CAPACITY = 12
EXTREME_HEAT_C = 85.0


def run(world: World, sample: HardwareSample, events: list[Event],
        now: dt.datetime, species_table: dict[str, Species],
        epitaphs_path: Path | None = None) -> list[Death]:
    templates = _load_templates(epitaphs_path)
    rng = seeded("mortality", world.created_at.isoformat(), now.isoformat())

    deaths: list[Death] = []
    survivors: list[Fish] = []

    has_kernel = any(e.kind == "kernel_error" for e in events)
    extreme_heat = (sample.cpu_temp_c or 0) > EXTREME_HEAT_C \
                   or (sample.gpu_temp_c or 0) > EXTREME_HEAT_C
    oom = sample.memory_pct > 95.0

    for fish in world.fish:
        cause = _determine_cause(fish, sample, has_kernel, extreme_heat, oom, now, rng)
        if cause is None:
            survivors.append(fish)
            continue
        sp = species_table.get(fish.species)
        fossil = sp.fossil_glyph if sp else "·"
        deaths.append(_make_death(fish, cause, fossil, templates, now))

    over = len(survivors) - CARRYING_CAPACITY
    if over > 0:
        survivors.sort(key=lambda f: f.born_at)
        crowded = survivors[:over]
        for fish in crowded:
            sp = species_table.get(fish.species)
            fossil = sp.fossil_glyph if sp else "·"
            deaths.append(_make_death(fish, "crowding", fossil, templates, now))
        survivors = survivors[over:]

    world.fish = survivors
    return deaths


def _determine_cause(fish: Fish, sample: HardwareSample, has_kernel: bool,
                     extreme_heat: bool, oom: bool, now: dt.datetime,
                     rng) -> str | None:
    age_days = (now - fish.born_at).total_seconds() / 86400.0
    if age_days >= fish.lifespan_days:
        return "old_age"
    if oom and rng.random() < 0.4:
        return "oom"
    if has_kernel and rng.random() < 0.5:
        return "kernel_event"
    if extreme_heat and fish.species in {"coldfin", "frostneon"} and rng.random() < 0.5:
        return "thermal_shock"
    if extreme_heat and rng.random() < 0.05:
        return "thermal_shock"
    return None


def _make_death(fish: Fish, cause: str, fossil: str,
                templates: dict, now: dt.datetime) -> Death:
    tmpl = (templates.get(cause, {}) or {}).get(fish.species) \
        or (templates.get(cause, {}) or {}).get("default") \
        or "{name} ({species}) — {died_short}, cause: {cause}"
    age_days = max(0, int((now - fish.born_at).total_seconds() / 86400.0))
    epitaph = tmpl.format(
        name=fish.name,
        species=fish.species,
        project=fish.project or "",
        born_short=fish.born_at.date().isoformat(),
        died_short=now.date().isoformat(),
        age_days=age_days,
        cause=cause,
    )
    return Death(
        fish_id=fish.id, name=fish.name, species=fish.species,
        born_at=fish.born_at, died_at=now, cause=cause,
        epitaph=epitaph, fossil_glyph=fossil,
    )


def _load_templates(path: Path | None) -> dict:
    if path and Path(path).exists():
        try:
            return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("epitaphs load failed (%s); using bundled", e)
    text = resources.files("tank").joinpath("data/epitaphs.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}
