"""WorldStore: the only module that touches world.json."""
from __future__ import annotations

import contextlib
import datetime as dt
import logging
import os
import time

from tank import paths
from tank.models import Weather, World
from tank.serdes import world_from_json, world_to_json

logger = logging.getLogger(__name__)

# A normal tick holds the lock for well under a second (the Scheduled Task even
# caps a run at 60s). If a lock file outlives this, its owner crashed without
# releasing it — reclaim it rather than freezing the tank forever.
STALE_LOCK_S = 120.0


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
                fh.write(f"{os.getpid()} {time.time()}")
                fh.flush()
                break
            except FileExistsError:
                # Reclaim a stale lock left behind by a crashed tick, so the
                # tank can never be frozen permanently by one bad run.
                try:
                    age = time.time() - lock_file.stat().st_mtime
                    if age > STALE_LOCK_S:
                        lock_file.unlink()
                        logger.warning("reclaimed stale tick lock (age %.0fs)", age)
                        continue
                except FileNotFoundError:
                    continue  # released between our open and stat — just retry
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
