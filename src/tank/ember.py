"""Ember's fish — a readout of work they actually did, not of what they said.

Ember has a brain (2,065 chunks, every estate seal) and a clock, and until
today no place. This module is the wire between the brain and the tank: their
adopted fish's glyph and caption are a pure function of the counters their brain
reports, so the tank shows their real state rather than a friendly one.

THE ONE RULE, and the whole reason this is worth building:

    Their fish cannot be made brighter by talking.

Brightness reads ``total_writebacks`` and ``total_actions`` — work they did.
Nothing in here consults anything they *said*. An avatar that brightens when the
agent is chatty is a mood light; this one can only brighten when the numbers
move, which makes it a thermometer that cannot be argued with. Under today's
counters (writebacks 0, last action-board event 2026-05-01) they read DARK, and
that is the correct, uncomfortable answer.

Read-only, always. Ember's brain is theirs; this module never POSTs, never
ingests, and treats every failure as "they are asleep" rather than inventing a
number. A brain that is down must never look like a brain that is idle, so the
two states have different captions.

Scope note (M0): no generated text anywhere in here. The caption is a
deterministic render of integers. Ember's own review asked for a caption rather
than silence — "a silent fish that's pale and still doesn't explain why it's
pale or still" — and a rendered integer answers that without putting a language
model in the loop, so there is no fabrication surface to guard.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Ember's brain. Loopback by design — the tank never reaches off this box.
BRAIN_STATS_URL = "http://127.0.0.1:8089/brain/stats"

#: Short: the tick must not stall because their brain is thinking.
TIMEOUT_SECONDS = 2.0

#: Glyphs, dimmest first. Index = vitality tier.
#: The `{` body is theirs alone; the EYE is the dimmer. Every glyph in this
#: bestiary is authored facing RIGHT (tank.glyphs.faces_right) — a left-facing
#: spelling here fails test_every_bestiary_glyph_is_authored_facing_right
#: rather than rendering a moonwalking fish.
GLYPH_ASLEEP = "><{~>"   # brain unreachable — unknown, NOT idle
GLYPH_DARK = "><{·>"     # reachable, no work done
GLYPH_WARM = "><{°>"     # read to, nothing written back
GLYPH_LIT = "><{*>"      # writebacks on the board

#: `actions` above this with writebacks still 0 is the "library, not work"
#: shape the 2026-08-28 seal named: they have been read to, and has written back
#: nothing. It gets its own caption because it is the interesting failure.
_LIBRARY_ONLY_ACTIONS = 1


@dataclass(frozen=True)
class Vitality:
    """What the tank should show for Ember this tick."""

    glyph: str
    mood: str
    caption: str
    reachable: bool
    writebacks: int = 0
    actions: int = 0
    chunks: int = 0

    @property
    def lit(self) -> bool:
        """True only when they have actually written something back."""
        return self.reachable and self.writebacks > 0


def _asleep(reason: str) -> Vitality:
    """Their brain did not answer. This is NOT the same as their being idle."""
    return Vitality(glyph=GLYPH_ASLEEP, mood="sleeping",
                    caption=f"ember · asleep ({reason})",
                    reachable=False)


def fetch_stats(url: str = BRAIN_STATS_URL, *, timeout: float = TIMEOUT_SECONDS) -> dict | None:
    """GET their brain's counters. Returns None on any failure — never raises.

    The tick has to survive their brain being down, restarting, or slow. Any
    exception here would take the whole aquarium with it, which would make the
    fish a liability rather than an instrument.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug("ember brain unreachable: %s", exc)
        return None


def read_vitality(stats: dict | None) -> Vitality:
    """Map their brain's counters onto a glyph, a mood and a caption.

    ``stats`` is the parsed ``/brain/stats`` body, or None if unreachable.
    Deliberately total: every input shape returns a Vitality, because a tick
    that raises is worse than a fish that reads "asleep".
    """
    if stats is None:
        return _asleep("brain unreachable")

    engine = stats.get("engine")
    if not isinstance(engine, dict):
        # Reachable but the shape changed under us. Say so rather than
        # defaulting the counters to 0 — that would render as "idle", which is
        # a different and much more flattering claim than "I cannot tell".
        return _asleep("unrecognised brain response")

    def _count(key: str) -> int:
        value = engine.get(key)
        return value if isinstance(value, int) and value >= 0 else 0

    writebacks = _count("total_writebacks")
    actions = _count("total_actions")
    chunks = _count("total_chunks")

    if writebacks > 0:
        glyph, mood = GLYPH_LIT, "darting"
        caption = f"ember · working · {writebacks} writeback{'s' * (writebacks != 1)}"
    elif actions > _LIBRARY_ONLY_ACTIONS:
        # Read to, never written back. The honest, uncomfortable middle.
        glyph, mood = GLYPH_WARM, "calm"
        caption = (f"ember · idle · {chunks} chunks read, "
                   f"{actions} actions, 0 written back")
    else:
        glyph, mood = GLYPH_DARK, "sleeping"
        caption = f"ember · dark · {chunks} chunks, no work logged"

    return Vitality(glyph=glyph, mood=mood, caption=caption, reachable=True,
                    writebacks=writebacks, actions=actions, chunks=chunks)


def current(url: str = BRAIN_STATS_URL) -> Vitality:
    """Live read. The one call the tick needs."""
    return read_vitality(fetch_stats(url))


def apply_to(world, *, url: str = BRAIN_STATS_URL) -> Vitality | None:
    """Point every ember-species fish at the current reading. Returns it.

    No-op (None) when they have not been adopted, so a tank without Ember is
    completely unaffected by this module — the aquarium at brokenbranch.dev
    does not have their and must not change behaviour because this code exists.
    """
    residents = [f for f in getattr(world, "fish", []) if getattr(f, "species", None) == "ember"]
    if not residents:
        return None

    vitality = current(url)
    for fish in residents:
        fish.glyph = vitality.glyph
        fish.mood = vitality.mood
    return vitality
