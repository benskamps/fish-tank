"""Circadian phase mapping — local-time aware, machine-stable."""
from __future__ import annotations

import datetime as dt

import pytest

from tank.circadian import circadian_light, is_witching, phase_for


def _local(hour: int, minute: int = 0) -> dt.datetime:
    """Naive datetime — phase_for treats naive as local wall time."""
    return dt.datetime(2026, 5, 29, hour, minute)


@pytest.mark.parametrize(
    "hour,expected",
    [
        (0, "witching"),
        (1, "witching"),
        (2, "witching"),
        (3, "night"),
        (4, "night"),
        (5, "dawn"),
        (6, "dawn"),
        (7, "day"),
        (12, "day"),
        (17, "day"),
        (18, "dusk"),
        (20, "dusk"),
        (21, "night"),
        (23, "night"),
    ],
)
def test_phase_for_each_hour(hour, expected):
    assert phase_for(_local(hour)) == expected


def test_is_witching_only_midnight_to_three():
    assert is_witching(_local(0, 1))
    assert is_witching(_local(2, 59))
    assert not is_witching(_local(3, 0))
    assert not is_witching(_local(23, 59))
    assert not is_witching(_local(12))


def test_circadian_light_ordering():
    # day brightest, witching darkest
    assert circadian_light("day") == 1.0
    assert circadian_light("witching") < circadian_light("night")
    assert circadian_light("night") < circadian_light("dusk")
    assert circadian_light("dusk") < circadian_light("day")
    # unknown phase falls back to full light, never crashes
    assert circadian_light("nonsense") == 1.0


def test_phase_for_handles_utc_aware_input():
    # Real clock hands us UTC-aware datetimes; phase_for must convert to local
    # and return a valid phase without raising.
    aware = dt.datetime(2026, 5, 29, 6, 0, tzinfo=dt.timezone.utc)
    assert phase_for(aware) in {
        "witching", "night", "dawn", "day", "dusk",
    }
