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
        seen_notes=set(),
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
    broken = [p for p in tmp_tank_dir.glob("world.json.broken-*")
              if not p.name.endswith(".why.txt")]
    assert len(broken) == 1
    assert broken[0].read_text() == "{not json"


def test_lock_blocks_concurrent_writers(tmp_tank_dir, fixed_now):
    store = WorldStore()
    with store.lock(timeout=0.1):
        with pytest.raises(TimeoutError):
            with store.lock(timeout=0.1):
                pass


def test_stale_lock_is_reclaimed(tmp_tank_dir):
    """A lock file left by a crashed tick must not freeze the tank forever."""
    import os
    import time
    from tank import paths
    from tank.world import STALE_LOCK_S

    lock_file = paths.lock_path()
    paths.ensure_dirs()
    lock_file.write_text("99999 old")  # pretend a dead process owns it
    old = time.time() - (STALE_LOCK_S + 60)
    os.utime(lock_file, (old, old))

    store = WorldStore()
    acquired = False
    with store.lock(timeout=0.5):  # would TimeoutError if not reclaimed
        acquired = True
    assert acquired
    assert not lock_file.exists()  # released cleanly afterward


def test_lock_writes_owner_pid(tmp_tank_dir):
    import os
    from tank import paths
    store = WorldStore()
    with store.lock(timeout=0.5):
        content = paths.lock_path().read_text()
    assert content.split()[0] == str(os.getpid())
