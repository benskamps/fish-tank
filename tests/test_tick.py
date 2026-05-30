import datetime as dt
import json

from tank import paths
from tank.bestiary import load_bundled
from tank.clock import FakeClock
from tank.models import Event, HardwareSample
from tank.tick import TickEngine


class FakeObserver:
    def __init__(self, events_per_tick):
        self.events = list(events_per_tick)

    def scan_since(self, since, world):
        return self.events.pop(0) if self.events else []


class FakeHardware:
    def __init__(self, samples):
        self.samples = list(samples)

    def sample(self, timeout=2.0):
        return self.samples.pop(0) if self.samples else _quiet()


def _quiet():
    return HardwareSample(
        cpu_temp_c=40.0, gpu_temp_c=50.0, cpu_load_pct=10.0,
        gpu_load_pct=10.0, memory_pct=30.0, idle_seconds=0,
        uptime_seconds=3600, sources_used=["test"], degraded=False,
    )


def test_first_tick_initializes_world_and_writes_snapshot(tmp_tank_dir, fixed_now):
    clock = FakeClock(fixed_now)
    hw = FakeHardware([_quiet()])
    obs = FakeObserver([[]])
    engine = TickEngine(clock=clock, hardware=hw, observer=obs,
                        species=load_bundled())
    engine.run_once()
    assert paths.world_path().exists()
    assert paths.snapshot_path().exists()


def test_ship_event_produces_shipfish_after_tick(tmp_tank_dir, fixed_now):
    clock = FakeClock(fixed_now)
    hw = FakeHardware([_quiet()])
    obs = FakeObserver([[
        Event(kind="ship", project="thelongway",
              detail="abc", at=fixed_now),
    ]])
    engine = TickEngine(clock=clock, hardware=hw, observer=obs,
                        species=load_bundled())
    engine.run_once()
    world_blob = json.loads(paths.world_path().read_text())
    species = [f["species"] for f in world_blob["fish"]]
    assert "shipfish" in species


def test_old_age_death_appends_to_graveyard(tmp_tank_dir, fixed_now):
    clock = FakeClock(fixed_now)
    hw = FakeHardware([_quiet(), _quiet()])
    obs = FakeObserver([
        [Event(kind="commit", project="a", detail="sha", at=fixed_now)],
        [],
    ])
    engine = TickEngine(clock=clock, hardware=hw, observer=obs,
                        species=load_bundled())
    engine.run_once()
    clock.advance(dt.timedelta(days=400))
    engine.run_once()
    gy = paths.graveyard_path()
    assert gy.exists()
    lines = gy.read_text().strip().splitlines()
    assert any('"old_age"' in line or '"crowding"' in line for line in lines)


def test_world_persists_across_ticks(tmp_tank_dir, fixed_now):
    clock = FakeClock(fixed_now)
    hw = FakeHardware([_quiet(), _quiet()])
    obs = FakeObserver([[], []])
    engine = TickEngine(clock=clock, hardware=hw, observer=obs,
                        species=load_bundled())
    engine.run_once()
    first = json.loads(paths.world_path().read_text())["created_at"]
    clock.advance(dt.timedelta(minutes=5))
    engine.run_once()
    second = json.loads(paths.world_path().read_text())["created_at"]
    assert first == second
