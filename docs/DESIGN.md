# fish-tank — design notes

A terminal aquarium that lives in your machine. The fish thrive on your machine's
activity and die from its events. There is no upkeep loop and no chores — you can visit,
name a fish, or ignore it for months. State accumulates the whole time.

This doc explains *why* it's built the way it is. For how to run it, see the README.

## The core idea: a terrarium, not a Tamagotchi

There are two souls a virtual pet can have:

- **Tamagotchi soul** — it needs you. Feed it or it suffers. Attention is a chore.
- **Terrarium soul** — it lives on its own. You observe; you don't maintain.

fish-tank is deliberately a terrarium. It draws its life from things that are *already
happening* — your CPU temperature, your memory pressure, your git commits — so it never
asks for anything. An ant farm on your desk, not a needy widget. This lineage runs back
through asciiquarium (the original terminal aquarium), screen-saver ecosystems, and the
"calm technology" idea (Weiser & Brown): good ambient tools live at the *periphery* of
attention and only step to the center when they have something worth saying.

## The metaphor that does the work: the nitrogen cycle

Real freshwater tanks live and die by the nitrogen cycle — fish waste becomes ammonia
(toxic), bacteria convert it to nitrite (toxic) and then nitrate (tolerable), and a tank
that isn't cycled crashes. That maps surprisingly well onto machine hygiene, and the
mapping is what makes the tank *feel* true rather than arbitrary:

| Aquarium reality | Machine analogue |
|---|---|
| Ammonia spike stresses fish | A pile of uncommitted change / thrash |
| Nitrate accumulation, "old tank syndrome" | Long uptime, stale branches, memory bloat |
| Stable, cycled tank | A clean, well-tended working set |
| Temperature swings | CPU/GPU thermals |

Weather (`weather.py`) synthesizes these signals into temperature, current, silt, light,
and pressure, smoothed over time so the tank drifts rather than flickers.

## The bestiary

Fish are data, not code (`data/bestiary.yaml`, user-editable). Each species has a glyph
pool, a lifespan range, a spawn trigger, and a mood bias. Some are born from real events
in your work, some from sustained machine conditions:

- **Event-born** — a *ship* spawns a long-lived shipfish named after the project; a new
  project spawns a founderfish; commits spawn short-lived driftfish; optional session
  "seals" (journal files) spawn witnessfish.
- **Weather-born** — sustained cold spawns cold-water species; sustained GPU heat spawns
  heat species.
- **Bestiary rolls** — ordinary residents drift in to fill the tank.

When a fish dies it leaves an epitaph (`data/epitaphs.yaml`) and a fossil glyph that
settles into the substrate. Epitaphs borrow the grammar of real fishkeepers' loss notes
and roguelike death messages (NetHack, Caves of Qud) — specific, a little wry, never
saccharine. Death causes are drawn from real aquarium failure modes (ich, fin rot, swim
bladder, ammonia poisoning, old age, "jumped out of the tank") so they ring true.

## The soul layer: circadian body + mood + the night-fish

A later pass gave the tank a sense of time and an inner state:

- **Circadian phase** (`circadian.py`) — the tank knows your *local* clock and moves
  through `dawn → day → dusk → night → witching`. Light folds into the phase, so the
  tank is genuinely dark at 2am even under load. The surface ripples by day and goes
  still and dotted at night.
- **Mood** (`mood.py`) — one felt word distilled from weather, phase, and what just
  happened: `jubilant / electric / haunted / restless / drowsy / calm`. It renders as a
  single line, "the tank feels: …".
- **The night-fish** — a resident that only surfaces during the witching hour
  (00:00–03:00 local), at most one at a time, and submerges silently at dawn with no
  death and no trace. It's the one wink: the tank notices when you're up too late.

## Architecture: strict module boundaries

The whole thing is a set of short-lived processes around one state file
(`~/.tank/world.json`). A scheduled task runs `tank tick` every few minutes; every
renderer (`peek`, `live`, `serve`, the cached snapshot) is a read-only consumer of that
file. No long-running daemon, no LLM in the runtime loop — the personality lives entirely
in the editable YAML.

Each module has one job and one source of truth:

- `hardware.py` — the only module that probes the machine (LibreHardwareMonitor → psutil
  → nvidia-smi, degrading gracefully).
- `observer.py` — the only module that reads your projects/journal for events.
- `weather.py` — hardware sample → weather.
- `spawn.py` / `mortality.py` — births and deaths.
- `world.py` — the only module that touches `world.json` (atomic write, lock, corrupt
  recovery).
- `render/frame.py` — the single source of visual truth: a pure `compose()` plus a
  style-dispatched `render()` (plain / line / html, with color in `live`/`serve`).

Time, hardware, and the observer are all injectable, so the test suite can simulate weeks
in milliseconds with deterministic, seeded randomness.

## Deliberately out of scope

Seasonal cycles, moon phases, multi-day weather memory, sound, and any GUI. The tank is a
small, quiet thing on purpose. The constraint is the feature.
