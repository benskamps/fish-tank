"""Tank mood — distills the tick's signals into one felt word.

Pure and rule-based: weather (already carrying its circadian phase) plus the
events, births, and deaths from this tick. Priority-ordered, first match wins.
A real win (a ship, new life) outranks the late-night dark; the witching hour
outranks mere churn.
"""
from __future__ import annotations

from tank.models import Event, Weather

# Vocabulary, brightest-to-darkest in spirit:
# jubilant, electric, haunted, restless, drowsy, calm.


def compute(weather: Weather, *, events: list[Event], births: list,
            deaths: list) -> str:
    # 1. Something was born or shipped — celebration outranks everything.
    if any(getattr(e, "kind", None) == "ship" for e in events) or len(births) >= 2:
        return "jubilant"
    # 2. The machine is roaring — high load and strong current together.
    if weather.pressure > 0.5 and weather.current_strength > 0.5:
        return "electric"
    # 3. The up-too-late hour. Quiet or busy, 2am has its own weight.
    if weather.phase == "witching":
        return "haunted"
    # 4. Churn — lots happening, or a loss this tick.
    if len(events) >= 3 or len(deaths) >= 1:
        return "restless"
    # 5. Dim and settled.
    if weather.light_level < 0.35:
        return "drowsy"
    # 6. Default: a calm tank.
    return "calm"
