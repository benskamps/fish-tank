# Changelog

All notable changes to fish-tank are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/benskamps/fish-tank/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.6.0
[0.5.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.5.0
[0.4.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.4.0
[0.3.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.3.0
[0.2.0]: https://github.com/benskamps/fish-tank/releases/tag/v0.2.0
