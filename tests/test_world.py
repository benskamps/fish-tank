import datetime as dt

import pytest

from tank.models import Weather, World
from tank.world import WorldStore


def _fresh_world(now: dt.datetime) -> World:
    return World(
        schema_version=1,
        created_at=now,
        last_tick_at=now,
        fish=[],
        weather=Weather(20.0, 0.0, 0.0, 0.5, 0.0, []),
        seen_commits={},
        seen_seals=set(),
        seen_projects=set(),
        config_overrides={},
    )


def test_load_or_init_creates_fresh_world_when_missing(tmp_tank_dir, fixed_now):
    store = WorldStore()
    world = store.load_or_init(now=fixed_now)
    assert world.schema_version == 1
    assert world.fish == []
    assert world.created_at == fixed_now


def test_save_then_load_roundtrips(tmp_tank_dir, fixed_now):
    store = WorldStore()
    world = _fresh_world(fixed_now)
    store.save(world)
    again = store.load_or_init(now=fixed_now)
    assert again == world


def test_save_is_atomic_no_tmp_left_behind(tmp_tank_dir, fixed_now):
    store = WorldStore()
    store.save(_fresh_world(fixed_now))
    leftovers = list(tmp_tank_dir.glob("world.json.tmp*"))
    assert leftovers == []


def test_corrupt_world_recovers_and_archives(tmp_tank_dir, fixed_now):
    from tank import paths

    paths.world_path().write_text("{not json")
    store = WorldStore()
    world = store.load_or_init(now=fixed_now)
    assert world.fish == []
    broken = list(tmp_tank_dir.glob("world.json.broken-*"))
    assert len(broken) == 1
    assert broken[0].read_text() == "{not json"


def test_lock_blocks_concurrent_writers(tmp_tank_dir, fixed_now):
    store = WorldStore()
    with store.lock(timeout=0.1):
        with pytest.raises(TimeoutError):
            with store.lock(timeout=0.1):
                pass
