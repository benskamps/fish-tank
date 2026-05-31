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
