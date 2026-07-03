import datetime as dt
import json
import threading
import time
import urllib.request

from tank.clock import FakeClock
from tank.serve import serve
from tank.tick import TickEngine


def _fetch(port, path):
    url = f"http://localhost:{port}{path}"
    with urllib.request.urlopen(url, timeout=2.0) as r:
        return r.read().decode("utf-8")


def _start_server(port):
    """Start a real (non one-shot) serve loop in a daemon thread.

    The daemon thread dies with the test process, so there's nothing to tear
    down — and unlike ``_one_shot`` it can answer both ``/tank`` and
    ``/tank.json`` in the same test.
    """
    t = threading.Thread(target=serve,
                         kwargs={"port": port},
                         daemon=True)
    t.start()
    time.sleep(0.3)
    return t


def test_serve_returns_html_with_pre_tag(tmp_tank_dir):
    TickEngine(clock=FakeClock(
        dt.datetime(2026, 5, 14, tzinfo=dt.timezone.utc)
    )).run_once()

    port = 7333
    t = threading.Thread(target=serve,
                         kwargs={"port": port, "_one_shot": True},
                         daemon=True)
    t.start()
    time.sleep(0.3)

    with urllib.request.urlopen(f"http://localhost:{port}/tank", timeout=2.0) as r:
        body = r.read().decode("utf-8")
    assert "<pre>" in body
    assert "</pre>" in body
    assert "tank" in body.lower()


def test_serve_ships_full_aquarium_renderer(tmp_tank_dir):
    """/tank serves the full brokenbranch.dev/aquarium renderer, and still
    carries the static ASCII tank in a <noscript> so the terminal soul
    survives with JS off (v0.8.0 parity)."""
    TickEngine(clock=FakeClock(
        dt.datetime(2026, 5, 14, tzinfo=dt.timezone.utc)
    )).run_once()

    port = 7334
    _start_server(port)
    body = _fetch(port, "/tank")

    # The animated renderer is present and wired to this server's data feed.
    assert "requestAnimationFrame" in body
    assert "BBTank" in body
    assert "/tank.json" in body
    # …and the terminal fallback is still there for JS-off viewers.
    assert "<noscript>" in body
    assert "<pre>" in body and "</pre>" in body


def test_serve_tank_json_snapshot_schema(tmp_tank_dir):
    """/tank.json returns the sanitized snapshot the renderer expects:
    a nested weather block, a flat fish roster, a count, the fossil layer,
    and a tick stamp."""
    TickEngine(clock=FakeClock(
        dt.datetime(2026, 5, 14, tzinfo=dt.timezone.utc)
    )).run_once()

    port = 7335
    _start_server(port)
    snap = json.loads(_fetch(port, "/tank.json"))

    assert set(("weather", "fish", "fish_count", "fossil_layer", "tick_at")) \
        <= set(snap)
    weather = snap["weather"]
    for key in ("phase", "temperature_c", "current_strength",
                "silt_density", "light_level", "mood"):
        assert key in weather, key
    assert isinstance(snap["fish"], list)
    assert snap["fish_count"] == len(snap["fish"])
    if snap["fish"]:
        first = snap["fish"][0]
        for key in ("species", "glyph", "zone", "mood"):
            assert key in first, key


def test_serve_tank_json_warms_up_without_a_world(tmp_tank_dir):
    """With no world file yet, /tank.json degrades to the 'warming up'
    sentinel the renderer settles on instead of crashing the request."""
    port = 7336
    _start_server(port)
    snap = json.loads(_fetch(port, "/tank.json"))
    assert snap == {"empty": True}
