import datetime as dt

from tank.bestiary import load_bundled
from tank.models import Event, HardwareSample, Weather, World
from tank.spawn import run as spawn_run


def _world(now):
    return World(
        schema_version=1,
        created_at=now, last_tick_at=now,
        fish=[],
        weather=Weather(40.0, 0.0, 0.0, 0.5, 0.0, []),
        seen_commits={}, seen_seals=set(), seen_projects=set(),
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


def test_ship_event_spawns_shipfish_named_after_project(fixed_now):
    species = load_bundled()
    events = [Event(kind="ship", project="my-app",
                    detail="abc123", at=fixed_now)]
    births = spawn_run(_world(fixed_now), events, _sample(), fixed_now, species)
    ship = [f for f in births if f.species == "shipfish"]
    assert len(ship) == 1
    assert "my-app" in ship[0].name


def test_new_project_event_spawns_founderfish(fixed_now):
    species = load_bundled()
    events = [Event(kind="new_project", project="newthing",
                    detail="/path", at=fixed_now)]
    births = spawn_run(_world(fixed_now), events, _sample(), fixed_now, species)
    assert any(f.species == "founderfish" for f in births)


def test_seal_event_spawns_witnessfish(fixed_now):
    species = load_bundled()
    events = [Event(kind="seal_written", project=None,
                    detail="2026-05-14-seal.md", at=fixed_now)]
    births = spawn_run(_world(fixed_now), events, _sample(), fixed_now, species)
    assert any(f.species == "witnessfish" for f in births)


def test_cold_sustained_triggers_coldfin(fixed_now):
    species = load_bundled()
    world = _world(fixed_now)
    world.weather = Weather(25.0, 0.0, 0.0, 0.5, 0.0, [])
    rolled = False
    for tick in range(30):
        world.created_at = fixed_now + dt.timedelta(seconds=tick)
        births = spawn_run(world, [], _sample(cpu_temp_c=22.0),
                           fixed_now, species)
        if any(f.species in {"coldfin", "frostneon"} for f in births):
            rolled = True
            break
    assert rolled


def test_spawn_is_deterministic_given_same_inputs(fixed_now):
    species = load_bundled()
    world1 = _world(fixed_now)
    world2 = _world(fixed_now)
    events = [Event(kind="commit", project="x", detail="sha", at=fixed_now)]
    # uuid is non-deterministic, so compare species + name structure
    b1 = spawn_run(world1, events, _sample(), fixed_now, species)
    b2 = spawn_run(world2, events, _sample(), fixed_now, species)
    assert [f.species for f in b1] == [f.species for f in b2]
    assert [f.glyph for f in b1] == [f.glyph for f in b2]
    assert [f.provenance for f in b1] == [f.provenance for f in b2]
