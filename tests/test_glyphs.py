"""Canonical glyph facing.

The bug this file exists to prevent: the web renderer used to carry a
hand-maintained facing table, and its own verification multiplied by that same
table — so a wrong entry produced a permanently backwards species that every
automated check reported as fine. A tautological test is worse than no test.

These tests are NOT tautological: they assert against the bestiary data and
against an independent mirror operation, so a glyph authored backwards fails.
"""
from __future__ import annotations

import pytest

from tank.bestiary import load_bundled
from tank.glyphs import faces_right, mirror_glyph


def _all_glyphs():
    for key, sp in sorted(load_bundled().items()):
        for g in sp.glyph_pool:
            yield key, g


def test_mirror_is_an_involution():
    """Mirroring twice must return the original, for every shipped glyph."""
    for key, g in _all_glyphs():
        assert mirror_glyph(mirror_glyph(g)) == g, f"{key}: {g!r} does not round-trip"


def test_mirror_swaps_wedges_and_parens():
    assert mirror_glyph("><(°>") == "<°)><"
    assert mirror_glyph("><((((°>") == "<°))))><"
    assert mirror_glyph("><(W(°>") == "<°)W)><"
    assert mirror_glyph("@_") == "_@"
    assert mirror_glyph("(\\°°/)") == "(\\°°/)"   # symmetric: unchanged


def test_symmetric_creatures_are_unchanged_by_mirroring():
    """Crab and cleaner shrimp read the same both ways — mirroring is a no-op."""
    best = load_bundled()
    for key in ("crab", "cleanershrimp"):
        for g in best[key].glyph_pool:
            assert mirror_glyph(g) == g, f"{key}: {g!r} is not symmetric"


def test_every_bestiary_glyph_is_authored_facing_right():
    """THE regression guard. One canonical facing, asserted against the data.

    If someone adds a left-facing spelling back into a pool, this fails here
    rather than silently rendering a moonwalking fish on brokenbranch.dev.
    """
    backwards = [(key, g) for key, g in _all_glyphs() if not faces_right(g)]
    assert not backwards, f"glyphs authored facing left: {backwards}"


def test_no_pool_contains_a_glyph_and_its_own_mirror():
    """Pools hold distinct DESIGNS, never both spellings of one fish.

    Storing a mirror pair is what made facing ambiguous in the first place:
    two entries that are the same fish, and nothing in the data saying so.
    """
    for key, sp in sorted(load_bundled().items()):
        pool = list(sp.glyph_pool)
        for g in pool:
            m = mirror_glyph(g)
            if m == g:
                continue  # symmetric creature, fine
            assert m not in pool, f"{key}: pool holds both {g!r} and its mirror {m!r}"


def test_pools_have_no_duplicates():
    for key, sp in sorted(load_bundled().items()):
        pool = list(sp.glyph_pool)
        assert len(pool) == len(set(pool)), f"{key}: duplicate glyphs in pool {pool}"


@pytest.mark.parametrize("glyph", ["><(°>", "><o>", "{·_·}>", ">.>", "@_"])
def test_faces_right_accepts_canonical_forms(glyph):
    assert faces_right(glyph)


@pytest.mark.parametrize("glyph", ["<°)><", "<o><", "<°)W><", "<°))))><"])
def test_faces_right_rejects_the_old_left_facing_spellings(glyph):
    """Negative control — the lint must actually reject something."""
    assert not faces_right(glyph)
