from tank.bestiary import load_bestiary, load_bundled


def test_load_bundled_returns_at_least_twelve_species():
    species = load_bundled()
    assert len(species) >= 12


def test_load_bundled_guppy_has_glyph_pool():
    species = load_bundled()
    assert "guppy" in species
    assert species["guppy"].glyph_pool


def test_load_invalid_yaml_falls_back_to_bundled(tmp_path):
    bad = tmp_path / "bestiary.yaml"
    bad.write_text("not: [valid: yaml: at: all")
    species = load_bestiary(bad)
    assert "guppy" in species


def test_load_missing_fields_uses_defaults(tmp_path):
    minimal = tmp_path / "bestiary.yaml"
    minimal.write_text("noname:\n  glyph_pool: ['<o>']\n")
    species = load_bestiary(minimal)
    assert species["noname"].category == "common"
    assert species["noname"].base_lifespan_days == (7, 30)


def test_lookup_by_trigger():
    species = load_bundled()
    cold = [s for s in species.values() if s.spawn_trigger == "cold_sustained"]
    assert len(cold) >= 1


def test_new_species_present_with_zones():
    species = load_bundled()
    for key in ("rummynose", "hatchetfish", "killifish", "pleco", "cleanershrimp"):
        assert key in species, f"missing species {key}"
    # habitat zones make the tank feel real: surface vs bottom dwellers
    assert species["hatchetfish"].zone == "surface"
    assert species["pleco"].zone == "bottom"
    assert species["snail"].zone == "bottom"
    assert species["guppy"].social == "school"


def test_default_zone_is_mid():
    from tank.bestiary import load_bestiary
    import tempfile
    import pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / "bestiary.yaml"
    p.write_text("x:\n  glyph_pool: ['<o>']\n")
    sp = load_bestiary(p)["x"]
    assert sp.zone == "mid"
    assert sp.social == "solo"


def test_crab_loads_with_bottom_zone():
    species = load_bundled()
    assert "crab" in species
    assert species["crab"].zone == "bottom"


def test_override_loader_honors_user_bestiary(tmp_tank_dir):
    """The README promises ~/.tank/bestiary.yaml is honored on the next tick."""
    from tank import bestiary, paths
    override = paths.bestiary_path()
    override.write_text(
        "myfish:\n  glyph_pool: ['<o>']\n  category: custom\n",
        encoding="utf-8",
    )
    species = bestiary.load()
    assert "myfish" in species
    assert species["myfish"].category == "custom"
    # A user override replaces the bundled set; bundled guppy is gone.
    assert "guppy" not in species


def test_override_loader_falls_back_to_bundled_when_absent(tmp_tank_dir):
    from tank import bestiary
    species = bestiary.load()
    assert "guppy" in species


def test_override_loader_falls_back_on_empty_file(tmp_tank_dir):
    from tank import bestiary, paths
    paths.bestiary_path().write_text("", encoding="utf-8")
    species = bestiary.load()
    assert "guppy" in species


def test_sparse_override_species_is_spawn_safe(tmp_path):
    """A minimal user species (no mood_bias/glyph_pool/lifespan) must spawn,
    not crash the headless tick with rng.choices([])/rng.choice([])."""
    from tank.bestiary import load_bestiary
    from tank.spawn import _make_fish
    import datetime as dt
    import random
    p = tmp_path / "bestiary.yaml"
    p.write_text("bare:\n  category: custom\n", encoding="utf-8")
    sp = load_bestiary(p)["bare"]
    assert sp.mood_bias  # non-empty
    assert sp.glyph_pool  # non-empty
    fish = _make_fish(sp, random.Random(0), None,
                      dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    assert fish.species == "bare"


def test_malformed_lifespan_overrides_do_not_crash(tmp_path):
    from tank.bestiary import load_bestiary
    p = tmp_path / "bestiary.yaml"
    p.write_text(
        "single:\n  base_lifespan_days: [5]\n"
        "scalar:\n  base_lifespan_days: 9\n"
        "reversed:\n  base_lifespan_days: [40, 10]\n",
        encoding="utf-8",
    )
    species = load_bestiary(p)
    assert species["single"].base_lifespan_days == (5, 5)
    assert species["scalar"].base_lifespan_days == (9, 9)
    assert species["reversed"].base_lifespan_days == (10, 40)
