import datetime as dt

from tank.models import Fish, HardwareSample, Weather, World
from tank.serdes import world_from_json, world_to_json


def make_world(now: dt.datetime) -> World:
    return World(
        schema_version=1,
        created_at=now,
        last_tick_at=now,
        fish=[
            Fish(
                id="abc123",
                name="Pip",
                species="guppy",
                glyph=">°))<",
                born_at=now,
                lifespan_days=15.0,
                provenance="bestiary:guppy",
                project=None,
                mood="calm",
                last_position=(10, 5),
            )
        ],
        weather=Weather(
            temperature_c=22.0,
            current_strength=0.3,
            silt_density=0.2,
            light_level=0.7,
            pressure=0.1,
            fossil_layer=["·", "✦"],
        ),
        seen_commits={"~/projects/my-app": "abc"},
        seen_seals={"2026-05-13-seal.md"},
        seen_projects={"my-app", "demo-site"},
        config_overrides={},
    )


def test_roundtrip_world(fixed_now):
    world = make_world(fixed_now)
    blob = world_to_json(world)
    restored = world_from_json(blob)
    assert restored == world


def test_roundtrip_preserves_datetime_tzinfo(fixed_now):
    world = make_world(fixed_now)
    blob = world_to_json(world)
    restored = world_from_json(blob)
    assert restored.created_at.tzinfo is not None
    assert restored.fish[0].born_at == fixed_now


def test_hardware_sample_degraded_flag():
    s = HardwareSample(
        cpu_temp_c=None,
        gpu_temp_c=None,
        cpu_load_pct=20.0,
        gpu_load_pct=None,
        memory_pct=40.0,
        idle_seconds=10,
        uptime_seconds=3600,
        sources_used=["psutil"],
        degraded=True,
    )
    assert s.degraded is True
