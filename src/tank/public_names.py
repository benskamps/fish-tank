"""Resolve which projects may be publicly named in the tank snapshot.

A project (a repo directory) is "public-nameable" when its git remote points at
a PUBLIC GitHub repo, OR it is listed in the user's manual allow-list. Anything
public is safe to name on the live page; everything else stays anonymous.

GitHub detection uses the `gh` CLI and is cached (default 24h) so the tick never
hammers the API. If `gh` or git isn't available, detection degrades to the manual
list only — never fatal.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from tank import paths, proc

logger = logging.getLogger(__name__)

CACHE_TTL_S = 24 * 3600
_REMOTE_RE = re.compile(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$", re.IGNORECASE)


def _run(args: list[str], timeout: float = 8.0) -> str:
    """Default command runner: return stdout, or '' on any failure."""
    try:
        r = proc.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _owner_repo(remote_url: str) -> tuple[str, str] | None:
    m = _REMOTE_RE.search(remote_url.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def detect_public(dirs, run=_run) -> dict[str, str]:
    """Map {dir_name: repo_name} for dirs whose origin is a PUBLIC GitHub repo."""
    out: dict[str, str] = {}
    for d in dirs:
        if not (d / ".git").exists():
            continue
        url = run(["git", "-C", str(d), "remote", "get-url", "origin"]).strip()
        owner_repo = _owner_repo(url)
        if not owner_repo:
            continue
        owner, repo = owner_repo
        vis = run(["gh", "repo", "view", f"{owner}/{repo}",
                   "--json", "visibility", "-q", ".visibility"]).strip()
        if vis.upper() == "PUBLIC":
            out[d.name] = repo
    return out


def _candidate_dirs(projects_root, watch: set[str]):
    if not projects_root or not projects_root.exists():
        return []
    dirs = [e for e in projects_root.iterdir() if e.is_dir()]
    if watch:
        dirs = [e for e in dirs if e.name in watch]
    return dirs


def resolve(projects_root, watch, manual, *, run=_run,
            cache_path=None, ttl=CACHE_TTL_S, now=None) -> dict[str, str]:
    """Return the {project: label} allow-list. Cached; refreshes via gh when stale.

    Always includes the `manual` map (user's explicit allow-list). The manual
    entries win over auto-detected labels."""
    now = now if now is not None else time.time()
    manual = dict(manual or {})
    cache_path = cache_path or (paths.tank_home() / "public_names.json")
    watch = set(watch or [])

    cached, stamp = _read_cache(cache_path)
    if cached is not None and (now - stamp) < ttl:
        return {**cached, **manual}

    # Stale or missing — best-effort refresh.
    try:
        auto = detect_public(_candidate_dirs(projects_root, watch), run=run)
        _write_cache(cache_path, auto, now)
        return {**auto, **manual}
    except Exception as e:  # never let naming break the tick
        logger.warning("public-name refresh failed: %s", e)
        return {**(cached or {}), **manual}


def _read_cache(path) -> tuple[dict | None, float]:
    try:
        if not path.exists():
            return None, 0.0
        raw = json.loads(path.read_text(encoding="utf-8"))
        return dict(raw.get("names", {})), float(raw.get("resolved_at", 0.0))
    except Exception:
        return None, 0.0


def _write_cache(path, names: dict, now: float) -> None:
    try:
        paths.ensure_dirs()
        path.write_text(json.dumps({"resolved_at": now, "names": names}, indent=2),
                        encoding="utf-8")
    except Exception as e:
        logger.warning("public-name cache write failed: %s", e)
