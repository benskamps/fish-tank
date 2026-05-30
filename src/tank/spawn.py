"""Spawn logic: event-driven births + bestiary rolls based on weather."""
from __future__ import annotations

import datetime as dt
import uuid

from tank import circadian
from tank.bestiary import Species
from tank.models import Event, Fish, HardwareSample, World
from tank.rng import seeded

EVENT_TO_TRIGGER = {
    "ship": "ship_event",
    "new_project": "new_project",
    "seal_written": "seal_event",
    "commit": "commit_event",
    "kernel_error": "kernel_event",
}

NIGHT_FISH = "night-fish"
WITCHING_SPAWN_PROB = 0.15


def run(world: World, events: list[Event], sample: HardwareSample,
        now: dt.datetime, species_table: dict[str, Species]) -> list[Fish]:
    births: list[Fish] = []
    births.extend(_event_births(world, events, now, species_table))
    births.extend(_bestiary_rolls(world, sample, now, species_table))
    births.extend(_witching_rolls(world, now, species_table))
    return births


def _witching_rolls(world: World, now: dt.datetime,
                    species_table: dict[str, Species]) -> list[Fish]:
    """The night-fish: only surfaces in the witching hour, only one at a time."""
    if not circadian.is_witching(now):
        return []
    if any(f.species == NIGHT_FISH for f in world.fish):
        return []
    candidates = [s for s in species_table.values()
                  if s.spawn_trigger == "witching"]
    if not candidates:
        return []
    rng = seeded("witching", world.created_at.isoformat(), now.isoformat())
    if rng.random() >= WITCHING_SPAWN_PROB:
        return []
    sp = rng.choices(candidates, weights=[c.spawn_weight for c in candidates])[0]
    return [_make_fish(sp, rng, None, now)]


def submerge_dawn(fish: list[Fish], now: dt.datetime) -> list[Fish]:
    """Night-fish vanish outside the witching hour — they submerge, they do not
    die. No graveyard entry, no fossil. Just not there in daylight."""
    if circadian.is_witching(now):
        return list(fish)
    return [f for f in fish if f.species != NIGHT_FISH]


def _event_births(world: World, events: list[Event], now: dt.datetime,
                  species_table: dict[str, Species]) -> list[Fish]:
    out: list[Fish] = []
    by_trigger: dict[str, list[Species]] = {}
    for sp in species_table.values():
        by_trigger.setdefault(sp.spawn_trigger, []).append(sp)

    for ev in events:
        trigger = EVENT_TO_TRIGGER.get(ev.kind)
        if not trigger:
            continue
        candidates = by_trigger.get(trigger, [])
        if not candidates:
            continue
        rng = seeded("event", trigger, ev.detail, world.created_at.isoformat())
        sp = rng.choices(candidates, weights=[c.spawn_weight for c in candidates])[0]
        out.append(_make_fish(sp, rng, ev, now))
    return out


def _bestiary_rolls(world: World, sample: HardwareSample, now: dt.datetime,
                    species_table: dict[str, Species]) -> list[Fish]:
    out: list[Fish] = []
    rng = seeded("bestiary", world.created_at.isoformat(),
                 now.isoformat())

    if (sample.cpu_temp_c is not None and sample.cpu_temp_c < 30.0
            and world.weather.temperature_c < 30.0):
        if rng.random() < 0.20:
            cold = [s for s in species_table.values()
                    if s.spawn_trigger == "cold_sustained"]
            if cold:
                sp = rng.choices(cold, weights=[c.spawn_weight for c in cold])[0]
                out.append(_make_fish(sp, rng, None, now))

    if (sample.gpu_load_pct is not None and sample.gpu_load_pct > 80.0
            and world.weather.pressure > 0.5):
        if rng.random() < 0.20:
            hot = [s for s in species_table.values()
                   if s.spawn_trigger == "heat_sustained"]
            if hot:
                sp = rng.choices(hot, weights=[c.spawn_weight for c in hot])[0]
                out.append(_make_fish(sp, rng, None, now))

    if len(world.fish) < _carrying_capacity(world):
        if rng.random() < 0.10:
            common = [s for s in species_table.values()
                      if s.spawn_trigger == "bestiary_roll"]
            if common:
                sp = rng.choices(common, weights=[c.spawn_weight for c in common])[0]
                out.append(_make_fish(sp, rng, None, now))

    return out


def _carrying_capacity(world: World) -> int:
    return 12


def _make_fish(sp: Species, rng, event: Event | None,
               now: dt.datetime) -> Fish:
    fish_id = uuid.uuid4().hex[:8]
    glyph = rng.choice(sp.glyph_pool)
    lifespan = rng.uniform(*sp.base_lifespan_days)
    mood = rng.choices(list(sp.mood_bias.keys()),
                       weights=list(sp.mood_bias.values()))[0]

    if event and event.project:
        name = f"{event.project}-{sp.key}"
        provenance = f"event:{event.kind}:{event.project}:{event.detail}"
        project = event.project
    elif event:
        name = f"{sp.key}-{event.detail[:8]}"
        provenance = f"event:{event.kind}:{event.detail}"
        project = None
    else:
        name = f"{sp.key}-{fish_id[:4]}"
        provenance = f"bestiary:{sp.key}"
        project = None

    return Fish(
        id=fish_id, name=name, species=sp.key, glyph=glyph,
        born_at=now, lifespan_days=lifespan, provenance=provenance,
        project=project, mood=mood, last_position=(0, 0),
    )
