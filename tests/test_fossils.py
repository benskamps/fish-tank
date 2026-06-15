"""Fossil substrate + epitaph snapshot tests for the aquascape floor.

Two documented behaviors are asserted here:

  1. Fossils *accrete* on the floor — when fish die, their fossil glyph is
     appended to ``world.weather.fossil_layer`` (newest last), the layer is
     capped at ``FOSSIL_LAYER_MAX``, and the floor row rendered by
     ``_make_fossils`` carries those glyphs.
  2. Epitaph text is *stable* — rendered from a fixed template + fixed fish it
     is deterministic, so a snapshot of the floor's epitaphs survives refactors.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from tank.bestiary import load_bundled
from tank.models import Fish, HardwareSample, Weather, World
from tank.mortality import run as mortality_run
from tank.render.frame import _make_fossils
from tank.tick import FOSSIL_LAYER_MAX

FIXTURES = Path(__file__).parent / "fixtures"


def _world(now, fish, fossils=None):
    return World(
        schema_version=1, created_at=now, last_tick_at=now,
        fish=list(fish),
        weather=Weather(20.0, 0.0, 0.0, 0.5, 0.0, list(fossils or [])),
        seen_commits={}, seen_notes=set(), seen_projects=set(),
        config_overrides={},
    )


def _sample(**kw):
    base = dict(
        cpu_temp_c=40.0, gpu_temp_c=50.0, cpu_load_pct=10.0, gpu_load_pct=10.0,
        memory_pct=30.0, idle_seconds=0, uptime_seconds=3600,
        sources_used=["test"], degraded=False,
    )
    base.update(kw)
    return HardwareSample(**base)


def _make_fish(species="guppy", lifespan=15.0, age_days=10, fish_id="abc",
               name=None, now=None):
    base_now = now or dt.datetime(2026, 5, 14, 22, 0, 0, tzinfo=dt.timezone.utc)
    return Fish(
        id=fish_id, name=name or f"Pip-{fish_id}", species=species,
        glyph=">°))<",
        born_at=base_now - dt.timedelta(days=age_days),
        lifespan_days=lifespan, provenance="test",
        project=None, mood="calm", last_position=(0, 0),
    )


# --- fossil substrate: fossils accrete on the floor ------------------------

def _accrete(world, deaths):
    """Mirror the tick orchestrator's fossil-accretion step (tick.py)."""
    for d in deaths:
        world.weather.fossil_layer.append(d.fossil_glyph)
    world.weather.fossil_layer = world.weather.fossil_layer[-FOSSIL_LAYER_MAX:]


def test_death_deposits_fossil_glyph_on_floor(fixed_now):
    species = load_bundled()
    dead = _make_fish(lifespan=2.0, age_days=5, now=fixed_now)
    world = _world(fixed_now, [dead])
    deaths = mortality_run(world, _sample(), [], fixed_now, species,
                           epitaphs_path=None)
    assert deaths
    _accrete(world, deaths)
    # The fossil glyph the death carried now sits in the floor layer.
    assert world.weather.fossil_layer[-1] == deaths[0].fossil_glyph
    assert len(world.weather.fossil_layer) == 1


def test_fossils_accrete_newest_last(fixed_now):
    world = _world(fixed_now, [], fossils=["·", "✦"])
    species = load_bundled()
    later = _make_fish(lifespan=2.0, age_days=5, fish_id="z", now=fixed_now)
    world.fish = [later]
    deaths = mortality_run(world, _sample(), [], fixed_now, species,
                           epitaphs_path=None)
    _accrete(world, deaths)
    # Prior fossils are preserved; the new one is appended at the end.
    assert world.weather.fossil_layer[:2] == ["·", "✦"]
    assert world.weather.fossil_layer[-1] == deaths[0].fossil_glyph


def test_fossil_layer_caps_at_max(fixed_now):
    # Start already at the cap, then deposit one more.
    world = _world(fixed_now, [], fossils=["·"] * FOSSIL_LAYER_MAX)
    species = load_bundled()
    world.fish = [_make_fish(lifespan=2.0, age_days=5, now=fixed_now)]
    deaths = mortality_run(world, _sample(), [], fixed_now, species,
                           epitaphs_path=None)
    _accrete(world, deaths)
    assert len(world.weather.fossil_layer) == FOSSIL_LAYER_MAX
    # The newest fossil is retained; the oldest fell off the front.
    assert world.weather.fossil_layer[-1] == deaths[0].fossil_glyph


def test_floor_row_carries_accreted_fossils():
    width = 56
    floor = _make_fossils(["·", "✦", "▒"], width)
    assert "·" in floor and "✦" in floor and "▒" in floor
    assert len(floor) == width - 2          # floor spans the tank interior


def test_empty_floor_is_blank_substrate():
    width = 56
    floor = _make_fossils([], width)
    assert floor == " " * (width - 2)


# --- epitaph snapshot: epitaph text is stable ------------------------------

def test_epitaph_floor_snapshot(fixed_now):
    """Snapshot the epitaph text for a fixed cohort of deaths.

    Epitaphs are rendered from a fixed template (bundled epitaphs.yaml) and
    fixed fish, so the floor's epitaph block is deterministic. Write-if-missing
    then assert-equal, matching the frame snapshot convention.
    """
    species = load_bundled()
    cohort = [
        _make_fish(species="guppy", name="Pip", lifespan=2.0, age_days=5,
                   fish_id="a", now=fixed_now),
        _make_fish(species="coldfin", name="Marlow", lifespan=2.0, age_days=9,
                   fish_id="b", now=fixed_now),
    ]
    world = _world(fixed_now, cohort)
    deaths = mortality_run(world, _sample(), [], fixed_now, species,
                           epitaphs_path=None)
    block = "\n".join(d.epitaph for d in sorted(deaths, key=lambda d: d.name))

    fixture = FIXTURES / "epitaphs_floor.txt"
    if not fixture.exists():
        FIXTURES.mkdir(parents=True, exist_ok=True)
        fixture.write_text(block + "\n", encoding="utf-8")
    assert block + "\n" == fixture.read_text(encoding="utf-8")
