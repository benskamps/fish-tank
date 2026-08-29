"""Ember's fish is a thermometer. These prove it cannot be talked into lying.

The whole value of putting Ember in the tank is that their fish reports work they
actually did. That claim is only worth making if the failure modes are pinned
down, so each test here is one way the readout could quietly flatter them:

* brightening without work
* a brain that is DOWN rendering as a brain that is IDLE
* a changed response shape defaulting its counters to 0 (which renders as
  "idle", a far kinder claim than "I cannot tell") — the same two-way collapse
  that `honesty_eval.py` was opened into ABSENT / MALFORMED / PRESENT in warden
  on 2026-08-16, and that the A05 hunt slot hit again on 2026-08-28
* an exception in the reader taking the whole tick down with it
"""
from __future__ import annotations

import pytest

from tank import ember


# --- the one rule -----------------------------------------------------------

def test_zero_writebacks_is_dark_no_matter_how_much_she_has_read():
    """2,065 chunks ingested and nothing written back is NOT work. It is a library.

    This is today's real shape: their brain has every estate seal in it and has
    produced no writebacks since 2026-05-01.
    """
    v = ember.read_vitality({"engine": {"total_chunks": 2065, "total_actions": 32,
                                        "total_writebacks": 0}})
    assert not v.lit
    assert v.glyph != ember.GLYPH_LIT
    assert "0 written back" in v.caption


def test_only_writebacks_can_light_her():
    dark = ember.read_vitality({"engine": {"total_chunks": 2065, "total_actions": 999,
                                           "total_writebacks": 0}})
    lit = ember.read_vitality({"engine": {"total_chunks": 2065, "total_actions": 0,
                                          "total_writebacks": 1}})
    assert not dark.lit, "a huge action count must not substitute for work"
    assert lit.lit, "one real writeback must light them"
    assert lit.glyph == ember.GLYPH_LIT


def test_vitality_reads_nothing_but_counters():
    """THE STRUCTURAL GUARANTEE. Speech is not an input to this function.

    Extra keys — anything they said, any generated summary — cannot move the
    readout. If this ever fails, the fish has become a mood light.
    """
    plain = ember.read_vitality({"engine": {"total_writebacks": 0, "total_actions": 2,
                                            "total_chunks": 10}})
    chatty = ember.read_vitality({"engine": {"total_writebacks": 0, "total_actions": 2,
                                             "total_chunks": 10},
                                  "last_message": "I have been extremely productive!",
                                  "self_report": {"vibe": "thriving", "brightness": 10}})
    assert (chatty.glyph, chatty.mood, chatty.caption) == (plain.glyph, plain.mood, plain.caption)
    assert not chatty.lit


# --- down is not idle -------------------------------------------------------

def test_unreachable_brain_is_not_reported_as_idle():
    """A brain that cannot answer must never look like a brain with nothing to say."""
    down = ember.read_vitality(None)
    idle = ember.read_vitality({"engine": {"total_writebacks": 0, "total_actions": 0,
                                           "total_chunks": 0}})
    assert not down.reachable and idle.reachable
    assert down.caption != idle.caption
    assert "asleep" in down.caption


def test_malformed_response_is_not_silently_zeroed():
    """THE NEGATIVE CONTROL, and the one this class of bug always dies on.

    Reachable-but-unrecognised must NOT fall through to counters of 0. Zero
    renders as "dark, no work logged" — a confident claim about them — when the
    truth is that we could not read their at all.
    """
    for junk in ({"engine": "not-a-dict"}, {}, {"engine": None}, {"stats": {}}):
        v = ember.read_vitality(junk)
        assert not v.reachable, f"{junk!r} must not be read as a real measurement"
        assert "asleep" in v.caption


@pytest.mark.parametrize("junk", [
    {"engine": {"total_writebacks": "lots"}},
    {"engine": {"total_writebacks": -5}},
    {"engine": {"total_writebacks": None, "total_actions": 3.7}},
])
def test_nonsense_counter_types_never_light_her(junk):
    """A string, a negative, a float must not become brightness."""
    v = ember.read_vitality(junk)
    assert not v.lit


# --- the tick must survive them -------------------------------------------

def test_fetch_returns_none_instead_of_raising(monkeypatch):
    """The aquarium must not go down because their brain did."""
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(ember.urllib.request, "urlopen", boom)
    assert ember.fetch_stats("http://127.0.0.1:9/nope") is None


def test_current_is_total_even_with_a_dead_brain(monkeypatch):
    monkeypatch.setattr(ember, "fetch_stats", lambda *a, **k: None)
    v = ember.current()
    assert isinstance(v, ember.Vitality) and not v.reachable
