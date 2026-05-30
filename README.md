# fish-tank

A terminal aquarium that lives in your machine.

The tank lives on its own — fish thrive on machine activity, die from machine events. No upkeep loop, no chores. You can visit, name fish, drop in a treat after a clean ship; you can also ignore it forever and it'll keep going. State accumulates over months.

## Install

```bash
git clone <repo>
cd fish-tank
python -m venv .venv && .venv/Scripts/activate
pip install -e .
tank install         # registers a Scheduled Task to run `tank tick` every 5 min
tank tick            # seed the first world right now
```

## Use

```
tank                 # peek at the tank
tank --line          # one-line summary (statusline-friendly)
tank live            # ambient pane, Ctrl+C to quit
tank serve           # localhost HTTP at http://127.0.0.1:7311/tank
tank status          # diagnostics

tank adopt <name>    # add a fish manually
tank release <name>  # release a fish (gentle removal)
tank graveyard       # last 20 deaths with epitaphs
tank events          # last 20 tick records
tank reset --confirm # nuke ~/.tank/ (no recovery)

tank uninstall       # remove the Scheduled Task
```

## How it lives

- A 5-min Windows Scheduled Task runs `tank tick`: samples hardware (LibreHardwareMonitor → psutil → nvidia-smi), scans real events (commits, ships, seals, new projects in `~/projects/`), spawns and kills fish, advances weather, writes state to `~/.tank/world.json`.
- All renderers (peek, live, serve, snapshot file) are short-lived readers of one state file.
- No LLM in the runtime loop. The personality is in `~/.tank/bestiary.yaml` and `~/.tank/epitaphs.yaml` (both user-editable).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "tank: no world yet" | First-time install — run `tank tick` once. |
| Tank shows no temps / low-light mode | LibreHardwareMonitor isn't running. Enable Options > Web Server > Run, port 8085. Tank works without it; you just lose temperature signal. |
| No fish spawning from commits | Your repos must live under `~/projects/`. Run `tank status` to see counts. |
| `tank install` fails | Run PowerShell as admin once; or set ExecutionPolicy: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |

See `docs/superpowers/specs/2026-05-14-fish-tank-design.md` for design rationale and `docs/superpowers/plans/2026-05-14-fish-tank.md` for the implementation plan.
