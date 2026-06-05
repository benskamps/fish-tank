"""Regression tests: pre-0.6.x on-disk worlds must survive the seal->note rename.

The 0.6.x rename (seen_seals -> seen_notes, witnessfish -> notefish) was a pure
code rename, but worlds saved by older versions still carry the old names on
disk. Loading one must migrate, not quarantine — a live tank losing all its
fish to a vocabulary change is exactly the bug this file pins down.
"""
import json

from tank import paths
from tank.serdes import world_from_json
from tank.world import WorldStore


def _legacy_world_blob() -> str:
    """A world JSON as written by fish-tank <= 0.5.x (seen_seals, witnessfish)."""
    return json.dumps({
        "schema_version": 1,
        "created_at": {"__dt__": "2026-05-31T02:45:21+00:00"},
        "last_tick_at": {"__dt__": "2026-06-04T22:42:16+00:00"},
        "fish": [
            {
                "id": "f-1", "name": "old-witness", "species": "witnessfish",
                "glyph": "<°)W><",
                "born_at": {"__dt__": "2026-06-01T00:00:00+00:00"},
                "lifespan_days": 30.0,
                "provenance": "event:seal_written:note.md",
                "project": None, "mood": "calm",
                "last_position": {"__tuple__": [3, 4]}, "zone": "mid",
            },
            {
                "id": "f-2", "name": "drifty", "species": "driftfish",
                "glyph": "<·><",
                "born_at": {"__dt__": "2026-06-02T00:00:00+00:00"},
                "lifespan_days": 3.0,
                "provenance": "event:commit:proj:abc",
                "project": "proj", "mood": "darting",
                "last_position": {"__tuple__": [10, 2]}, "zone": "mid",
            },
        ],
        "weather": {
            "temperature_c": 30.0, "current_strength": 0.1,
            "silt_density": 0.5, "light_level": 0.6, "pressure": 0.2,
            "fossil_layer": ["·", "◇"], "phase": "day", "mood": "calm",
        },
        "seen_commits": {"proj": "abc"},
        "seen_seals": {"__set__": ["2026-05-15-old-seal.md"]},
        "seen_projects": {"__set__": ["proj"]},
        "config_overrides": {},
    })


def test_legacy_seen_seals_key_migrates_to_seen_notes():
    world = world_from_json(_legacy_world_blob())
    assert world.seen_notes == {"2026-05-15-old-seal.md"}


def test_legacy_witnessfish_species_migrates_to_notefish():
    world = world_from_json(_legacy_world_blob())
    species = {f.id: f.species for f in world.fish}
    assert species["f-1"] == "notefish"
    assert species["f-2"] == "driftfish"  # untouched


def test_legacy_world_load_or_init_keeps_fish(tmp_tank_dir, fixed_now):
    """End to end: an old world on disk loads instead of being quarantined."""
    paths.world_path().write_text(_legacy_world_blob(), encoding="utf-8")
    store = WorldStore()
    world = store.load_or_init(now=fixed_now)
    assert len(world.fish) == 2  # the tank kept its fish
    assert not list(tmp_tank_dir.glob("world.json.broken-*"))


def test_quarantine_leaves_a_why_file(tmp_tank_dir, fixed_now):
    """A genuinely corrupt world leaves the failure reason on disk — the tick
    runs headless (pythonw), so a silent quarantine just looks like the fish
    vanished."""
    paths.world_path().write_text("{not json", encoding="utf-8")
    store = WorldStore()
    world = store.load_or_init(now=fixed_now)
    assert world.fish == []
    why = list(tmp_tank_dir.glob("world.json.broken-*.why.txt"))
    assert len(why) == 1
    assert "Traceback" in why[0].read_text(encoding="utf-8")
