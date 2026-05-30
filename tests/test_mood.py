"""Tank mood — one felt word from weather + phase + tick flux."""
from __future__ import annotations

import datetime as dt

from tank.models import Event, Weather
from tank.mood import compute


def _weather(phase="day", pressure=0.0, current=0.0, light=0.8):
    return Weather(
        temperature_c=22.0, current_strength=current, silt_density=0.0,
        light_level=light, pressure=pressure, fossil_layer=[], phase=phase,
    )


def _event(kind="commit"):
    return Event(kind=kind, project="p", detail="d", at=dt.datetime(2026, 5, 29))


def test_ship_event_is_jubilant():
    m = compute(_weather(), events=[_event("ship")], births=[], deaths=[])
    assert m == "jubilant"


def test_multiple_births_is_jubilant():
    m = compute(_weather(), events=[], births=["a", "b"], deaths=[])
    assert m == "jubilant"


def test_high_load_and_current_is_electric():
    m = compute(_weather(pressure=0.8, current=0.7), events=[], births=[], deaths=[])
    assert m == "electric"


def test_witching_idle_is_haunted():
    m = compute(_weather(phase="witching", light=0.15), events=[], births=[], deaths=[])
    assert m == "haunted"


def test_event_churn_is_restless():
    evs = [_event() for _ in range(3)]
    m = compute(_weather(), events=evs, births=[], deaths=[])
    assert m == "restless"


def test_a_death_is_restless():
    m = compute(_weather(), events=[], births=[], deaths=["d"])
    assert m == "restless"


def test_dim_settled_night_is_drowsy():
    m = compute(_weather(phase="night", light=0.2), events=[], births=[], deaths=[])
    assert m == "drowsy"


def test_quiet_daytime_is_calm():
    m = compute(_weather(phase="day", light=0.8), events=[], births=[], deaths=[])
    assert m == "calm"


def test_ship_at_witching_still_jubilant():
    # A real win outranks the late-night dark.
    m = compute(_weather(phase="witching", light=0.15),
                events=[_event("ship")], births=[], deaths=[])
    assert m == "jubilant"
