"""Tick orchestrator — the 13-step lifecycle from spec §6."""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass

from tank import paths
from tank.bestiary import Species, load_bundled
from tank.clock import Clock, ClockProtocol
from tank.hardware import sample as real_sample
from tank.models import Death
from tank.observer import Observer
from tank.mood import compute as mood_compute
from tank.render.frame import compose, render as render_frame
from tank.spawn import run as spawn_run, submerge_dawn
from tank.mortality import run as mortality_run
from tank.weather import synthesize as weather_synthesize
from tank.world import WorldStore

logger = logging.getLogger(__name__)

FOSSIL_LAYER_MAX = 56


class _RealHardware:
    def sample(self, timeout=2.0):
        return real_sample(timeout=timeout)


@dataclass
class TickEngine:
    clock: ClockProtocol = None
    hardware: object = None
    observer: object = None
    species: dict[str, Species] = None

    def __post_init__(self):
        self.clock = self.clock or Clock()
        self.hardware = self.hardware or _RealHardware()
        self.observer = self.observer or Observer()
        self.species = self.species or load_bundled()
        self.store = WorldStore()

    def run_once(self) -> None:
        paths.ensure_dirs()
        try:
            with self.store.lock(timeout=5.0):
                self._tick_locked()
        except TimeoutError:
            logger.warning("tick lock held; skipping")

    def _tick_locked(self) -> None:
        now = self.clock.now()
        world = self.store.load_or_init(now=now)
        sample = self.hardware.sample(timeout=2.0)
        events = self.observer.scan_since(world.last_tick_at, world)
        dt_since = max(dt.timedelta(0), now - world.last_tick_at)
        world.weather = weather_synthesize(sample, world.weather, dt_since, now=now)
        births = spawn_run(world, events, sample, now, self.species)
        deaths = mortality_run(world, sample, events, now, self.species,
                               epitaphs_path=paths.epitaphs_path())
        world.fish.extend(births)
        # Night-fish submerge at dawn — silently, no death.
        world.fish = submerge_dawn(world.fish, now)
        # Felt inner state, derived after the tick's births/deaths are known.
        world.weather.mood = mood_compute(world.weather, events=events,
                                          births=births, deaths=deaths)

        if deaths:
            with open(paths.graveyard_path(), "a", encoding="utf-8") as f:
                for d in deaths:
                    f.write(json.dumps(_death_to_dict(d)) + "\n")
            for d in deaths:
                world.weather.fossil_layer.append(d.fossil_glyph)
            world.weather.fossil_layer = world.weather.fossil_layer[-FOSSIL_LAYER_MAX:]

        world.last_tick_at = now
        self.store.save(world)

        frame = compose(world)
        paths.snapshot_path().write_text(render_frame(frame, style="plain"),
                                         encoding="utf-8")

        with open(paths.events_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "tick_at": now.isoformat(),
                "fish_count": len(world.fish),
                "births": len(births),
                "deaths": len(deaths),
                "hw_sources": sample.sources_used,
                "degraded": sample.degraded,
            }) + "\n")


def _death_to_dict(d: Death) -> dict:
    return {
        "fish_id": d.fish_id, "name": d.name, "species": d.species,
        "born_at": d.born_at.isoformat(), "died_at": d.died_at.isoformat(),
        "cause": d.cause, "epitaph": d.epitaph, "fossil_glyph": d.fossil_glyph,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    TickEngine().run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
