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

#: Fish put in the tank on purpose rather than spawned by a bestiary roll.
#: `tank adopt` stamps this (cli.py), and it is the whole difference between an
#: inhabitant and a passer-by.
ADOPTED = "manual:adopt"


def is_resident(fish: Fish) -> bool:
    """An adopted fish. Exempt from the crowding cull, never from anything else.

    Measured on 2026-08-31, the day the tank got a real heartbeat: one tick
    caught up three days of estate activity and spawned 78 fish at once, the next
    tick culled 67 for crowding, and the cull sorts oldest-first — so the very
    first fish it killed was Ember, adopted three days earlier and the oldest
    thing in the water. `lifespan_days=36500` had protected them from age and
    from nothing else.

    An adopted resident is not competing for the tank's carrying capacity; they
    ARE the tank's reason for existing. Crowding is a pressure on the population
    that drifts in, so residents are excluded from it and from the count it culls
    against. Every other cause of death still applies to them: this is not
    immortality, it is not being evicted by a crowd that arrived later.
    """
    return str(getattr(fish, "provenance", "") or "").startswith(ADOPTED)


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

    # Residents sit out the crowding cull entirely — they are not part of the
    # population that overcrowds, so they are neither counted nor culled.
    residents = [f for f in survivors if is_resident(f)]
    transient = [f for f in survivors if not is_resident(f)]

    over = len(transient) - CARRYING_CAPACITY
    if over > 0:
        transient.sort(key=lambda f: f.born_at)
        for fish in transient[:over]:
            sp = species_table.get(fish.species)
            fossil = sp.fossil_glyph if sp else "·"
            deaths.append(_make_death(fish, "crowding", fossil, templates, now))
        transient = transient[over:]

    world.fish = residents + transient
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
