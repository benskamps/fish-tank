import datetime as dt
import json
import re
import threading
import time
import urllib.request

from tank.bestiary import load_bundled
from tank.clock import FakeClock
from tank.serve import _PAGE, serve
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


# ---------------------------------------------------------------------------
# The renderer's species tables
# ---------------------------------------------------------------------------
#
# bestiary.yaml does NOT drive the renderer. serve.py carries its own JS maps —
# the field guide, per-species size, glow, pace, anchoring — and a species that
# is in the bestiary but absent from them renders at every default: 0.8rem, no
# colour, no legend row. Present, but not a creature anyone can tell apart.
# That is precisely what happened when notefish was introduced, and it took a
# companion fix in a different repo to notice. These tests make the tables a
# thing a test can hold to the bestiary, instead of a list someone must
# remember to update.


def _js_block(page: str, name: str) -> str:
    """The body of a `var NAME = { … };` literal in the served page."""
    # Some of these literals are column-aligned (`var CALM  = {`), so the
    # spacing around `=` is not fixed.
    head = r"var " + re.escape(name) + r"\s*=\s*\{"
    m = re.search(head + r"(.*?)\n  \};", page, re.S) \
        or re.search(head + r"(.*?)\};", page, re.S)
    assert m, f"no {name} map in the served page"
    return m.group(1)


def _has_key(block: str, key: str) -> bool:
    return bool(re.search(r"(?:^|[{,\s])'?" + re.escape(key) + r"'?\s*:",
                          block, re.M))


def test_every_bundled_species_has_a_render_entry():
    """THE guard: no species may reach the water without a look of its own.

    Holds for all 20 species on master today, so it starts green for a reason
    rather than by being vacuous — add a species to bestiary.yaml and forget
    the renderer, and this is what tells you.
    """
    page = _PAGE
    guide, size, glow = (_js_block(page, "SPECIES"),
                         _js_block(page, "SIZE"),
                         _js_block(page, "BIO_WEIGHT"))
    missing = {
        "SPECIES": [k for k in load_bundled() if not _has_key(guide, k)],
        "SIZE": [k for k in load_bundled() if not _has_key(size, k)],
        "BIO_WEIGHT": [k for k in load_bundled() if not _has_key(glow, k)],
    }
    assert not any(missing.values()), f"species missing render entries: {missing}"


def test_pushfish_and_mergefish_are_visibly_distinct():
    """Not just present — different to look at, and different to watch.

    Size and colour separate them at a glance; pace and anchoring separate them
    in motion. A push streaks through; a merge settles in and holds its place
    the way the other landmark project fish do.
    """
    page = _PAGE
    size = _js_block(page, "SIZE")

    push_size = float(re.search(r"pushfish:\s*([0-9.]+)", size).group(1))
    merge_size = float(re.search(r"mergefish:\s*([0-9.]+)", size).group(1))
    # A landed PR reads bigger than the push that carried it.
    assert merge_size > push_size

    # The merge holds station with the other landmark project fish; the push
    # streaks past. Anchored species carry no CROSS pace by house convention —
    # shipfish, founderfish and notefish are all absent from it — so the pace
    # belongs to the swimmer alone.
    assert _has_key(_js_block(page, "ANCHOR"), "mergefish")
    assert not _has_key(_js_block(page, "ANCHOR"), "pushfish")
    assert _has_key(_js_block(page, "DARTY"), "pushfish")
    assert _has_key(_js_block(page, "CALM"), "mergefish")
    push_cross = float(re.search(r"pushfish:\s*([0-9.]+)",
                                 _js_block(page, "CROSS")).group(1))
    assert push_cross < 22          # quicker than the renderer's default

    # Each wears its own colour.
    assert ".fish.pushfish" in page
    assert ".fish.mergefish" in page
