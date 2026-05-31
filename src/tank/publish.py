"""Publish a sanitized, public-safe snapshot of the tank to a remote endpoint.

This module is the trust boundary between the local tank (which knows your
project names, commit hashes, and seal filenames) and any public surface. The
snapshot is built by ALLOW-LIST: we construct a fresh dict of named safe fields,
so a field added to World later cannot leak by default — it simply won't be
copied. Everything name/provenance/project/history-shaped is left behind.

Publishing is opt-in and best-effort: if the network call fails, the tick still
succeeds; we only log.
"""
from __future__ import annotations

import json
import logging
import urllib.request

from tank.models import World

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA = 1


def to_public_snapshot(world: World) -> dict:
    """Build the public, sanitized view of the world.

    Safe (rendered on the page): per-fish species/glyph/mood, weather + phase +
    mood, fish count, fossil glyphs, last-tick time.
    Withheld (never copied): fish name/provenance/project, seen_commits/seals/
    projects, anything that embeds a real project, commit, or seal identity.
    """
    w = world.weather
    return {
        "schema": SNAPSHOT_SCHEMA,
        "tick_at": world.last_tick_at.isoformat(),
        "fish_count": len(world.fish),
        "fish": [
            {"species": f.species, "glyph": f.glyph, "mood": f.mood}
            for f in world.fish
        ],
        "weather": {
            "temperature_c": w.temperature_c,
            "current_strength": w.current_strength,
            "silt_density": w.silt_density,
            "light_level": w.light_level,
            "pressure": w.pressure,
            "phase": w.phase,
            "mood": w.mood,
        },
        "fossil_layer": list(w.fossil_layer),
    }


def publish(snapshot: dict, url: str, token: str, timeout: float = 5.0) -> bool:
    """POST the snapshot as JSON with a bearer token. Best-effort.

    Returns True on a 2xx response, False on any failure (never raises).
    """
    data = json.dumps(snapshot).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= status < 300
    except Exception as e:  # network down, timeout, HTTP error — all non-fatal
        logger.warning("tank publish failed: %s", e)
        return False


def publish_gist(snapshot: dict, gist_id: str, token: str,
                 filename: str = "tank.json", timeout: float = 5.0) -> bool:
    """Write the snapshot into a GitHub Gist file via the GitHub API. Best-effort.

    The gist is the shared store: the local tank writes it, the public page reads
    it. The snapshot is already sanitized, so a public gist is safe. Returns True
    on a 2xx response, False on any failure (never raises).
    """
    body = json.dumps(
        {"files": {filename: {"content": json.dumps(snapshot)}}}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/gists/{gist_id}", data=body, method="PATCH")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "fish-tank")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= status < 300
    except Exception as e:
        logger.warning("tank gist publish failed: %s", e)
        return False
