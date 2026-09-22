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


def test_note_event_spawns_notefish(fixed_now):
    species = load_bundled()
    events = [Event(kind="note_written", project=None,
                    detail="2026-05-14-note.md", at=fixed_now)]
    births = spawn_run(_world(fixed_now), events, _sample(), fixed_now, species)
    assert any(f.species == "notefish" for f in births)


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


def test_push_event_spawns_pushfish_named_after_project(fixed_now):
    species = load_bundled()
    events = [Event(kind="push", project="my-app",
                    detail="abc123", at=fixed_now)]
    births = spawn_run(_world(fixed_now), events, _sample(), fixed_now, species)
    push = [f for f in births if f.species == "pushfish"]
    assert len(push) == 1
    assert "my-app" in push[0].name


def test_pr_merge_event_spawns_mergefish_named_after_project(fixed_now):
    species = load_bundled()
    events = [Event(kind="pr_merge", project="my-app",
                    detail="def456", at=fixed_now)]
    births = spawn_run(_world(fixed_now), events, _sample(), fixed_now, species)
    merge = [f for f in births if f.species == "mergefish"]
    assert len(merge) == 1
    assert "my-app" in merge[0].name


def test_a_push_and_a_merge_are_different_creatures(fixed_now):
    """Both events in one tick produce two visibly distinct residents.

    Distinct species, distinct glyphs. If a future edit collapses them onto a
    shared trigger this fails here rather than in someone's aquarium.
    """
    species = load_bundled()
    events = [
        Event(kind="push", project="app", detail="aaa", at=fixed_now),
        Event(kind="pr_merge", project="app", detail="bbb", at=fixed_now),
    ]
    births = spawn_run(_world(fixed_now), events, _sample(), fixed_now, species)
    born = {f.species: f for f in births}
    assert "pushfish" in born and "mergefish" in born
    assert born["pushfish"].glyph != born["mergefish"].glyph


def test_pushfish_is_momentary_and_mergefish_endures(fixed_now):
    """Lifespans encode what the two events MEAN.

    A push is momentum: real, and it fades. A landed PR is permanent. So a
    pushfish must never outlive a mergefish, and a push must not outlive the
    ship it may become.
    """
    species = load_bundled()
    push, merge = species["pushfish"], species["mergefish"]
    ship, drift = species["shipfish"], species["driftfish"]
    # Momentum fades faster than a merge settles.
    assert push.base_lifespan_days[1] < merge.base_lifespan_days[0]
    # …but a push is still more than one more commit.
    assert push.base_lifespan_days[1] > drift.base_lifespan_days[1]
    # …and neither outlives a ship.
    assert merge.base_lifespan_days[1] <= ship.base_lifespan_days[1]


def test_pushfish_expires_while_mergefish_is_still_swimming(fixed_now):
    """Appear AND expire: run the clock forward past the pushfish's whole span.

    Without this, "the resident appears" is half a lifecycle — a species that
    spawns and never leaves would silently fill the tank.
    """
    import datetime as _dt
    from tank.mortality import run as mortality_run

    species = load_bundled()
    world = _world(fixed_now)
    events = [
        Event(kind="push", project="app", detail="aaa", at=fixed_now),
        Event(kind="pr_merge", project="app", detail="bbb", at=fixed_now),
    ]
    world.fish.extend(
        spawn_run(world, events, _sample(), fixed_now, species))
    assert {f.species for f in world.fish} >= {"pushfish", "mergefish"}

    # One day past the longest a pushfish can live; well inside a mergefish's.
    later = fixed_now + _dt.timedelta(
        days=species["pushfish"].base_lifespan_days[1] + 1)
    deaths = mortality_run(world, _sample(), [], later, species)

    assert "pushfish" in [d.species for d in deaths]
    assert all(d.cause == "old_age" for d in deaths if d.species == "pushfish")
    assert "mergefish" in [f.species for f in world.fish]
    assert "pushfish" not in [f.species for f in world.fish]


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
