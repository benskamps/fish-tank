"""The night-fish — witching-hour spawn + silent dawn submerge."""
from __future__ import annotations

import datetime as dt

from tank.bestiary import load_bundled
from tank.models import Fish, Weather, World
from tank.spawn import NIGHT_FISH, run as spawn_run, submerge_dawn


def _world(fish=None, created="2026-01-01T00:00:00+00:00"):
    return World(
        schema_version=1,
        created_at=dt.datetime.fromisoformat(created),
        last_tick_at=dt.datetime.fromisoformat(created),
        fish=fish or [],
        weather=Weather(22.0, 0.0, 0.0, 0.5, 0.0, []),
        seen_commits={}, seen_seals=set(), seen_projects=set(),
    )


def _fish(species="guppy"):
    return Fish(
        id="x", name="x", species=species, glyph="><>",
        born_at=dt.datetime(2026, 5, 29), lifespan_days=10.0,
        provenance="test", project=None, mood="calm", last_position=(0, 0),
    )


def _local(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 5, 29, hour, minute)


SPECIES = load_bundled()


def test_bestiary_includes_night_fish():
    assert NIGHT_FISH in SPECIES
    assert SPECIES[NIGHT_FISH].spawn_trigger == "witching"


def test_no_night_fish_outside_witching():
    # Noon: a witching roll must never produce a night-fish.
    for minute in range(0, 60):
        births = spawn_run(_world(), [], _sample(), _local(12, minute), SPECIES)
        assert all(b.species != NIGHT_FISH for b in births)


def test_night_fish_can_spawn_during_witching():
    # Across the witching window, at least one tick should surface a night-fish,
    # and every night-fish surfaced is the right species.
    spawned = 0
    for minute in range(0, 180):  # 00:00–02:59
        hour, mi = divmod(minute, 60)
        births = spawn_run(_world(), [], _sample(), _local(hour, mi), SPECIES)
        nf = [b for b in births if b.species == NIGHT_FISH]
        spawned += len(nf)
    assert spawned >= 1


def test_only_one_night_fish_at_a_time():
    world = _world(fish=[_fish(NIGHT_FISH)])
    for minute in range(0, 180):
        hour, mi = divmod(minute, 60)
        births = spawn_run(world, [], _sample(), _local(hour, mi), SPECIES)
        assert all(b.species != NIGHT_FISH for b in births)


def test_submerge_removes_night_fish_in_daylight():
    fish = [_fish("guppy"), _fish(NIGHT_FISH), _fish("tetra")]
    out = submerge_dawn(fish, _local(8))  # daytime
    assert [f.species for f in out] == ["guppy", "tetra"]


def test_submerge_keeps_night_fish_during_witching():
    fish = [_fish("guppy"), _fish(NIGHT_FISH)]
    out = submerge_dawn(fish, _local(1))  # witching
    assert any(f.species == NIGHT_FISH for f in out)


def _sample():
    from tank.models import HardwareSample
    return HardwareSample(
        cpu_temp_c=40.0, gpu_temp_c=50.0, cpu_load_pct=10.0, gpu_load_pct=20.0,
        memory_pct=30.0, idle_seconds=0, uptime_seconds=3600,
        sources_used=["test"], degraded=False,
    )
