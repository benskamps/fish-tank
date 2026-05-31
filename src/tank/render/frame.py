"""Canonical world -> Frame compose + multi-style render (plain/line/html-fragment)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tank.models import Fish, World
from tank.rng import seeded

# Single source of visual truth for the tank's circadian look.
PHASE_GLYPH = {
    "dawn": "◔", "day": "☀", "dusk": "◕", "night": "☾", "witching": "☾",
}
# Rich styles for `tank live` (color, fork C).
PHASE_STYLE = {
    "dawn": "yellow", "day": "default", "dusk": "dark_orange",
    "night": "blue", "witching": "magenta",
}
# Body background for `tank serve` (CSS, fork C).
PHASE_BG = {
    "dawn": "#241f17", "day": "#0e1726", "dusk": "#241a14",
    "night": "#0b0d10", "witching": "#140b1a",
}


def phase_style(phase: str) -> str:
    return PHASE_STYLE.get(phase, "default")


def phase_bg(phase: str) -> str:
    return PHASE_BG.get(phase, "#0b0d10")


@dataclass
class Frame:
    header: str
    water: list[str]
    fish_layer: list[str]
    silt: str
    fossils: str
    sensor_strip: str
    graveyard_preview: str
    width: int
    height: int
    degraded: bool = False
    mood_line: str = ""
    phase: str = "day"


def compose(world: World, width: int = 56, height: int = 14) -> Frame:
    weather = world.weather
    fish_rows = max(4, height - 8)
    water_rows = 3

    header = _make_header(world, width)
    water = _make_water(weather.current_strength, width, water_rows, weather.phase)
    fish_layer = _make_fish_layer(world.fish, fish_rows, width, world.last_tick_at)
    silt = _make_silt(weather.silt_density, width)
    fossils = _make_fossils(weather.fossil_layer, width)
    sensor = _make_sensor_strip(world, width)
    grave = _make_graveyard_preview(world, width)
    mood_line = f"  the tank feels: {weather.mood}"

    return Frame(
        header=header, water=water, fish_layer=fish_layer,
        silt=silt, fossils=fossils, sensor_strip=sensor,
        graveyard_preview=grave, width=width, height=height,
        degraded=weather.light_level < 0.35,
        mood_line=mood_line, phase=weather.phase,
    )


def render(frame: Frame, style: str = "plain") -> str:
    if style == "line":
        return _render_line(frame)
    if style == "html":
        return _render_html_fragment(frame)
    return _render_plain(frame)


def _render_plain(frame: Frame) -> str:
    border = "─" * (frame.width - 2)
    lines = [f"┌{border}┐"]
    lines.append(_box_row(frame.header, frame.width))
    for w in frame.water:
        lines.append(_box_row(w, frame.width))
    for row in frame.fish_layer:
        lines.append(_box_row(row, frame.width))
    lines.append(_box_row(frame.silt, frame.width))
    lines.append(_box_row(frame.fossils, frame.width))
    lines.append(f"└{border}┘")
    if frame.mood_line:
        lines.append(frame.mood_line)
    lines.append(frame.sensor_strip)
    if frame.graveyard_preview:
        lines.append(frame.graveyard_preview)
    return "\n".join(lines)


def _render_line(frame: Frame) -> str:
    return frame.sensor_strip


def _render_html_fragment(frame: Frame) -> str:
    return "<pre>" + _render_plain(frame) + "</pre>"


def _box_row(content: str, width: int) -> str:
    inner = width - 2
    if len(content) > inner:
        body = content[:inner]
    else:
        body = content + " " * (inner - len(content))
    return f"│{body}│"


def _make_header(world: World, width: int) -> str:
    name = world.config_overrides.get("tank_name", "tank")
    sky = PHASE_GLYPH.get(world.weather.phase, "☀")
    temp = f"{world.weather.temperature_c:>4.0f}°C"
    fish = f"{len(world.fish)} fish"
    return f"  {sky} {name}: {fish}  {temp}"


def _make_water(strength: float, width: int, rows: int,
                phase: str = "day") -> list[str]:
    density = min(1.0, max(0.05, strength))
    # The surface reads the sky: ripples by day, a still dotted skin at night,
    # sparse points in the witching hour.
    glyph = "·" if phase in ("night", "witching") else "~"
    fill_floor = 0.4 if phase not in ("night", "witching") else 0.2
    out = []
    for r in range(rows):
        rng = seeded("water", r, round(density * 100), phase)
        s = ""
        for c in range(width - 2):
            s += glyph if rng.random() < (fill_floor + density * 0.5) else " "
        out.append(s)
    return out


def _zone_band(zone: str, rows: int) -> tuple[int, int]:
    """Vertical row band [lo, hi) a fish of this zone swims in.

    surface hugs the top, bottom hugs the silt, mid fills the broad middle.
    Bands overlap slightly so the tank doesn't look striped."""
    if rows <= 2:
        return 0, rows
    third = max(1, rows // 3)
    if zone == "surface":
        return 0, third
    if zone == "bottom":
        return rows - third, rows
    return third - (1 if third > 1 else 0), rows - third + 1  # mid, slight overlap


def _make_fish_layer(fish: list[Fish], rows: int, width: int,
                     tick_at: dt.datetime) -> list[str]:
    grid: list[list[str]] = [[" "] * (width - 2) for _ in range(rows)]
    for f in fish:
        rng = seeded("place", f.id, tick_at.isoformat())
        lo, hi = _zone_band(getattr(f, "zone", "mid"), rows)
        row = rng.randint(lo, max(lo, hi - 1))
        col = rng.randint(0, max(0, (width - 2) - len(f.glyph)))
        for i, ch in enumerate(f.glyph):
            if col + i < width - 2:
                grid[row][col + i] = ch
    return ["".join(r) for r in grid]


def _make_silt(density: float, width: int) -> str:
    chars = ["▒", "▓"] if density > 0.5 else ["░", "▒"]
    rng = seeded("silt", round(density * 100))
    return "".join(rng.choice(chars) for _ in range(width - 2))


def _make_fossils(fossil_layer: list[str], width: int) -> str:
    if not fossil_layer:
        return " " * (width - 2)
    take = fossil_layer[-(width - 2):]
    s = "".join(take)
    if len(s) < width - 2:
        s = s + " " * (width - 2 - len(s))
    return s


def _make_sensor_strip(world: World, width: int) -> str:
    w = world.weather
    return (
        f" {w.phase} · "
        f"temp {w.temperature_c:>4.1f}°C · "
        f"current {w.current_strength:.2f} · "
        f"silt {w.silt_density:.2f} · "
        f"light {w.light_level:.2f} · "
        f"{len(world.fish)} fish"
    )


def _make_graveyard_preview(world: World, width: int) -> str:
    return ""
