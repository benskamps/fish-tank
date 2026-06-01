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

A terminal aquarium fed by your real machine. Fish are born from your work and your hardware: a commit drifts a new fish in, a release ships a long-lived one, a brand-new project founds another, and writing notes, plans, or history spawns a quiet one that remembers. The weather tracks your CPU and GPU — the water runs cold when the room is idle, warm when it's working hard. Day and night follow your local clock, and a night-fish surfaces only in the small hours after midnight. There's no upkeep loop and nothing to feed: you can visit, name a fish, and otherwise let it run for months while state quietly accumulates.

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

That's it. `tank tick` builds `~/.tank/world.json`; `tank` renders it. The first tick baselines your existing repos silently — fish start appearing from activity that happens *after* the tank starts watching, not from your whole git history.

## Configuration

Everything is optional. Out of the box the tank watches **every** directory under `~/projects/`. On a machine with hundreds or thousands of repos that's far too much — point it at just the repos you care about with an allow-list.

Create `~/.tank/config.yaml`:

```yaml
observer:
  projects_root: "~/code"          # optional — where your repos live (default: ~/projects)
  watch: ["my-app", "my-lib"]      # optional — ONLY these repo folder names feed the tank
  seals_dir: "~/notes"             # optional — your notes/journal/plans dir (default: ~/seals)
```

- **`watch` — the allow-list (start here).** By default the tank scans every repo under `projects_root`. List the folder *names* you actually want to watch and it ignores the rest. If you leave `watch` out, the tank scans everything — but as a safety valve it will never silently scan more than 50 directories; above that it scans the 50 most-recently-modified and logs exactly how many it skipped, so you know to set a `watch` list.
- **`projects_root`** — the folder your repos live under. Defaults to `~/projects`.
- **`seals_dir`** — your "seals" directory. **"Seals" just means your notes / journal / planning markdown** — session logs, design docs, daily notes, whatever you keep. A new file here spawns a calm, long-lived fish that "remembers what you wrote down." Point it anywhere you keep that kind of writing.

`~` is expanded in any path value.

### Environment overrides

Environment variables override the config file (handy for one-off runs and CI):

| Variable | Overrides | Format |
|---|---|---|
| `TANK_PROJECTS_ROOT` | `observer.projects_root` | a path |
| `TANK_SEALS_DIR` | `observer.seals_dir` | a path |
| `TANK_WATCH` | `observer.watch` | comma-separated names, e.g. `my-app,my-lib` |
| `TANK_HOME` | where state lives (default `~/.tank/`) | a path |

## How it lives

- A scheduled tick (every 5–30 min via `tank install`) runs `tank tick`: it samples hardware (LibreHardwareMonitor → psutil → nvidia-smi), scans your real activity (commits, ships, new projects, notes/plans/history), spawns and kills fish, advances the weather, and writes everything to one state file, `~/.tank/world.json`.
- Every renderer — `tank`, `tank live`, `tank serve`, the snapshot file — is a short-lived **reader** of that one state file. They never compute the world; they just draw it.
- **No LLM at runtime.** The personality lives in editable YAML: `~/.tank/bestiary.yaml` (species) and `~/.tank/epitaphs.yaml` (death messages). Edit them and the next tick picks up your changes.

## Use

```
tank                 # peek at the tank
tank --line          # one-line summary (statusline-friendly)
tank live            # ambient pane, Ctrl+C to quit
tank serve           # localhost HTTP at http://127.0.0.1:7311/tank
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

## Live demo

There's a living example of the author's own tank at **brokenbranch.dev/aquarium**.

---

See `docs/superpowers/specs/2026-05-14-fish-tank-design.md` for design rationale and `docs/superpowers/plans/2026-05-14-fish-tank.md` for the implementation plan.
