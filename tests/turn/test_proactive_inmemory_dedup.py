"""Tests for the in-memory rumination dedup in core.turn.proactive.

Step 19 (2026-08-19): the two parked consider_outreach fire-path tests
retired with the lane; the normalization/trim primitives below are the
surviving contract (consider_proactive_action's fallback guard class).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def reset_dedup():
    """Clear the in-memory dedup ring before each test."""
    from core.turn import proactive

    proactive._RECENTLY_FIRED_NORMS.clear()
    yield
    proactive._RECENTLY_FIRED_NORMS.clear()


def test_different_candidates_have_distinct_norms(reset_dedup):
    """Two genuinely different candidates normalize to different keys.

    (Rewritten after the open-threads cutover orphaned the
    consider_outreach candidate flow — the normalization helper is the
    surviving dedup primitive, so its semantics are pinned directly.)
    """
    from core.turn import proactive

    norm_a = proactive._candidate_norm_for_inmemory("check broker today about the workshop")
    norm_b = proactive._candidate_norm_for_inmemory("ask how the writing project is going")
    assert norm_a != norm_b


def test_identical_paraphrase_candidates_share_norm(reset_dedup):
    """Two candidates expressing the same hunch in nearly-identical
    word sets normalize to the same key.

    Stopword-stripped, sorted: ["broker", "check", "today", "workshop"]
    for both inputs — they must collapse to one norm. (Rewritten after
    the open-threads cutover orphaned the consider_outreach candidate
    flow; the normalization helper is the surviving dedup primitive.)
    """
    from core.turn import proactive

    norm_a = proactive._candidate_norm_for_inmemory("check the broker today about workshop")
    norm_b = proactive._candidate_norm_for_inmemory("check broker today about the workshop")
    assert norm_a == norm_b


def test_trim_drops_old_entries(reset_dedup):
    from core.turn import proactive
    import time

    # Add a stale entry
    proactive._RECENTLY_FIRED_NORMS["old norm"] = time.time() - 999999
    # Add a fresh entry
    proactive._RECENTLY_FIRED_NORMS["fresh norm"] = time.time()
    proactive._trim_recently_fired()
    assert "old norm" not in proactive._RECENTLY_FIRED_NORMS
    assert "fresh norm" in proactive._RECENTLY_FIRED_NORMS


def test_trim_caps_size(reset_dedup):
    from core.turn import proactive
    import time

    now = time.time()
    for i in range(150):
        proactive._RECENTLY_FIRED_NORMS[f"norm{i}"] = now
    proactive._trim_recently_fired()
    assert len(proactive._RECENTLY_FIRED_NORMS) <= proactive._RECENTLY_FIRED_MAX_ENTRIES
