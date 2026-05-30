"""tank live — ambient pane loop with Rich."""
from __future__ import annotations

import time

from rich.live import Live
from rich.text import Text

from tank import paths
from tank.render.frame import compose, phase_style, render
from tank.serdes import world_from_json

FRAME_INTERVAL_S = 1.0
WORLD_RELOAD_S = 5.0


def live_loop(width: int = 56) -> int:
    try:
        with Live(refresh_per_second=4, screen=False) as live:
            last_reload = 0.0
            world = None
            while not _should_quit():
                now = time.monotonic()
                if world is None or now - last_reload > WORLD_RELOAD_S:
                    world = _load_world_silent()
                    last_reload = now
                style = phase_style(world.weather.phase) if world else "default"
                live.update(Text(_render_frame(width, world=world), style=style))
                _sleep(FRAME_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    return 0


def _load_world_silent():
    path = paths.world_path()
    if not path.exists():
        return None
    try:
        return world_from_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _render_frame(width: int, world=None) -> str:
    world = world if world is not None else _load_world_silent()
    if world is None:
        return "tank: no world yet — run `tank tick` once or wait for scheduled task."
    frame = compose(world, width=width)
    return render(frame, style="plain")


def _should_quit() -> bool:
    return False


def _sleep(seconds: float) -> None:
    time.sleep(seconds)
