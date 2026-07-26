"""Glyph facing.

Every glyph in the bestiary is authored facing ONE canonical direction: RIGHT.
Tail ``><`` on the left, body parens curving back toward it, eye, then the snout
``>`` on the right. Symmetric creatures (crab, cleaner shrimp, the night-fish
faces) are symmetric on purpose — mirroring them is a visual no-op.

That is a deliberate change from the original mixed pools, which held both a
left- and a right-facing spelling of the same fish and left every consumer to
*infer* which was which. The inference was unreliable: mirror-pairs collapsed to
the same answer, and the web renderer carried a hand-maintained lookup table
that no test could validate — a wrong entry rendered a permanently backwards
species that every automated probe reported as fine.

So facing is no longer a property of the data. It is a property of the render:

* the web renderer mirrors with ``transform: scaleX(-1)``
* the terminal renderer mirrors with :func:`mirror_glyph` below

Both are computed from the direction the fish is actually travelling, so a fish
can never disagree with itself.
"""
from __future__ import annotations

# Characters with a horizontal mirror image. Anything absent maps to itself,
# which is correct for the symmetric parts of the alphabet (o ° · _ ~ ≈ ^ v V
# x X W F # * = . @ - and the letters).
_MIRROR = {
    "<": ">", ">": "<",
    "(": ")", ")": "(",
    "[": "]", "]": "[",
    "{": "}", "}": "{",
    "/": "\\", "\\": "/",
    "d": "b", "b": "d",
    "p": "q", "q": "p",
}


def mirror_glyph(glyph: str) -> str:
    """Return `glyph` facing the other way.

    Reverses the string and swaps every character that has a mirror image, so
    ``><(°>`` becomes ``<°)><`` — which is exactly the left-facing spelling the
    old bestiary used to store as a second pool entry.

    >>> mirror_glyph("><(°>")
    '<°)><'
    >>> mirror_glyph("V(°°)V")
    'V(°°)V'
    >>> mirror_glyph(mirror_glyph("><((((°>"))
    '><((((°>'
    """
    return "".join(_MIRROR.get(ch, ch) for ch in reversed(glyph))


def faces_right(glyph: str) -> bool:
    """True if `glyph` is written in the canonical (rightward) orientation.

    This is a *lint* for the bestiary, not a runtime facing oracle — nothing
    should be inferring facing any more. It exists so a test can catch a glyph
    that was authored backwards before it ever reaches a renderer.

    The rule: **the snout is the last character.** In this alphabet a fish ends
    with its snout ``>`` when it faces right, and with its forked tail ``><``
    when it faces left — so the final character alone settles it. Symmetric
    creatures, and glyphs with no directional wedge at all (``@_``, ``°v°``),
    are canonical by definition.

    Deliberately NOT "compare the last '>' to the first '<'": the trailing
    ``><`` of a left-facing fish contains a ``>``, so that version scored every
    left-facer as canonical. It passed the whole bestiary and failed its own
    negative control, which is exactly why the negative control is there.
    """
    if mirror_glyph(glyph) == glyph:
        return True  # symmetric — nothing to get wrong
    if not glyph:
        return True
    if "<" not in glyph and ">" not in glyph:
        return True  # no directional wedge (e.g. "@_")
    return glyph[-1] == ">"
