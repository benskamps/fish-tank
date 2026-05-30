import subprocess
from pathlib import Path

from tank.models import Weather, World
from tank.observer import Observer


def _make_world(now):
    return World(
        schema_version=1,
        created_at=now,
        last_tick_at=now,
        fish=[],
        weather=Weather(20.0, 0.0, 0.0, 0.5, 0.0, []),
        seen_commits={},
        seen_seals=set(),
        seen_projects=set(),
        config_overrides={},
    )


def _init_repo(path: Path) -> None:
    subprocess.check_call(["git", "init", "-q"], cwd=path)
    subprocess.check_call(["git", "config", "user.email", "t@t"], cwd=path)
    subprocess.check_call(["git", "config", "user.name", "t"], cwd=path)


def _commit(path: Path, message: str) -> str:
    (path / "file.txt").write_text(message)
    subprocess.check_call(["git", "add", "."], cwd=path)
    subprocess.check_call(["git", "commit", "-q", "-m", message], cwd=path)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()


def test_observer_picks_up_new_commit(tmp_path, fixed_now):
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "feat: hello")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   seals_dir=tmp_path / "seals_doesntexist")
    events = obs.scan_since(world.last_tick_at, world)
    kinds = [e.kind for e in events]
    assert "new_project" in kinds
    assert "commit" in kinds


def test_observer_dedups_already_seen_commits(tmp_path, fixed_now):
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "feat: hello")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   seals_dir=tmp_path / "seals_doesntexist")
    obs.scan_since(world.last_tick_at, world)
    events_again = obs.scan_since(world.last_tick_at, world)
    assert [e for e in events_again if e.kind == "commit"] == []


def test_observer_promotes_ship_commit(tmp_path, fixed_now):
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "ship v0.1.0")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   seals_dir=tmp_path / "seals_doesntexist")
    events = obs.scan_since(world.last_tick_at, world)
    assert any(e.kind == "ship" for e in events)


def test_observer_picks_up_new_seal(tmp_path, fixed_now):
    seals = tmp_path / "seals"
    seals.mkdir()
    (seals / "2026-05-14-test.md").write_text("# test seal")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "no_projects",
                   seals_dir=seals)
    events = obs.scan_since(world.last_tick_at, world)
    assert any(e.kind == "seal_written" for e in events)


def test_observer_skips_corrupt_git_project(tmp_path, fixed_now):
    bad = tmp_path / "projects" / "bad"
    bad.mkdir(parents=True)
    (bad / ".git").mkdir()
    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   seals_dir=tmp_path / "no_seals")
    events = obs.scan_since(world.last_tick_at, world)
    assert any(e.kind == "new_project" for e in events)
