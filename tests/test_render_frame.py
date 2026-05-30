import datetime as dt
from pathlib import Path

from tank.models import Fish, Weather, World
from tank.render.frame import compose, render

FIXTURES = Path(__file__).parent / "fixtures" / "frames"


def _empty_world():
    now = dt.datetime(2026, 5, 14, 22, 0, 0, tzinfo=dt.timezone.utc)
    return World(
        schema_version=1, created_at=now, last_tick_at=now, fish=[],
        weather=Weather(22.0, 0.2, 0.1, 0.7, 0.1, []),
        seen_commits={}, seen_seals=set(), seen_projects=set(),
        config_overrides={},
    )


def _with_fish():
    w = _empty_world()
    w.fish = [
        Fish(id="a", name="Pip", species="guppy", glyph=">°))<",
             born_at=w.created_at, lifespan_days=15.0,
             provenance="manual:adopt", project=None, mood="calm",
             last_position=(10, 5)),
        Fish(id="b", name="Marlow", species="coldfin", glyph="<°)))><",
             born_at=w.created_at, lifespan_days=30.0,
             provenance="bestiary:coldfin", project=None, mood="sleeping",
             last_position=(20, 7)),
    ]
    w.weather.fossil_layer = ["·", "✦"]
    return w


def test_compose_empty_tank_has_expected_height():
    frame = compose(_empty_world(), width=56, height=14)
    out = render(frame, style="plain")
    assert len(out.strip().splitlines()) >= 10


def test_compose_with_fish_includes_glyphs():
    out = render(compose(_with_fish()), style="plain")
    assert ">°))<" in out
    assert "<°)))><" in out


def test_render_line_is_single_line():
    out = render(compose(_with_fish()), style="line")
    assert "\n" not in out.strip()
    assert "2 fish" in out


def test_fossil_layer_appears_in_output():
    out = render(compose(_with_fish()), style="plain")
    assert "·" in out or "✦" in out


def test_plain_style_has_no_ansi_escapes():
    out = render(compose(_with_fish()), style="plain")
    assert "\x1b[" not in out


def test_mood_line_appears_in_plain():
    w = _empty_world()
    w.weather.mood = "haunted"
    out = render(compose(w), style="plain")
    assert "the tank feels: haunted" in out


def test_phase_glyph_and_name_render():
    w = _empty_world()
    w.weather.phase = "witching"
    out = render(compose(w), style="plain")
    assert "☾" in out            # moon in the header
    assert "witching" in out     # phase named in the sensor strip


def test_witching_surface_is_dotted_not_rippled():
    w = _empty_world()
    w.weather.phase = "witching"
    frame = compose(w)
    surface = "".join(frame.water)
    assert "·" in surface and "~" not in surface


def test_snapshot_empty(tmp_path):
    frame = compose(_empty_world(), width=56, height=14)
    out = render(frame, style="plain")
    fixture = FIXTURES / "empty_tank.txt"
    if not fixture.exists():
        FIXTURES.mkdir(parents=True, exist_ok=True)
        fixture.write_text(out, encoding="utf-8")
    assert out == fixture.read_text(encoding="utf-8")
