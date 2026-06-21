"""Tank mood — one felt word from weather + phase + tick flux."""
from __future__ import annotations

import datetime as dt

from tank.models import Event, Weather
from tank.mood import compute


def _weather(phase="day", pressure=0.0, current=0.0, light=0.8, silt=0.0):
    return Weather(
        temperature_c=22.0, current_strength=current, silt_density=silt,
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


def test_heavy_silt_quiet_tank_is_murky():
    # A thick, settled tank with nothing else going on feels cloudy.
    m = compute(_weather(silt=0.85), events=[], births=[], deaths=[])
    assert m == "murky"


def test_murky_needs_a_quiet_tank():
    # Heavy silt during real churn does not mask the churn.
    evs = [_event() for _ in range(3)]
    m = compute(_weather(silt=0.9), events=evs, births=[], deaths=[])
    assert m == "restless"


def test_clear_water_is_not_murky():
    # Low silt never reads as murky, even in a quiet tank.
    m = compute(_weather(silt=0.3, light=0.8), events=[], births=[], deaths=[])
    assert m == "calm"


def test_murky_outranks_drowsy():
    # A dim *and* heavily-silted quiet tank reads murky, not merely drowsy:
    # the cloud is the more specific, more felt signal.
    m = compute(_weather(phase="night", light=0.2, silt=0.85),
                events=[], births=[], deaths=[])
    assert m == "murky"


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
