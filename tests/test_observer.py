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
        seen_notes=set(),
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
                   notes_dir=tmp_path / "notes_doesntexist")
    # First scan baselines the repo (new_project fires, but no historical commits).
    first = obs.scan_since(world.last_tick_at, world)
    assert "new_project" in [e.kind for e in first]
    # A commit made AFTER the tank starts watching spawns a commit event.
    _commit(proj, "feat: world")
    events = obs.scan_since(world.last_tick_at, world)
    assert "commit" in [e.kind for e in events]


def test_first_scan_does_not_dump_commit_history(tmp_path, fixed_now):
    """Regression guard for the 1358-fish bug: a repo with lots of history must
    NOT emit a commit event per historical commit on first sight."""
    proj = tmp_path / "projects" / "busy"
    proj.mkdir(parents=True)
    _init_repo(proj)
    for i in range(8):
        _commit(proj, f"commit {i}")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "notes_doesntexist")
    events = obs.scan_since(world.last_tick_at, world)
    assert [e for e in events if e.kind in ("commit", "ship")] == []
    assert world.seen_commits.get(str(proj))  # but HEAD is baselined


def test_observer_dedups_already_seen_commits(tmp_path, fixed_now):
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "feat: hello")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "notes_doesntexist")
    obs.scan_since(world.last_tick_at, world)
    events_again = obs.scan_since(world.last_tick_at, world)
    assert [e for e in events_again if e.kind == "commit"] == []


def test_observer_promotes_ship_commit(tmp_path, fixed_now):
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "init")  # baseline commit

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "notes_doesntexist")
    obs.scan_since(world.last_tick_at, world)  # baseline the repo
    _commit(proj, "ship v0.1.0")
    events = obs.scan_since(world.last_tick_at, world)
    assert any(e.kind == "ship" for e in events)


def test_pre_existing_projects_and_notes_are_baselined(tmp_path):
    """Items that pre-date the tank's creation are baselined silently — no
    founderfish per existing repo, no notefish per existing note."""
    import datetime as dt
    future = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
    proj = tmp_path / "projects" / "old"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "history")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "old-note.md").write_text("# old")

    world = _make_world(future)  # tank "created" far in the future of these files
    obs = Observer(projects_root=tmp_path / "projects", notes_dir=notes)
    events = obs.scan_since(world.last_tick_at, world)
    assert events == []  # all pre-existing -> baselined, nothing spawns
    assert "old" in world.seen_projects
    assert "old-note.md" in world.seen_notes


def test_observer_picks_up_new_note(tmp_path, fixed_now):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "2026-05-14-test.md").write_text("# test note")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "no_projects",
                   notes_dir=notes)
    events = obs.scan_since(world.last_tick_at, world)
    assert any(e.kind == "note_written" for e in events)


def test_observer_skips_corrupt_git_project(tmp_path, fixed_now):
    bad = tmp_path / "projects" / "bad"
    bad.mkdir(parents=True)
    (bad / ".git").mkdir()
    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    events = obs.scan_since(world.last_tick_at, world)
    assert any(e.kind == "new_project" for e in events)
