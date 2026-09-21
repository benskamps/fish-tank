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


# ---------------------------------------------------------------------------
# pushfish + mergefish — the two events git already records for you
# ---------------------------------------------------------------------------
#
# These use a REAL bare remote and a REAL `git push`, not a mocked subprocess.
# That is the whole point: the signal pushfish reads is the line git itself
# writes into the reflog of refs/remotes/* when a push lands, and only a real
# push writes it. A mock would assert that our parser parses our own fixture —
# a tautology of exactly the shape tests/test_glyphs.py exists to warn about.


def _branch(path: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path, text=True
    ).strip()


def _give_remote(path: Path, remote: Path) -> None:
    """Point `path` at a real bare remote it can actually push to."""
    subprocess.check_call(["git", "init", "--bare", "-q", str(remote)])
    subprocess.check_call(["git", "remote", "add", "origin", str(remote)],
                          cwd=path)


def _push(path: Path, branch: str | None = None) -> None:
    subprocess.check_call(
        ["git", "push", "-q", "origin", branch or _branch(path)], cwd=path)


def _kinds(events) -> list[str]:
    return [e.kind for e in events]


def test_push_spawns_a_push_event(tmp_path, fixed_now):
    """A push the tank watched happen is a push event. pushfish's whole basis."""
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "feat: hello")
    _give_remote(proj, tmp_path / "remote.git")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    obs.scan_since(world.last_tick_at, world)      # baseline

    _push(proj)
    events = obs.scan_since(world.last_tick_at, world)
    pushes = [e for e in events if e.kind == "push"]
    assert len(pushes) == 1
    assert pushes[0].project == "demo"


def test_first_scan_does_not_replay_push_history(tmp_path, fixed_now):
    """The 1358-fish guard, pushfish edition.

    A repo with a push history that predates the tank must be baselined
    silently. Otherwise installing the tank onto a real machine spawns a
    pushfish for every push still surviving in every reflog.
    """
    proj = tmp_path / "projects" / "busy"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _give_remote(proj, tmp_path / "remote.git")
    for i in range(4):
        _commit(proj, f"commit {i}")
        _push(proj)

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    events = obs.scan_since(world.last_tick_at, world)
    assert [e for e in events if e.kind == "push"] == []


def test_fetch_is_not_a_push(tmp_path, fixed_now):
    """THE negative control: someone else's work ARRIVING must not spawn.

    Two clones share one remote. The watched repo only ever fetches, so its
    remote-tracking ref moves without the user having pushed anything. A
    detector that keyed on "the remote ref moved" would fire here — which would
    make pushfish a fish about other people's work.
    """
    remote = tmp_path / "remote.git"
    proj = tmp_path / "projects" / "watched"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "shared base")
    _give_remote(proj, remote)
    _push(proj)

    other = tmp_path / "elsewhere"
    subprocess.check_call(["git", "clone", "-q", str(remote), str(other)])
    subprocess.check_call(["git", "config", "user.email", "o@o"], cwd=other)
    subprocess.check_call(["git", "config", "user.name", "o"], cwd=other)

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    obs.scan_since(world.last_tick_at, world)      # baseline

    # The other clone pushes; the watched repo merely fetches it.
    _commit(other, "their work")
    _push(other)
    subprocess.check_call(["git", "fetch", "-q", "origin"], cwd=proj)

    events = obs.scan_since(world.last_tick_at, world)
    assert "push" not in _kinds(events)


def test_push_to_a_branch_other_than_head_is_still_seen(tmp_path, fixed_now):
    """Pushes must be found across every remote-tracking ref, not just HEAD's.

    Measured across a real machine's repos before this was written, rather than
    assumed: one checked-out branch had no upstream at all, so a HEAD-only
    reader returned nothing for that whole repo; another repo's most recent
    push sat on a branch nobody was standing on; a third had 83 pushes spread
    over 39 refs. A reader that consults only HEAD@{push} sees a small minority
    of real work.
    """
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "base")
    _give_remote(proj, tmp_path / "remote.git")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    obs.scan_since(world.last_tick_at, world)      # baseline

    # Push a feature branch WITHOUT -u, so it never becomes HEAD's upstream…
    subprocess.check_call(["git", "checkout", "-q", "-b", "feature"], cwd=proj)
    _commit(proj, "feature work")
    _push(proj, "feature")
    # …then sit on a branch with no upstream whatsoever, which is precisely what
    # `git rev-parse HEAD@{push}` fails outright on. The only push this repo has
    # ever seen is now on a ref that HEAD cannot reach.
    subprocess.check_call(["git", "checkout", "-q", "-b", "orphan"], cwd=proj)

    events = obs.scan_since(world.last_tick_at, world)
    assert "push" in _kinds(events)


def test_repo_with_no_remote_is_silent_not_fatal(tmp_path, fixed_now):
    """Most local repos have no remote. They must scan quietly, never raise."""
    proj = tmp_path / "projects" / "local-only"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "solo work")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    obs.scan_since(world.last_tick_at, world)
    _commit(proj, "more solo work")
    events = obs.scan_since(world.last_tick_at, world)
    assert "push" not in _kinds(events)


def test_merge_commit_subject_is_a_pr_merge(tmp_path, fixed_now):
    """GitHub's classic merge commit. mergefish's basis."""
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "base")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    obs.scan_since(world.last_tick_at, world)      # baseline

    _commit(proj, "Merge pull request #12 from octocat/feat-thing")
    events = obs.scan_since(world.last_tick_at, world)
    assert "pr_merge" in _kinds(events)


def test_squash_merge_subject_is_a_pr_merge(tmp_path, fixed_now):
    """The squash-merge shape — which is how this repo's own PRs land."""
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "base")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    obs.scan_since(world.last_tick_at, world)      # baseline

    _commit(proj, "feat(mood): add a murky tank mood (#6)")
    events = obs.scan_since(world.last_tick_at, world)
    assert "pr_merge" in _kinds(events)


def test_release_landing_via_pr_is_a_ship_not_a_merge(tmp_path, fixed_now):
    """Negative control for the one real collision in this repo's history.

    `release: v0.9.0 — tank serve catches up … (#11)` satisfies BOTH patterns.
    A release is the rarer, prouder event, so ship wins and shipfish keeps it;
    without this ruling pinned, v0.9.0 would have quietly spawned a mergefish.
    """
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "base")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    obs.scan_since(world.last_tick_at, world)      # baseline

    _commit(proj, "release: v0.9.0 - tank serve catches up (#11)")
    events = obs.scan_since(world.last_tick_at, world)
    assert "ship" in _kinds(events)
    assert "pr_merge" not in _kinds(events)


def test_an_ordinary_commit_is_still_just_a_commit(tmp_path, fixed_now):
    """Driftfish must not be starved by the new patterns."""
    proj = tmp_path / "projects" / "demo"
    proj.mkdir(parents=True)
    _init_repo(proj)
    _commit(proj, "base")

    world = _make_world(fixed_now)
    obs = Observer(projects_root=tmp_path / "projects",
                   notes_dir=tmp_path / "no_notes")
    obs.scan_since(world.last_tick_at, world)      # baseline

    _commit(proj, "refactor: tidy the observer")
    events = obs.scan_since(world.last_tick_at, world)
    assert "commit" in _kinds(events)
    assert "pr_merge" not in _kinds(events)
