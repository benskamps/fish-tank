# Changelog

All notable changes to fish-tank are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **pushfish and mergefish — two residents born from the events git already
  records for you.** The tank could see a commit, a release and a new project,
  but not the two moments that actually feel like progress: getting work out of
  the machine, and a pull request landing.
  - **pushfish** (`>><(°>`) is born from a real `git push`. Quick, darty,
    short-lived — momentum is real and momentum fades. The chevrons trailing it
    are the current at its back.
  - **mergefish** (`><(M(°>`) is born when a pull request lands. Bigger, calm,
    and it holds station with the other landmark project fish, because a merge
    is the one event here that cannot come apart again. It outlives by weeks
    every push that carried it.
- **A test that holds the renderer's species tables to the bestiary.**
  `bestiary.yaml` does not drive `tank serve`; the renderer carries its own
  field guide, size, pace and glow maps, and a species missing from them still
  swims — at every default, with no legend row and no colour. That is what
  happened when notefish was introduced. Now a species added to the bestiary
  and forgotten in the renderer fails a test instead of shipping invisible.

### Notes on how the two events are detected — no network, ever
- **A push** is read from the reflog git writes on your remote-tracking refs,
  keyed on the subject `update by push`. A fetch or a pull writes `fetch …` /
  `pull …` instead, so work *arriving* from someone else never spawns anything.
  Every `refs/remotes/*` ref is scanned, not just the current branch's push
  destination: on a real machine the checked-out branch often has no upstream
  at all, and the newest push is frequently on a branch nobody is standing on.
- **A merged PR** is read from the commit subject — GitHub's classic
  `Merge pull request #12 from …` and its squash-merge `… (#12)`. A release
  that lands via PR matches both patterns; **ship wins**, so a release stays a
  shipfish.
- Dedup is a per-repo high-water mark on the reflog timestamp, and the first
  sighting of a repo baselines silently. Installing the tank onto a machine
  with years of reflog history spawns nothing for it.

## [0.9.0] - 2026-09-06

### Changed
- **`tank serve` re-ported to the current brokenbranch.dev/aquarium renderer.**
  The local page had fallen ~1,100 lines behind the site's `aquarium.js`; it
  now carries the same code again (the only deliberate differences: it polls
  `/tank.json` every 15s instead of the site's `/api/tank` every 45s). What
  that brings back: fish ease through their turns instead of snapping to face
  their heading; a startled fish flinches with a C-start escape instead of a
  teleport; park the pointer still over the water and one free swimmer drifts
  over to inspect it; two same-species schoolers occasionally play a short game
  of chase before melting back into the school; each schooling species has its
  own shoaling personality (a rummy-nose school moves as one body, killifish
  barely school at all); plecos and cleaner shrimp hold station on the
  substrate and relocate a few body lengths at a time; and the floor is the
  composed aquascape — two masses on the golden thirds with a protected
  swimming lane between, three depth bands, and the skull as the keystone.
- **Ember is rendered.** The adopt-only resident now has an entry in every
  renderer table (field guide, size, pace, calm, bioluminescence, shoaling),
  so it swims with a name and a tooltip instead of as an untitled generic
  glyph. Its glyph still comes from the tick, brightest-last.
- **`light_level` is honoured.** The renderer reads `weather.light_level` and
  dims the phase's lamp — ray strength, the caustic sheet, the phase glow —
  within the phase, so an idle machine at noon sits visibly dimmer than a busy
  one. The phase stays the palette; light only modulates it. Applied under
  reduced motion too (it is a colour, not motion).
- **The poll pauses in background tabs.** Hiding the tab now stops the
  `/tank.json` timer as well as the animation loop, and both restart on
  return. The old port kept fetching from a tab nobody was looking at.
- `GET /tank.json` now emits the same shape as the published site snapshot:
  it gained `schema` and `weather.pressure`, and `tick_at` comes from the
  world's `last_tick_at` (falling back to the file mtime only for a world that
  predates it). One renderer, one contract, both feeds.

### Fixed
- The README's version badge had been stuck at 0.6.0 since that release; it
  now tracks `VERSION`.

## [0.8.0] - 2026-07-03

### Added
- **`tank serve` now ships the full brokenbranch.dev/aquarium renderer.** The
  local aquarium at `http://127.0.0.1:7311/tank` reaches parity with the live
  site: the same requestAnimationFrame simulation — wander + loose schooling
  (boids), a closed-form flow field, burst-and-coast locomotion, habitat-zone
  containment and cursor-startle — plus god rays, caustics, depth attenuation,
  swaying weeds and decor, near-surface reflections, light-trails, a
  bioluminescent night glow, and click-to-feed. Glyphs flip to face their
  travel direction and undulate with a speed-locked tail beat. It's the same
  renderer code that runs on the author's site; here it polls this server's
  `/tank.json` instead of the site's `/api/tank`, and the snapshot is built
  locally from `~/.tank/world.json`. Still no dependencies, still one file.
- A `<noscript>` block keeps the static ASCII tank for JS-off viewers, so the
  terminal soul survives either way.

### Changed
- `tank serve` gained a machine-readable `GET /tank.json` endpoint — a
  sanitized snapshot (nested `weather` block, flat fish roster, `fish_count`,
  `fossil_layer`, `tick_at`) that the animated page polls every 15s. With no
  world yet it degrades to `{"empty": true}` so the renderer settles to a
  glassy idle rather than erroring.

## [0.7.0] - 2026-06-23

### Fixed
- **The `~/.tank/bestiary.yaml` override is now honored.** Editing your bestiary
  (or epitaphs) and waiting for the next tick now actually changes the tank — the
  README has promised this for a while, but the live tick path and `tank adopt` were
  loading the bundled species table directly and silently ignoring your edits.
  Both now route through an override-aware loader that falls back to the bundled
  defaults if your file is missing or malformed.
- **First run seeds editable copies of `bestiary.yaml` and `epitaphs.yaml`** into
  `~/.tank/`, so there's a real file to edit (idempotent — your edits are never
  overwritten).
- Hardened the bestiary loader against malformed user overrides: an empty
  `mood_bias` or `glyph_pool`, or a single-element / scalar / reversed
  `base_lifespan_days`, no longer crashes the (headless, invisible) scheduled tick —
  it degrades to sane defaults.
- `tank adopt` no longer crashes when a custom bestiary omits the `guppy` species.
- Reconciled the package version: `tank.__version__` read `0.1.0` while `VERSION`
  and `pyproject.toml` said `0.6.0`. The version is now single-sourced from
  `tank.__version__`, so `importlib.metadata.version("fish-tank")` reports `0.6.0`.

### Added
- **A `murky` tank mood.** Silt density — already tracked every tick (a long-running,
  memory-heavy machine clouds the water) — now surfaces in the felt word: a thick,
  settled tank with nothing else stirring reads `murky` rather than merely `drowsy` or
  `calm`. Real signals still win — a ship, the witching hour, or churn outrank the
  cloud — so `murky` only shows up in an otherwise quiet tank.
- **`crashstrider` now hatches from real machine crashes.** On Windows, a
  best-effort crash sense reads the Windows **System** event log (via `wevtutil`) for
  unexpected-shutdown (event `6008`) and BugCheck/BSOD (event `1001`) records, dedupes them
  so the same crash never spawns twice, and turns each fresh one into a brief, frantic
  `crashstrider`. (The noisier Kernel-Power `41` is deliberately excluded so an ordinary
  ungraceful reboot doesn't fake a crash.) It's
  strictly best-effort — if the log is unavailable, empty, or slow, the tick carries
  on untouched; a crash can never break a tick.
- **`tank serve` night level-up.** The animated localhost aquarium got a tasteful
  nighttime glow-up: a soft bioluminescent halo on the fish after dark (it breathes
  gently and respects `prefers-reduced-motion`), living water — faint motes that
  drift and sink so the tank is never still — and a small, clear aquascape resting on
  the floor (a couple of plant stems, a low rock mound, a tiny chest, a reed cluster).
- `CHANGELOG.md`, `CONTRIBUTING.md`, and a GitHub Actions CI workflow that runs the
  test suite (with `PYTHONIOENCODING=utf-8`) on every push and pull request.
- README storefront pass: badges, a species/event table, the "terrarium, not a
  Tamagotchi" framing, a clickable live-demo link, and a *Customizing the bestiary*
  section.

## [0.6.0] - 2026-06

### Added
- `tank serve`: an animated localhost aquarium at `http://127.0.0.1:7311/tank` —
  fish swim, face the way they're going, and wiggle.
- A living-tank hero frame in the README (sanitized demo world).

### Changed
- Generalized the observer for a public release: the internal observer concept is now
  **notes** and the species it spawns is **notefish**. Writing notes, plans, or history
  spawns a calm, long-lived fish that "remembers what you wrote down."
- Pre-0.6 worlds are migrated forward automatically across the rename, and the old
  `seals_dir` / `TANK_SEALS_DIR` names keep working as deprecated aliases.

## [0.5.0]

### Added
- A `watch` allow-list so machines with hundreds of repos can point the tank at just
  the repos they care about.
- Opt-in public fish naming for sharing a sanitized tank.
- The crab (a bottom-zone resident).

### Changed
- README overhaul.

## [0.4.0]

### Added
- A more living ecosystem: depth zones (surface / mid / bottom), five real species, and
  more diversity in who shows up.

## [0.3.0]

### Added
- Opt-in publishing of a sanitized snapshot.
- Windowless subprocess handling and stale-lock recovery for the scheduled tick.

## [0.2.0]

### Added
- First public cut of fish-tank: a terminal aquarium that lives in your machine, fed by
  hardware weather and your real activity, with a bestiary, mortality, and epitaphs.

[Unreleased]: https://github.com/benskamps/fish-tank/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/benskamps/fish-tank/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/benskamps/fish-tank/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.7.0
[0.6.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.6.0
[0.5.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.5.0
[0.4.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.4.0
[0.3.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.3.0
[0.2.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.2.0
