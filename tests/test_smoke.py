from pathlib import Path

import tank


def test_version_matches_version_file():
    """tank.__version__ is the single source of truth; it must agree with the
    VERSION file (which the README's version badge points at)."""
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    assert tank.__version__ == version_file.read_text(encoding="utf-8").strip()


def test_version_is_resolvable_via_metadata():
    """pyproject derives the package version from tank.__version__, so the two
    must match — guards against the 0.1.0/0.6.0 drift that used to exist."""
    from importlib.metadata import version
    assert version("fish-tank") == tank.__version__


def test_import_does_not_explode():
    from tank import __version__  # noqa: F401
