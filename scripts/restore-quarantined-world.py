"""Restore a world quarantined by the 0.6.x seal->note rename.

Upgrading from <=0.5.x quarantined the old world as world.json.broken-<ts>
(KeyError: 'seen_notes') and silently seeded a fresh one — an empty tank where
your fish used to be. With the legacy load migration now in serdes, this
script merges the quarantined world back:

    python scripts/restore-quarantined-world.py [--dry-run]

The newest world.json.broken-* is the base (your fish, weather, fossils,
history); anything the fresh replacement world saw since the quarantine (new
fish, newer commit baselines) is merged on top. The .broken file is left
untouched as a backup.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tank import paths  # noqa: E402
from tank.mortality import CARRYING_CAPACITY  # noqa: E402
from tank.serdes import world_from_json  # noqa: E402
from tank.world import WorldStore  # noqa: E402


def newest_quarantined(tank_dir: Path) -> Path | None:
    candidates = [p for p in tank_dir.glob("world.json.broken-*")
                  if not p.name.endswith(".why.txt")]
    return max(candidates, key=lambda p: p.name) if candidates else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report the merge without writing world.json")
    args = ap.parse_args()

    tank_dir = paths.world_path().parent
    broken = newest_quarantined(tank_dir)
    if broken is None:
        print(f"nothing to restore: no world.json.broken-* in {tank_dir}")
        return 1

    old = world_from_json(broken.read_text(encoding="utf-8"))
    store = WorldStore()
    with store.lock(timeout=30.0):
        current_path = paths.world_path()
        if current_path.exists():
            new = world_from_json(current_path.read_text(encoding="utf-8"))
            # The quarantined world is the base; graft on whatever the fresh
            # replacement world learned while it stood in. Restored fish take
            # precedence: stand-in fish only fill remaining carrying capacity,
            # otherwise the next mortality run would crowding-cull a restored
            # original to make room for a days-old stand-in.
            have = {f.id for f in old.fish}
            room = max(0, CARRYING_CAPACITY - len(old.fish))
            candidates = [f for f in new.fish if f.id not in have]
            grafted = sorted(candidates, key=lambda f: f.born_at)[:room]
            released = len(candidates) - len(grafted)
            if released:
                print(f"releasing {released} stand-in fish (tank at capacity)")
            old.fish.extend(grafted)
            old.seen_commits.update(new.seen_commits)  # newer baselines win
            old.seen_notes |= new.seen_notes
            old.seen_projects |= new.seen_projects
            old.last_tick_at = new.last_tick_at
        else:
            grafted = []

        print(f"restoring {broken.name}: {len(old.fish)} fish "
              f"({len(grafted)} grafted from the stand-in world)")
        for f in old.fish:
            print(f"  {f.name} ({f.species})")
        if args.dry_run:
            print("dry run — nothing written")
            return 0
        store.save(old)

    print(f"world.json restored. {broken.name} kept as backup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
