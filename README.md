# fish-tank

```text
┌──────────────────────────────────────────────────────┐
│  ◕ tank: 9 fish    42°C                              │
│ ~~    ~  ~  ~~~~~~ ~ ~~   ~~~   ~      ~~  ~~ ~      │
│~~~~~~~~ ~ ~ ~~ ~    ~~~~~~~ ~  ~ ~       ~~ ~ ~ ~  ~ │
│~   ~ ~   ~ ~ ~~   ~~  ~ ~ ~~~~  ~   ~ ~  ~ ~~ ~ ~   ~│
│                                      <^v^            │
│         >°))<                            =>o)>       │
│<°))>                              <≈><       >o))<   │
│ <·><                                                 │
│                  >°))<    =>°)>                      │
│                                                      │
│▒▒▓▒▓▒▓▒▒▓▒▓▓▓▒▒▒▒▒▓▓▓▓▒▒▓▒▓▒▒▒▒▒▓▒▓▓▒▓▓▓▓▒▓▒▓▒▒▓▓▒▒▒▒│
│                                                      │
└──────────────────────────────────────────────────────┘
  the tank feels: calm
 dusk · temp 41.9°C · current 0.10 · silt 0.82 · light 0.60 · 9 fish
```

<!-- HERO: tank-serve.gif / web-demo screenshot goes here (captured in the below-pass) -->

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![version](https://img.shields.io/badge/version-0.6.0-brightgreen.svg)](VERSION)
[![tests](https://github.com/benskamps/fish-tank/actions/workflows/test.yml/badge.svg)](https://github.com/benskamps/fish-tank/actions/workflows/test.yml)

**A terminal aquarium fed by your real machine — a terrarium, not a Tamagotchi.**

You don't feed it. You don't clean it. Nothing nags you. The fish live on what's
*already happening* on your machine: a commit drifts a new fish in, a release ships a
long-lived one, a brand-new project founds another, and writing notes or plans spawns a
quiet fish that remembers. The water runs cold when the room is idle and warm when it's
working hard. A night-fish surfaces only in the small hours after midnight. There's
nothing to maintain — visit it, name a fish, or ignore it for months while your machine
quietly fills the tank.

> **See it alive →** **[brokenbranch.dev/aquarium](https://www.brokenbranch.dev/aquarium/)** — a live, animated aquarium running on the author's own machine. No install required.

## How it works

The tank is a *terrarium*: an ambient ecosystem that draws its life from your real
activity and hardware, so it never asks you for anything. Every few minutes a scheduled
tick reads your machine and your work, then spawns or retires fish accordingly:

| When this happens on your machine… | …this fish appears |
|---|---|
| You make a commit | **driftfish** — short-lived, schools with its kin |
| You ship a release | **shipfish** — long-lived, named after the project |
| A new project appears | **founderfish** — the longest-lived of all |
| You write notes, plans, or history | **notefish** — calm; it remembers what you wrote down |
| Your machine runs hot for a while | **thermalwisp** / **emberlung** — heat-borne, quick to come and go |
| It runs cool and quiet for a while | **coldfin** / **frostneon** — they drift in after a calm stretch |
| Something crashes | **crashstrider** — a brief, frantic life |
| It's between 00:00 and 03:00 | **night-fish** — surfaces only in the witching hour, gone by dawn |

When a fish dies it leaves an epitaph and a fossil glyph that settles into the substrate.
Causes are drawn from real aquarium failure modes — ich, fin rot, ammonia, old age,
"jumped out of the tank" — so the losses ring true. There's no LLM at runtime: every
species, mood, and epitaph lives in editable YAML.

## Quickstart

Five minutes from clone to a living tank:

```bash
git clone https://github.com/benskamps/fish-tank
cd fish-tank
python -m venv .venv && .venv/Scripts/activate   # Windows; use source .venv/bin/activate elsewhere
pip install -e .

tank tick      # seed the first world right now (one manual tick)
tank           # peek at the tank
tank install   # register a scheduled task so it ticks on its own (see "How it lives")
```

That's it. `tank tick` builds `~/.tank/world.json`; `tank` renders it. The first tick
baselines your existing repos silently — fish start appearing from activity that happens
*after* the tank starts watching, not from your whole git history.

## How it lives

- A scheduled tick (every 5–30 min via `tank install`) runs `tank tick`: it samples hardware (LibreHardwareMonitor → psutil → nvidia-smi), scans your real activity (commits, ships, new projects, notes/plans/history), spawns and kills fish, advances the weather, and writes everything to one state file, `~/.tank/world.json`.
- Every renderer — `tank`, `tank live`, `tank serve`, the snapshot file — is a short-lived **reader** of that one state file. They never compute the world; they just draw it.
- **No LLM at runtime.** The personality lives in editable YAML (see *Customizing the bestiary* below).

## Configuration

Everything is optional. Out of the box the tank watches **every** directory under
`~/projects/`. On a machine with hundreds or thousands of repos that's far too much —
point it at just the repos you care about with an allow-list.

Create `~/.tank/config.yaml`:

```yaml
observer:
  projects_root: "~/code"          # optional — where your repos live (default: ~/projects)
  watch: ["my-app", "my-lib"]      # optional — ONLY these repo folder names feed the tank
  notes_dir: "~/notes"             # optional — your notes/journal/plans dir (default: ~/notes)
```

- **`watch` — the allow-list (start here).** By default the tank scans every repo under `projects_root`. List the folder *names* you actually want to watch and it ignores the rest. If you leave `watch` out, the tank scans everything — but as a safety valve it will never silently scan more than 50 directories; above that it scans the 50 most-recently-modified and logs exactly how many it skipped, so you know to set a `watch` list.
- **`projects_root`** — the folder your repos live under. Defaults to `~/projects`.
- **`notes_dir`** — your notes directory: session logs, design docs, plans, daily notes, whatever journal-shaped markdown you keep. A new file here spawns a calm, long-lived **notefish** that "remembers what you wrote down." Point it anywhere you keep that kind of writing. (The pre-0.6 name `seals_dir` still works as a deprecated alias.)

`~` is expanded in any path value.

### Environment overrides

Environment variables override the config file (handy for one-off runs and CI):

| Variable | Overrides | Format |
|---|---|---|
| `TANK_PROJECTS_ROOT` | `observer.projects_root` | a path |
| `TANK_NOTES_DIR` | `observer.notes_dir` | a path (`TANK_SEALS_DIR` is a deprecated alias) |
| `TANK_WATCH` | `observer.watch` | comma-separated names, e.g. `my-app,my-lib` |
| `TANK_HOME` | where state lives (default `~/.tank/`) | a path |

## Customizing the bestiary

The personality is data, not code. On first run the tank seeds two editable files into
`~/.tank/`:

- **`bestiary.yaml`** — species, glyphs, lifespans, spawn triggers, and moods.
- **`epitaphs.yaml`** — the death messages.

Edit either one and the next tick picks up your changes — your edits win, with the
bundled defaults as the fallback. Add your own species, retune a lifespan, or rewrite an
epitaph; nothing is hard-coded. If a file is missing or malformed, the tank quietly falls
back to the bundled defaults rather than emptying the tank.

## Use

```
tank                 # peek at the tank
tank --line          # one-line summary (statusline-friendly)
tank live            # ambient pane, Ctrl+C to quit
tank serve           # animated localhost aquarium at http://127.0.0.1:7311/tank
tank status          # diagnostics (fish count, last tick, weather, fossils)

tank adopt <name>    # add a fish manually (--species to pick one)
tank release <name>  # release a fish (gentle removal)
tank graveyard       # last 20 deaths with epitaphs (--all for everything)
tank events          # last 20 tick records
tank reset --confirm # nuke ~/.tank/ (no recovery)

tank install         # register the scheduled tick task
tank uninstall       # remove the scheduled task
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| No world yet | First-time install — run `tank tick` once. |
| No temps / low-light mode | LibreHardwareMonitor isn't running. Enable Options > Web Server > Run (port 8085). The tank works without it; you just lose the temperature signal. |
| No fish spawning from commits | Your repos must live under `projects_root` (default `~/projects`), and — if you set a `watch` list — be named in it. Run `tank status` to see counts. |
| It seems to be scanning too much | You have a lot of repos under `projects_root`. Set `observer.watch` (or `TANK_WATCH`) to the handful you care about. |
| `tank install` fails | Run PowerShell as admin once, or set the execution policy: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |

## Dependencies

fish-tank keeps a deliberately small, self-contained footprint and **adds no new runtime
dependencies** — there's no LLM in the loop and no service to stand up. It ships exactly
three well-established libraries: [`rich`](https://github.com/Textualize/rich) (color
rendering), [`psutil`](https://github.com/giampaolo/psutil) (cross-platform hardware
sampling), and [`PyYAML`](https://pyyaml.org/) (the editable bestiary/epitaphs). That's
the whole supply chain.

## Design & philosophy

The "terrarium, not a Tamagotchi" idea — and the nitrogen-cycle metaphor that makes the
tank *feel* true — are written up in **[docs/DESIGN.md](docs/DESIGN.md)**.

## Contributing

Issues and PRs welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** for the dev setup and
the one Windows test gotcha (`PYTHONIOENCODING=utf-8`).

## License

MIT — see [LICENSE](LICENSE).
