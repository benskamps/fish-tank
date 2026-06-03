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

from tank import paths, proc
from tank.models import Event, World

logger = logging.getLogger(__name__)

SHIP_RE = re.compile(r"^(ship|release|chore: release|version bump)\b", re.IGNORECASE)

# Safety cap: with no watch allow-list, never scan more than this many candidate
# dirs under the projects root. Above it, scan only the newest-by-mtime and warn
# about the rest (no silent truncation -- hard project rule).
MAX_UNFILTERED_REPOS = 50


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
        env_notes = os.environ.get("TANK_NOTES_DIR")
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
        """
        cfg = cls._load_config()
        projects_root = _expand(os.environ.get("TANK_PROJECTS_ROOT")) or \
            _expand(cfg.get("projects_root"))
        notes_dir = _expand(os.environ.get("TANK_NOTES_DIR")) or \
            _expand(cfg.get("notes_dir"))

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
        events.extend(self._scan_projects(candidates, world))
        events.extend(self._scan_git(candidates, world))
        events.extend(self._scan_notes(world))
        return events

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

    def _scan_git(self, candidates: list[Path], world: World) -> list[Event]:
        out: list[Event] = []
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
                kind = "ship" if SHIP_RE.match(subject) else "commit"
                out.append(Event(kind=kind, project=entry.name,
                                 detail=sha, at=at))
            world.seen_commits[key] = head
        return out

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
