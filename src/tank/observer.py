"""Scans real-world surfaces for events: git commits, notes, new projects.

This is the ONLY module that reads the projects tree and the notes dir, so it
is also the single place that resolves observer configuration (allow-list +
path overrides). Config precedence: ~/.tank/config.yaml < environment vars.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import subprocess
from pathlib import Path

import yaml

from tank import crashsense, paths, proc
from tank.models import Event, World

logger = logging.getLogger(__name__)

SHIP_RE = re.compile(r"^(ship|release|chore: release|version bump)\b", re.IGNORECASE)

# A commit that lands a pull request — mergefish's signal. Two shapes: GitHub's
# classic merge commit ("Merge pull request #12 from …") and its squash-merge
# subject, which ends with the PR number ("feat(mood): add a murky mood (#6)").
# The squash shape is how this project's own PRs land.
#
# SHIP_RE is tested first and wins ties, because the one real collision is a
# release that landed via PR — this repo's own `release: v0.9.0 … (#11)` matches
# both patterns — and a release is the rarer, prouder event, so shipfish keeps
# it. Pinned by test_release_landing_via_pr_is_a_ship_not_a_merge.
#
# Known and accepted: a purely local commit whose subject merely cites an issue
# as "(#12)" reads as a merge here. That spelling IS the GitHub squash
# convention, and no local signal distinguishes the two, so guessing would cost
# more truth than it buys.
MERGE_RE = re.compile(r"^Merge pull request #\d+|\(#\d+\)$")

# The reflog subject git writes on a remote-tracking ref when YOU push. A fetch
# or a pull writes "fetch …" / "pull …" instead. Keying on this subject rather
# than on "the remote ref moved" is what keeps pushfish a fish about your own
# work — see test_fetch_is_not_a_push, where a second clone's push arrives by
# fetch and must not spawn anything.
PUSH_REFLOG_SUBJECT = "update by push"

# Safety cap, per repo per tick. Reflogs keep 90 days by default, so a tank that
# was switched off for a while could otherwise wake into a hundred pushes at
# once — the pushfish cousin of the 1358-fish bug.
MAX_PUSH_EVENTS_PER_REPO = 5

# Safety cap: with no watch allow-list, never scan more than this many candidate
# dirs under the projects root. Above it, scan only the newest-by-mtime and warn
# about the rest (no silent truncation -- hard project rule).
MAX_UNFILTERED_REPOS = 50


def _reflog_time(selector: str) -> dt.datetime | None:
    """Pull the timestamp out of a ``%gD`` reflog selector.

    Under ``--date=iso-strict`` git renders these as
    ``refs/remotes/origin/main@{2026-09-20T23:11:47-04:00}``. Branch names may
    themselves contain '@', so split on the LAST '@{'.

    This is the reflog's own stamp — when the push happened — not the commit
    date. The two differ arbitrarily whenever old work is pushed or a branch is
    force-pushed, and only one of them is the event the tank is watching for.
    """
    _, sep, stamp = selector.rpartition("@{")
    if not sep or not stamp.endswith("}"):
        return None
    try:
        at = dt.datetime.fromisoformat(stamp[:-1])
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)


def _baseline(newest: dt.datetime | None, now: dt.datetime) -> dt.datetime:
    """The high-water mark to record the first time a repo is seen.

    With pushes already in the reflog, the mark is the newest of them: the tank
    notices pushes from when it starts watching, never a reflog's history.

    With NO pushes in the reflog, the mark is one second before now — not `now`
    itself. Reflog stamps carry whole seconds, so a push landing in the very
    same second the tank first looked would be stamped *earlier* than a
    microsecond-precision `now` and be dropped as stale. Nothing can replay
    here, because there was nothing in the reflog to replay; backing off a
    second only closes that window.
    """
    if newest is not None:
        return newest
    return now.replace(microsecond=0) - dt.timedelta(seconds=1)


def _expand(value: str | None) -> Path | None:
    """Expand ~ and return a Path, or None for empty/blank input."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return Path(os.path.expanduser(value))


class Observer:
    def __init__(
        self,
        projects_root: Path | None = None,
        notes_dir: Path | None = None,
        watch: list[str] | None = None,
    ):
        home = Path.home()
        env_projects = os.environ.get("TANK_PROJECTS_ROOT")
        # TANK_SEALS_DIR is the deprecated pre-0.6.x name, kept as a fallback
        # so tanks configured against the old README keep their notes feed.
        env_notes = os.environ.get("TANK_NOTES_DIR") \
            or os.environ.get("TANK_SEALS_DIR")
        self.projects_root = projects_root or (
            Path(env_projects) if env_projects else (home / "projects")
        )
        # The notes dir holds optional notes/plans/journal markdown files —
        # point TANK_NOTES_DIR (or config observer.notes_dir) at your own dir to
        # feed notefish. Defaults to ~/notes; if it doesn't exist, scanning no-ops.
        self.notes_dir = notes_dir or (
            Path(env_notes) if env_notes else (home / "notes")
        )
        # Normalize the allow-list to a set of dir names; empty/None => watch all.
        self.watch: set[str] = {w for w in (watch or []) if w}

    @classmethod
    def from_config(cls) -> "Observer":
        """Build an Observer from ~/.tank/config.yaml, with env vars overriding.

        config.yaml may contain an ``observer:`` section::

            observer:
              projects_root: "~/code"        # optional, default ~/projects
              watch: ["my-app", "my-lib"]    # optional allow-list of dir names
              notes_dir: "~/notes"           # optional, default ~/notes tree

        Environment overrides: TANK_PROJECTS_ROOT, TANK_NOTES_DIR, and
        TANK_WATCH (comma-separated names). ``~`` is expanded in path values.
        The pre-0.6.x names (``seals_dir`` / TANK_SEALS_DIR) are accepted as
        deprecated fallbacks.
        """
        cfg = cls._load_config()
        projects_root = _expand(os.environ.get("TANK_PROJECTS_ROOT")) or \
            _expand(cfg.get("projects_root"))
        notes_dir = _expand(os.environ.get("TANK_NOTES_DIR")) or \
            _expand(cfg.get("notes_dir")) or \
            _expand(os.environ.get("TANK_SEALS_DIR")) or \
            _expand(cfg.get("seals_dir"))

        env_watch = os.environ.get("TANK_WATCH")
        if env_watch is not None:
            watch = [w.strip() for w in env_watch.split(",") if w.strip()]
        else:
            raw_watch = cfg.get("watch") or []
            watch = [str(w).strip() for w in raw_watch if str(w).strip()]

        return cls(projects_root=projects_root, notes_dir=notes_dir, watch=watch)

    @staticmethod
    def _load_config() -> dict:
        """Read the ``observer:`` section of ~/.tank/config.yaml (best-effort)."""
        try:
            p = paths.config_yaml_path()
            if not p.exists():
                return {}
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                return {}
            section = raw.get("observer") or {}
            return section if isinstance(section, dict) else {}
        except Exception as e:  # noqa: BLE001 - config is opt-in, never fatal
            logger.warning("observer config read failed: %s", e)
            return {}

    def _candidate_dirs(self) -> list[Path]:
        """Directories under the projects root the observer should consider.

        Applies the watch allow-list when set. With no allow-list, applies the
        MAX_UNFILTERED_REPOS safety cap (newest-by-mtime), warning about any
        dirs skipped -- never truncates silently.
        """
        if not self.projects_root.exists():
            return []
        dirs = [e for e in self.projects_root.iterdir() if e.is_dir()]

        if self.watch:
            return [e for e in dirs if e.name in self.watch]

        if len(dirs) > MAX_UNFILTERED_REPOS:
            dirs.sort(key=lambda e: e.stat().st_mtime, reverse=True)
            skipped = len(dirs) - MAX_UNFILTERED_REPOS
            kept = dirs[:MAX_UNFILTERED_REPOS]
            logger.warning(
                "projects root %s has %d dirs; scanning the %d newest, "
                "skipping %d (set observer.watch / TANK_WATCH to choose repos)",
                self.projects_root, len(dirs), MAX_UNFILTERED_REPOS, skipped,
            )
            return kept
        return dirs

    def scan_since(self, since: dt.datetime, world: World) -> list[Event]:
        events: list[Event] = []
        candidates = self._candidate_dirs()
        # Real wall-clock, deliberately not the tank's (fakeable) Clock: reflog
        # stamps are wall-clock, so the push baseline has to be compared against
        # the true now. Same reasoning crashsense already documents for its
        # dedup marker.
        now = dt.datetime.now(tz=dt.timezone.utc)
        events.extend(self._scan_projects(candidates, world))
        events.extend(self._scan_git(candidates, world, now))
        events.extend(self._scan_notes(world))
        events.extend(self._scan_crashes())
        return events

    def _scan_crashes(self) -> list[Event]:
        """Emit a kernel_error per real machine crash (crashstrider's birth).

        Delegates to the best-effort, Windows-only crash detector. Its dedup
        marker uses real wall-clock crash timestamps (independent of the
        simulated tick clock), so the baseline reference is the true now. The
        whole detector is internally guarded — it can only ever return events or
        an empty list, never raise — so a crash-log read can't freeze the tick.
        """
        return crashsense.scan_crashes(dt.datetime.now(tz=dt.timezone.utc))

    def _scan_projects(self, candidates: list[Path], world: World) -> list[Event]:
        out: list[Event] = []
        for entry in candidates:
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            if entry.name in world.seen_projects:
                continue
            world.seen_projects.add(entry.name)
            ctime = dt.datetime.fromtimestamp(
                entry.stat().st_ctime, tz=dt.timezone.utc
            )
            # Only spawn for projects that appeared AFTER the tank started
            # watching. Pre-existing projects are baselined silently -- otherwise
            # a fresh tank spawns a founderfish for every repo you already have.
            if ctime >= world.created_at:
                out.append(Event(kind="new_project", project=entry.name,
                                 detail=str(entry), at=ctime))
        return out

    def _scan_git(self, candidates: list[Path], world: World,
                  now: dt.datetime | None = None) -> list[Event]:
        out: list[Event] = []
        now = now or dt.datetime.now(tz=dt.timezone.utc)
        for entry in candidates:
            if not (entry / ".git").exists():
                continue
            key = str(entry)
            try:
                head = proc.check_output(
                    ["git", "-C", str(entry), "rev-parse", "HEAD"],
                    encoding="utf-8", errors="replace",
                    timeout=2.0, stderr=subprocess.DEVNULL,
                ).strip()
            except (subprocess.SubprocessError, FileNotFoundError):
                continue
            # Pushes are scanned independently of the commit dedup below. A push
            # normally lands on a tick where HEAD has not moved since its
            # commits were already counted, so if this sat after the
            # `seen == head` short-circuit it would almost never run.
            out.extend(self._scan_pushes(entry, key, world, now))
            seen = world.seen_commits.get(key)
            if seen == head:
                continue
            if seen is None:
                # First time we've seen this repo: baseline at HEAD and emit
                # nothing. The tank notices commits from when it STARTS watching
                # -- otherwise a fresh install spawns a fish for every commit in
                # every repo's whole history (that's the 1358-fish bug).
                world.seen_commits[key] = head
                continue
            try:
                rev_range = f"{seen}..{head}"
                log = proc.check_output(
                    ["git", "-C", str(entry), "log", rev_range,
                     "--format=%H%x09%cI%x09%s"],
                    encoding="utf-8", errors="replace",
                    timeout=3.0, stderr=subprocess.DEVNULL,
                ).strip().splitlines()
            except subprocess.SubprocessError:
                world.seen_commits[key] = head
                continue
            for line in log:
                if "\t" not in line:
                    continue
                sha, iso, subject = line.split("\t", 2)
                at = dt.datetime.fromisoformat(iso)
                if at.tzinfo is None:
                    at = at.replace(tzinfo=dt.timezone.utc)
                if SHIP_RE.match(subject):
                    kind = "ship"          # ship wins ties; see MERGE_RE
                elif MERGE_RE.search(subject):
                    kind = "pr_merge"
                else:
                    kind = "commit"
                out.append(Event(kind=kind, project=entry.name,
                                 detail=sha, at=at))
            world.seen_commits[key] = head
        return out

    def _scan_pushes(self, entry: Path, key: str, world: World,
                     now: dt.datetime) -> list[Event]:
        """Emit an event per ``git push`` that landed since the last scan.

        Reads the reflogs of EVERY remote-tracking ref, not just the current
        branch's push destination. That choice was measured on this estate
        rather than assumed: one repo's checked-out branch had no upstream at
        all, which makes ``git rev-parse HEAD@{push}`` a fatal error and takes a
        HEAD-only reader blind across the whole repo; another's most recent push
        sat on a branch nobody was standing on; a third had 83 pushes spread
        over 39 refs. Work gets pushed from branches you are not on.

        Dedup is a per-repo high-water mark on the reflog timestamp rather than
        a ref tip, because "the newest sha" stops being a single answer once
        several refs are in play. The first sighting of a repo baselines and
        emits nothing — the tank notices pushes from when it starts watching,
        never a reflog's history.

        Two pushes inside the same second collapse into one event, since reflog
        stamps have one-second resolution. That is the safe direction to fail:
        one fish too few, never a duplicate.

        Best-effort throughout. A repo with no remote, no reflog, or no git at
        all returns an empty list rather than raising — this runs inside a
        headless tick where an exception would take the whole aquarium down.
        """
        try:
            log = proc.check_output(
                ["git", "-C", str(entry), "log", "-g",
                 "--date=iso-strict", "--format=%gD%x09%gs%x09%H",
                 "--glob=refs/remotes/*"],
                encoding="utf-8", errors="replace",
                timeout=3.0, stderr=subprocess.DEVNULL,
            ).splitlines()
        except (subprocess.SubprocessError, FileNotFoundError):
            return []

        pushes: list[tuple[dt.datetime, str]] = []
        for line in log:
            selector, _, rest = line.partition("\t")
            subject, _, sha = rest.partition("\t")
            if not subject.startswith(PUSH_REFLOG_SUBJECT):
                continue
            at = _reflog_time(selector)
            if at is not None:
                pushes.append((at, sha.strip()))

        newest = max((at for at, _ in pushes), default=None)
        seen = world.seen_pushes.get(key)
        if seen is None:
            world.seen_pushes[key] = _baseline(newest, now).isoformat()
            return []
        try:
            watermark = dt.datetime.fromisoformat(seen)
        except (TypeError, ValueError):
            # A hand-edited or future-format mark: re-baseline rather than
            # replay a reflog's worth of history into the tank.
            world.seen_pushes[key] = _baseline(newest, now).isoformat()
            return []

        fresh = sorted((p for p in pushes if p[0] > watermark),
                       key=lambda p: p[0], reverse=True)
        if newest is not None and newest > watermark:
            world.seen_pushes[key] = newest.isoformat()
        return [Event(kind="push", project=entry.name, detail=sha, at=at)
                for at, sha in fresh[:MAX_PUSH_EVENTS_PER_REPO]]

    def _scan_notes(self, world: World) -> list[Event]:
        out: list[Event] = []
        if not self.notes_dir.exists():
            return out
        for note in self.notes_dir.iterdir():
            if not note.is_file():
                continue
            if note.name in world.seen_notes:
                continue
            world.seen_notes.add(note.name)
            mtime = dt.datetime.fromtimestamp(
                note.stat().st_mtime, tz=dt.timezone.utc
            )
            # Baseline pre-existing notes silently; only notes written after the
            # tank started watching spawn a notefish.
            if mtime >= world.created_at:
                out.append(Event(kind="note_written", project=None,
                                 detail=note.name, at=mtime))
        return out
