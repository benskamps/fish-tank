# Contributing to fish-tank

Thanks for poking at the tank. It's a small, dependency-light project and PRs are
welcome — bug fixes, new species, better epitaphs, doc polish, all fair game.

## Dev setup

```bash
git clone https://github.com/benskamps/fish-tank
cd fish-tank
python -m venv .venv
.venv/Scripts/activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -e .[dev]           # installs the package + pytest
```

## Running the tests

```bash
pytest -q
```

### The one Windows gotcha: `PYTHONIOENCODING=utf-8`

fish-tank renders box-drawing characters and fish glyphs. On Windows the default
console codepage is cp1252, which can't encode those glyphs, so a few rendering tests
will crash unless UTF-8 is forced. **Always run the suite with `PYTHONIOENCODING=utf-8`:**

```bash
# PowerShell
$env:PYTHONIOENCODING = "utf-8"; pytest -q

# bash / cmd
PYTHONIOENCODING=utf-8 pytest -q
```

CI sets this env var explicitly for the same reason. (pytest's `capsys` capture
bypasses the real console encoder, so some glyph tests pass under capture but the same
output crashes in a live terminal — hence the env var, not a code change.)

## A few house rules

- **No new runtime dependencies.** The project is deliberately self-contained: stdlib
  plus the existing three libraries (`rich`, `psutil`, `PyYAML`). If you think you need a
  new dependency, open an issue first so we can talk it through.
- **Public vocabulary.** The observer concept is **notes** and the species it spawns is
  **notefish** — use those terms in user-facing copy, docs, and code, not any older
  internal name.
- **The personality is data.** Species live in `src/tank/data/bestiary.yaml` and death
  messages in `src/tank/data/epitaphs.yaml`. Prefer editing the YAML over hard-coding
  behavior in Python.
- **Keep `serve.py` self-contained.** The animated renderer is intentionally a
  standalone reader of the world state.
- **Update `CHANGELOG.md`** under `## [Unreleased]` for any user-visible change.

## Design context

`docs/DESIGN.md` explains *why* the tank is built the way it is — the terrarium soul,
the nitrogen-cycle metaphor, and the strict module boundaries. Worth a read before a
larger change.
