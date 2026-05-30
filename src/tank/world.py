"""WorldStore: the only module that touches world.json."""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import time

from tank import paths
from tank.models import Weather, World
from tank.serdes import world_from_json, world_to_json


class WorldStore:
    """Loads, saves, and locks the world state file."""

    def load_or_init(self, now: dt.datetime) -> World:
        paths.ensure_dirs()
        path = paths.world_path()
        if not path.exists():
            return self._fresh(now)
        try:
            return world_from_json(path.read_text(encoding="utf-8"))
        except Exception:
            ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
            broken = path.with_name(f"world.json.broken-{ts}")
            path.rename(broken)
            return self._fresh(now)

    def save(self, world: World) -> None:
        paths.ensure_dirs()
        path = paths.world_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(world_to_json(world), encoding="utf-8")
        try:
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

    @contextlib.contextmanager
    def lock(self, timeout: float = 5.0):
        paths.ensure_dirs()
        lock_file = paths.lock_path()
        deadline = time.monotonic() + timeout
        fh = None
        while True:
            try:
                fh = open(lock_file, "x")
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("tick lock held by another process")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fh.close()
            finally:
                try:
                    lock_file.unlink()
                except FileNotFoundError:
                    pass

    def _fresh(self, now: dt.datetime) -> World:
        return World(
            schema_version=1,
            created_at=now,
            last_tick_at=now,
            fish=[],
            weather=Weather(
                temperature_c=22.0,
                current_strength=0.0,
                silt_density=0.0,
                light_level=0.5,
                pressure=0.0,
                fossil_layer=[],
            ),
            seen_commits={},
            seen_seals=set(),
            seen_projects=set(),
            config_overrides={},
        )
