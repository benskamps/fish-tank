"""Circadian rhythm — maps local wall-clock time to a day/night phase.

The tank's clock is UTC-aware (see clock.py); this module converts to the
machine's *local* time so the tank's day matches Ben's day. Naive datetimes are
treated as local wall time, which keeps tests stable on any machine.
"""
from __future__ import annotations

import datetime as dt

# Base light multiplier per phase: day is full light, the witching hour is darkest.
PHASE_LIGHT = {
    "day": 1.0,
    "dusk": 0.6,
    "dawn": 0.55,
    "night": 0.3,
    "witching": 0.2,
}


def _local(now: dt.datetime) -> dt.datetime:
    """Return `now` as local wall time.

    `.astimezone()` treats a naive datetime as local and converts an aware one
    (e.g. the UTC clock) into local time. Either way we get local wall hours.
    """
    return now.astimezone()


def phase_for(now: dt.datetime) -> str:
    """Local hour -> phase name. See PHASE_LIGHT for the vocabulary."""
    h = _local(now).hour
    if 0 <= h < 3:
        return "witching"
    if 3 <= h < 5:
        return "night"
    if 5 <= h < 7:
        return "dawn"
    if 7 <= h < 18:
        return "day"
    if 18 <= h < 21:
        return "dusk"
    return "night"  # 21:00–24:00


def is_witching(now: dt.datetime) -> bool:
    """True only during 00:00–03:00 local — the up-too-late window."""
    return phase_for(now) == "witching"


def circadian_light(phase: str) -> float:
    """Base light multiplier for a phase; unknown phases fall back to full light."""
    return PHASE_LIGHT.get(phase, 1.0)
