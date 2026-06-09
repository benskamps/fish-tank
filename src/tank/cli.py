"""Command-line interface for the tank."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import uuid

from tank import paths
from tank.bestiary import load as load_species
from tank.clock import Clock
from tank.models import Fish
from tank.render.frame import compose, render
from tank.serdes import world_from_json
from tank.tick import TickEngine
from tank.world import WorldStore


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    cmd = args.cmd or "peek"
    handler = _COMMANDS.get(cmd)
    if not handler:
        parser.print_help()
        return 1
    return handler(args)


def _ensure_utf8_stdout() -> None:
    """On Windows the default codepage is cp1252; tank glyphs need utf-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tank")
    p.add_argument("--line", action="store_true", help="single-line render")
    p.add_argument("--plain", action="store_true", help="no color")
    p.add_argument("--ascii", action="store_true", help="ascii-only line")
    p.add_argument("--width", type=int, default=56)

    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("peek")
    sub.add_parser("status")
    sub.add_parser("tick")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    sub.add_parser("live")

    adopt = sub.add_parser("adopt")
    adopt.add_argument("name")
    adopt.add_argument("--species", default="guppy")

    release = sub.add_parser("release")
    release.add_argument("name")

    grave = sub.add_parser("graveyard")
    grave.add_argument("-n", type=int, default=20)
    grave.add_argument("--all", action="store_true")

    events = sub.add_parser("events")
    events.add_argument("-n", type=int, default=20)

    reset = sub.add_parser("reset")
    reset.add_argument("--confirm", action="store_true")

    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=7311)

    return p


def _ensure_world():
    if not paths.world_path().exists():
        TickEngine().run_once()


def _load_world():
    return world_from_json(paths.world_path().read_text(encoding="utf-8"))


def _save_world(world):
    WorldStore().save(world)


def _cmd_peek(args) -> int:
    _ensure_world()
    world = _load_world()
    frame = compose(world, width=args.width)
    style = "line" if args.line else "plain"
    print(render(frame, style=style))
    return 0


def _cmd_status(args) -> int:
    _ensure_world()
    world = _load_world()
    print(f"tank · {len(world.fish)} fish · last tick {world.last_tick_at.isoformat()}")
    print(f"weather: {world.weather.temperature_c:.1f}°C · "
          f"current {world.weather.current_strength:.2f} · "
          f"silt {world.weather.silt_density:.2f}")
    print(f"fossils: {''.join(world.weather.fossil_layer) or '(none)'}")
    return 0


def _cmd_tick(args) -> int:
    TickEngine().run_once()
    return 0


def _cmd_adopt(args) -> int:
    _ensure_world()
    species_table = load_species()
    sp = species_table.get(args.species) or species_table.get("guppy")
    if sp is None:
        # An override bestiary without a "guppy" and an unknown --species: pick
        # any species rather than crashing on a hard-coded key that isn't there.
        sp = next(iter(species_table.values()), None)
    if sp is None:
        print("no species available to adopt (empty bestiary)", file=sys.stderr)
        return 1
    world = _load_world()
    fish = Fish(
        id=uuid.uuid4().hex[:8], name=args.name, species=sp.key,
        glyph=sp.glyph_pool[0], born_at=Clock().now(),
        lifespan_days=float(sum(sp.base_lifespan_days)) / 2,
        provenance="manual:adopt", project=None, mood="calm",
        last_position=(0, 0),
    )
    world.fish.append(fish)
    _save_world(world)
    print(f"adopted {fish.name} ({sp.key})")
    return 0


def _cmd_release(args) -> int:
    _ensure_world()
    world = _load_world()
    before = len(world.fish)
    world.fish = [f for f in world.fish if f.name != args.name]
    _save_world(world)
    if len(world.fish) < before:
        print(f"released {args.name}")
        return 0
    print(f"no fish named {args.name}")
    return 1


def _cmd_graveyard(args) -> int:
    gy = paths.graveyard_path()
    if not gy.exists():
        print("(graveyard empty)")
        return 0
    lines = gy.read_text(encoding="utf-8").strip().splitlines()
    if not args.all:
        lines = lines[-args.n:]
    if not lines:
        print("(graveyard empty)")
        return 0
    for raw in lines:
        try:
            d = json.loads(raw)
            print(f"  {d['fossil_glyph']} {d['epitaph']}")
        except json.JSONDecodeError:
            print(raw)
    return 0


def _cmd_events(args) -> int:
    ep = paths.events_path()
    if not ep.exists():
        print("(events empty)")
        return 0
    lines = ep.read_text(encoding="utf-8").strip().splitlines()[-args.n:]
    for raw in lines:
        print(raw)
    return 0


def _cmd_reset(args) -> int:
    if not args.confirm:
        print("tank reset requires --confirm (nukes ~/.tank/)", file=sys.stderr)
        return 2
    home = paths.tank_home()
    if home.exists():
        import shutil
        shutil.rmtree(home)
    print("tank reset.")
    return 0


def _cmd_install(args) -> int:
    from tank.install import install_scheduled_task
    return install_scheduled_task()


def _cmd_uninstall(args) -> int:
    from tank.install import uninstall_scheduled_task
    return uninstall_scheduled_task()


def _cmd_live(args) -> int:
    from tank.render.live import live_loop
    return live_loop(width=args.width)


def _cmd_serve(args) -> int:
    from tank.serve import serve
    return serve(port=args.port)


_COMMANDS = {
    "peek": _cmd_peek,
    "status": _cmd_status,
    "tick": _cmd_tick,
    "adopt": _cmd_adopt,
    "release": _cmd_release,
    "graveyard": _cmd_graveyard,
    "events": _cmd_events,
    "reset": _cmd_reset,
    "install": _cmd_install,
    "uninstall": _cmd_uninstall,
    "live": _cmd_live,
    "serve": _cmd_serve,
}
