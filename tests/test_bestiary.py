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
