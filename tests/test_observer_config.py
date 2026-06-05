"""Tests for Observer allow-list, safety cap, and config sourcing (Task 1)."""
import datetime as dt
import logging
import subprocess
from pathlib import Path

from tank.models import Weather, World
from tank.observer import MAX_UNFILTERED_REPOS, Observer


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


# ---------------------------------------------------------------------------
# watch allow-list
# ---------------------------------------------------------------------------

def test_watch_list_includes_only_listed_repos(tmp_path, fixed_now):
    root = tmp_path / "projects"
    for name in ("keep-me", "skip-me", "also-keep"):
        (root / name).mkdir(parents=True)
    world = _make_world(fixed_now)
    obs = Observer(projects_root=root, notes_dir=tmp_path / "no_notes",
                   watch=["keep-me", "also-keep"])
    obs.scan_since(world.last_tick_at, world)
    assert "keep-me" in world.seen_projects
    assert "also-keep" in world.seen_projects
    assert "skip-me" not in world.seen_projects


def test_watch_list_filters_git_scan(tmp_path, fixed_now):
    root = tmp_path / "projects"
    watched = root / "watched"
    ignored = root / "ignored"
    watched.mkdir(parents=True)
    ignored.mkdir(parents=True)
    _init_repo(watched)
    _commit(watched, "init")
    _init_repo(ignored)
    _commit(ignored, "init")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=root, notes_dir=tmp_path / "no_notes",
                   watch=["watched"])
    obs.scan_since(world.last_tick_at, world)  # baseline
    # only the watched repo should have been baselined in seen_commits
    assert str(watched) in world.seen_commits
    assert str(ignored) not in world.seen_commits

    _commit(watched, "feat: a")
    _commit(ignored, "feat: b")
    events = obs.scan_since(world.last_tick_at, world)
    projects = {e.project for e in events if e.kind == "commit"}
    assert "watched" in projects
    assert "ignored" not in projects


def test_empty_watch_scans_all(tmp_path, fixed_now):
    root = tmp_path / "projects"
    for name in ("a", "b", "c"):
        (root / name).mkdir(parents=True)
    world = _make_world(fixed_now)
    obs = Observer(projects_root=root, notes_dir=tmp_path / "no_notes", watch=[])
    obs.scan_since(world.last_tick_at, world)
    assert {"a", "b", "c"} <= world.seen_projects


def test_none_watch_scans_all(tmp_path, fixed_now):
    root = tmp_path / "projects"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
    world = _make_world(fixed_now)
    obs = Observer(projects_root=root, notes_dir=tmp_path / "no_notes", watch=None)
    obs.scan_since(world.last_tick_at, world)
    assert {"a", "b"} <= world.seen_projects


# ---------------------------------------------------------------------------
# safety cap (no watch list, > MAX_UNFILTERED_REPOS dirs)
# ---------------------------------------------------------------------------

def test_cap_scans_only_newest_and_logs_skip_count(tmp_path, fixed_now, caplog):
    root = tmp_path / "projects"
    root.mkdir(parents=True)
    total = MAX_UNFILTERED_REPOS + 7
    import os
    import time
    base = time.time()
    newest_names = set()
    for i in range(total):
        d = root / f"repo{i:03d}"
        d.mkdir()
        # Stagger mtimes so the highest-index dirs are the newest.
        ts = base + i
        os.utime(d, (ts, ts))
        if i >= total - MAX_UNFILTERED_REPOS:
            newest_names.add(d.name)

    world = _make_world(fixed_now)
    obs = Observer(projects_root=root, notes_dir=tmp_path / "no_notes")
    with caplog.at_level(logging.WARNING, logger="tank.observer"):
        obs.scan_since(world.last_tick_at, world)

    assert len(world.seen_projects) == MAX_UNFILTERED_REPOS
    assert world.seen_projects == newest_names
    # No silent truncation: the skip count must be logged.
    assert any("skipping 7" in rec.getMessage() for rec in caplog.records)


def test_under_cap_does_not_warn(tmp_path, fixed_now, caplog):
    root = tmp_path / "projects"
    for i in range(5):
        (root / f"r{i}").mkdir(parents=True)
    world = _make_world(fixed_now)
    obs = Observer(projects_root=root, notes_dir=tmp_path / "no_notes")
    with caplog.at_level(logging.WARNING, logger="tank.observer"):
        obs.scan_since(world.last_tick_at, world)
    assert not any("skipping" in rec.getMessage() for rec in caplog.records)
    assert len(world.seen_projects) == 5


# ---------------------------------------------------------------------------
# config file + env sourcing (from_config)
# ---------------------------------------------------------------------------

def _write_config(tmp_tank_dir, body: str) -> None:
    (tmp_tank_dir / "config.yaml").write_text(body, encoding="utf-8")


def test_config_file_watch_is_honored(tmp_tank_dir, tmp_path, monkeypatch):
    root = tmp_path / "code"
    monkeypatch.delenv("TANK_WATCH", raising=False)
    monkeypatch.setenv("TANK_PROJECTS_ROOT", str(root))
    monkeypatch.setenv("TANK_NOTES_DIR", str(tmp_path / "no_notes"))
    _write_config(tmp_tank_dir, 'observer:\n  watch: ["my-app", "my-lib"]\n')
    obs = Observer.from_config()
    assert obs.watch == {"my-app", "my-lib"}


def test_env_watch_overrides_file(tmp_tank_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("TANK_PROJECTS_ROOT", str(tmp_path / "code"))
    monkeypatch.setenv("TANK_NOTES_DIR", str(tmp_path / "no_notes"))
    _write_config(tmp_tank_dir, 'observer:\n  watch: ["from-file"]\n')
    monkeypatch.setenv("TANK_WATCH", "from-env-a, from-env-b")
    obs = Observer.from_config()
    assert obs.watch == {"from-env-a", "from-env-b"}


def test_config_file_paths_with_tilde_expand(tmp_tank_dir, monkeypatch):
    # Ensure file values (not env) are used, then check ~ expansion.
    monkeypatch.delenv("TANK_PROJECTS_ROOT", raising=False)
    monkeypatch.delenv("TANK_NOTES_DIR", raising=False)
    monkeypatch.delenv("TANK_WATCH", raising=False)
    _write_config(
        tmp_tank_dir,
        'observer:\n'
        '  projects_root: "~/code"\n'
        '  notes_dir: "~/notes"\n',
    )
    obs = Observer.from_config()
    home = Path.home()
    assert obs.projects_root == home / "code"
    assert obs.notes_dir == home / "notes"


def test_env_projects_root_overrides_file(tmp_tank_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("TANK_WATCH", raising=False)
    monkeypatch.setenv("TANK_PROJECTS_ROOT", str(tmp_path / "env-root"))
    monkeypatch.delenv("TANK_NOTES_DIR", raising=False)
    _write_config(tmp_tank_dir, 'observer:\n  projects_root: "~/file-root"\n')
    obs = Observer.from_config()
    assert obs.projects_root == tmp_path / "env-root"


def test_from_config_no_file_no_env_uses_defaults(tmp_tank_dir, monkeypatch):
    monkeypatch.delenv("TANK_PROJECTS_ROOT", raising=False)
    monkeypatch.delenv("TANK_NOTES_DIR", raising=False)
    monkeypatch.delenv("TANK_WATCH", raising=False)
    obs = Observer.from_config()
    home = Path.home()
    assert obs.projects_root == home / "projects"
    assert obs.watch == set()


# ---------------------------------------------------------------------------
# deprecated pre-0.6.x aliases (seals_dir / TANK_SEALS_DIR)
# ---------------------------------------------------------------------------

def test_legacy_env_seals_dir_is_honored(tmp_tank_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("TANK_NOTES_DIR", raising=False)
    monkeypatch.delenv("TANK_WATCH", raising=False)
    monkeypatch.setenv("TANK_SEALS_DIR", str(tmp_path / "legacy-seals"))
    obs = Observer.from_config()
    assert obs.notes_dir == tmp_path / "legacy-seals"


def test_legacy_config_seals_dir_is_honored(tmp_tank_dir, monkeypatch):
    monkeypatch.delenv("TANK_NOTES_DIR", raising=False)
    monkeypatch.delenv("TANK_SEALS_DIR", raising=False)
    monkeypatch.delenv("TANK_WATCH", raising=False)
    _write_config(tmp_tank_dir, 'observer:\n  seals_dir: "~/legacy-seals"\n')
    obs = Observer.from_config()
    assert obs.notes_dir == Path.home() / "legacy-seals"


def test_notes_dir_wins_over_legacy_seals_dir(tmp_tank_dir, monkeypatch):
    monkeypatch.delenv("TANK_NOTES_DIR", raising=False)
    monkeypatch.delenv("TANK_SEALS_DIR", raising=False)
    monkeypatch.delenv("TANK_WATCH", raising=False)
    _write_config(
        tmp_tank_dir,
        'observer:\n  notes_dir: "~/new-notes"\n  seals_dir: "~/legacy-seals"\n',
    )
    obs = Observer.from_config()
    assert obs.notes_dir == Path.home() / "new-notes"
