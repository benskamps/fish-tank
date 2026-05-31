"""Scans real-world surfaces for events: git commits, seals, new projects."""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import subprocess
from pathlib import Path

from tank import proc
from tank.models import Event, World

logger = logging.getLogger(__name__)

SHIP_RE = re.compile(r"^(ship|release|chore: release|version bump)\b", re.IGNORECASE)


class Observer:
    def __init__(
        self,
        projects_root: Path | None = None,
        seals_dir: Path | None = None,
    ):
        home = Path.home()
        env_projects = os.environ.get("TANK_PROJECTS_ROOT")
        env_seals = os.environ.get("TANK_SEALS_DIR")
        self.projects_root = projects_root or (
            Path(env_projects) if env_projects else (home / "projects")
        )
        # "Seals" are optional session-journal markdown files — point
        # TANK_SEALS_DIR at your own journal/log dir to feed witnessfish.
        # Defaults to ~/seals; if it doesn't exist, seal scanning no-ops.
        self.seals_dir = seals_dir or (
            Path(env_seals) if env_seals else (home / "seals")
        )

    def scan_since(self, since: dt.datetime, world: World) -> list[Event]:
        events: list[Event] = []
        events.extend(self._scan_projects(world))
        events.extend(self._scan_git(world))
        events.extend(self._scan_seals(world))
        return events

    def _scan_projects(self, world: World) -> list[Event]:
        out: list[Event] = []
        if not self.projects_root.exists():
            return out
        for entry in self.projects_root.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            if entry.name in world.seen_projects:
                continue
            world.seen_projects.add(entry.name)
            ctime = dt.datetime.fromtimestamp(
                entry.stat().st_ctime, tz=dt.timezone.utc
            )
            out.append(Event(kind="new_project", project=entry.name,
                             detail=str(entry), at=ctime))
        return out

    def _scan_git(self, world: World) -> list[Event]:
        out: list[Event] = []
        if not self.projects_root.exists():
            return out
        for entry in self.projects_root.iterdir():
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
            try:
                rev_range = f"{seen}..{head}" if seen else head
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

    def _scan_seals(self, world: World) -> list[Event]:
        out: list[Event] = []
        if not self.seals_dir.exists():
            return out
        for seal in self.seals_dir.iterdir():
            if not seal.is_file():
                continue
            if seal.name in world.seen_seals:
                continue
            world.seen_seals.add(seal.name)
            mtime = dt.datetime.fromtimestamp(
                seal.stat().st_mtime, tz=dt.timezone.utc
            )
            out.append(Event(kind="seal_written", project=None,
                             detail=seal.name, at=mtime))
        return out
