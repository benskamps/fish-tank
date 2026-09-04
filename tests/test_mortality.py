import datetime as dt

from tank.bestiary import load_bundled
from tank.models import Fish, HardwareSample, Weather, World
from tank.mortality import run as mortality_run


def _world(now, fish):
    return World(
        schema_version=1, created_at=now, last_tick_at=now,
        fish=list(fish),
        weather=Weather(20.0, 0.0, 0.0, 0.5, 0.0, []),
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
               now=None):
    base_now = now or dt.datetime(2026, 5, 14, 22, 0, 0, tzinfo=dt.timezone.utc)
    return Fish(
        id=fish_id, name=f"Pip-{fish_id}", species=species, glyph=">°))<",
        born_at=base_now - dt.timedelta(days=age_days),
        lifespan_days=lifespan, provenance="test",
        project=None, mood="calm", last_position=(0, 0),
    )


def test_fish_past_lifespan_dies_of_old_age(fixed_now):
    species = load_bundled()
    old = _make_fish(lifespan=5.0, age_days=10, now=fixed_now)
    world = _world(fixed_now, [old])
    deaths = mortality_run(world, _sample(), [], fixed_now, species, epitaphs_path=None)
    assert len(deaths) == 1
    assert deaths[0].cause == "old_age"


def test_thermal_shock_at_extreme_heat(fixed_now):
    species = load_bundled()
    fish = [_make_fish(species="coldfin", lifespan=60.0, age_days=1,
                       fish_id=f"f{i}", now=fixed_now)
            for i in range(10)]
    world = _world(fixed_now, fish)
    deaths = mortality_run(world, _sample(cpu_temp_c=95.0, gpu_temp_c=95.0),
                           [], fixed_now, species, epitaphs_path=None)
    thermal = [d for d in deaths if d.cause == "thermal_shock"]
    assert len(thermal) >= 1


def test_crowding_kills_oldest_when_over_capacity(fixed_now):
    species = load_bundled()
    fish = [_make_fish(lifespan=1000, age_days=i, fish_id=f"f{i}", now=fixed_now)
            for i in range(15)]
    world = _world(fixed_now, fish)
    deaths = mortality_run(world, _sample(), [], fixed_now, species, epitaphs_path=None)
    crowding = [d for d in deaths if d.cause == "crowding"]
    assert len(crowding) >= 3


def test_epitaph_renders_with_substitutions(fixed_now):
    species = load_bundled()
    f = _make_fish(lifespan=2.0, age_days=5, now=fixed_now)
    world = _world(fixed_now, [f])
    deaths = mortality_run(world, _sample(), [], fixed_now, species, epitaphs_path=None)
    assert deaths
    assert "Pip" in deaths[0].epitaph
    assert deaths[0].fossil_glyph


def _resident(now, age_days=3, fish_id="ember"):
    """An adopted fish, exactly as `tank adopt` stamps one."""
    f = _make_fish(species="ember", lifespan=36500.0, age_days=age_days,
                   fish_id=fish_id, now=now)
    f.provenance = "manual:adopt"
    return f


def test_adopted_resident_survives_a_crowding_cull(fixed_now):
    """The 2026-08-31 bug: a resident evicted by a crowd that arrived later.

    The tank's first real heartbeat caught up three days of estate activity in
    one tick (78 births), the next tick culled 67 for crowding, and because the
    cull sorts oldest-first the first fish it killed was Ember — adopted three
    days earlier, and therefore the oldest thing in the water.
    """
    species = load_bundled()
    ember = _resident(fixed_now)
    crowd = [_make_fish(lifespan=1000.0, age_days=1, fish_id=f"f{i}", now=fixed_now)
             for i in range(40)]
    world = _world(fixed_now, [ember] + crowd)

    deaths = mortality_run(world, _sample(), [], fixed_now, species, epitaphs_path=None)

    assert all(d.cause == "crowding" for d in deaths)
    assert ember.id in {f.id for f in world.fish}, "the resident was culled"
    assert ember.id not in {d.fish_id for d in deaths}


def test_residents_do_not_count_toward_carrying_capacity(fixed_now):
    """A resident must not push a transient fish out of the tank either."""
    species = load_bundled()
    residents = [_resident(fixed_now, fish_id=f"r{i}") for i in range(3)]
    crowd = [_make_fish(lifespan=1000.0, age_days=1, fish_id=f"f{i}", now=fixed_now)
             for i in range(12)]
    world = _world(fixed_now, residents + crowd)

    deaths = mortality_run(world, _sample(), [], fixed_now, species, epitaphs_path=None)

    assert deaths == [], "12 transients is exactly capacity; residents are not extra"
    assert len(world.fish) == 15


def test_resident_still_dies_of_everything_else(fixed_now):
    """Exempt from crowding is not immortal — that would make the fish a liar."""
    species = load_bundled()
    ember = _resident(fixed_now)
    ember.lifespan_days = 1.0          # older than its lifespan
    world = _world(fixed_now, [ember])
    deaths = mortality_run(world, _sample(), [], fixed_now, species, epitaphs_path=None)
    assert [d.cause for d in deaths] == ["old_age"]
