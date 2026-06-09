from tank import paths


def test_tank_home_defaults_to_dot_tank(monkeypatch, tmp_path):
    monkeypatch.delenv("TANK_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert paths.tank_home() == tmp_path / ".tank"


def test_tank_home_respects_env_override(tmp_tank_dir):
    assert paths.tank_home() == tmp_tank_dir


def test_ensure_dirs_creates_tank_home(tmp_path, monkeypatch):
    target = tmp_path / "newtank"
    monkeypatch.setenv("TANK_HOME", str(target))
    paths.ensure_dirs()
    assert target.is_dir()


def test_first_run_copy_writes_missing_files(tmp_tank_dir, tmp_path):
    src = tmp_path / "data"
    src.mkdir()
    (src / "bestiary.yaml").write_text("guppy: {}\n")
    paths.first_run_copy(src)
    assert (tmp_tank_dir / "bestiary.yaml").read_text() == "guppy: {}\n"


def test_first_run_copy_does_not_overwrite_user_edits(tmp_tank_dir, tmp_path):
    src = tmp_path / "data"
    src.mkdir()
    (src / "bestiary.yaml").write_text("bundled: yes\n")
    (tmp_tank_dir / "bestiary.yaml").write_text("user: edits\n")
    paths.first_run_copy(src)
    assert (tmp_tank_dir / "bestiary.yaml").read_text() == "user: edits\n"


def test_seed_user_data_creates_editable_files(tmp_tank_dir):
    """First run seeds bestiary.yaml + epitaphs.yaml so the README's
    'edit the file' promise has a file to edit."""
    copied = paths.seed_user_data()
    assert (tmp_tank_dir / "bestiary.yaml").exists()
    assert (tmp_tank_dir / "epitaphs.yaml").exists()
    names = {p.name for p in copied}
    assert names == {"bestiary.yaml", "epitaphs.yaml"}
    # The seeded bestiary is the real bundled one, ready to edit.
    assert "guppy" in (tmp_tank_dir / "bestiary.yaml").read_text(encoding="utf-8")


def test_seed_user_data_is_idempotent_and_preserves_edits(tmp_tank_dir):
    (tmp_tank_dir / "bestiary.yaml").write_text("myfish: {}\n", encoding="utf-8")
    copied = paths.seed_user_data()
    # bestiary.yaml already existed -> untouched; only epitaphs.yaml seeded.
    assert (tmp_tank_dir / "bestiary.yaml").read_text() == "myfish: {}\n"
    assert {p.name for p in copied} == {"epitaphs.yaml"}
    # Running again is a no-op.
    assert paths.seed_user_data() == []
