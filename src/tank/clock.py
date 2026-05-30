"""Injectable time source so tests can simulate weeks in seconds."""
from __future__ import annotations

import datetime as dt
from typing import Protocol


class ClockProtocol(Protocol):
    def now(self) -> dt.datetime: ...


class Clock:
    """Real wall clock, UTC-aware."""

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)


class FakeClock:
    """Test clock — manually advance via `advance(timedelta)`."""

    def __init__(self, start: dt.datetime):
        if start.tzinfo is None:
            raise ValueError("FakeClock requires a timezone-aware datetime.")
        self._now = start

    def now(self) -> dt.datetime:
        return self._now

    def advance(self, delta: dt.timedelta) -> None:
        self._now += delta
