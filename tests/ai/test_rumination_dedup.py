"""Tests for the rumination-loop dedup added in proactive_candidate_source."""
from __future__ import annotations

from ai.proactive_candidate_source import _candidate_norm


def test_norm_collapses_near_duplicates():
    """Two candidates expressing the same hunch in different words should
    normalize to the same key."""
    a = _candidate_norm("Gentle check-in this evening about sleep and grounding")
    b = _candidate_norm("Send a low-pressure check-in tonight about sleep and grounding")
    # Both should share the meaningful tokens (check, sleep, grounding)
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    overlap = a_tokens & b_tokens
    assert len(overlap) >= 3, f"expected 3+ shared content tokens; got overlap={overlap}"


def test_norm_drops_stopwords():
    norm = _candidate_norm("the user should be the focus of this and that")
    assert "the" not in norm.split()
    assert "and" not in norm.split()
    assert "this" not in norm.split()


def test_norm_drops_punctuation():
    norm = _candidate_norm("Send: a check-in (gentle).")
    assert "send" in norm.split()
    assert "check" in norm.split() or "checkin" in norm.split()
    assert "(" not in norm
    assert ":" not in norm


def test_norm_caps_token_count():
    """Very long candidate text should still produce a bounded norm."""
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    norm = _candidate_norm(text)
    assert len(norm.split()) <= 8


def test_norm_empty_string_returns_empty():
    assert _candidate_norm("") == ""
    assert _candidate_norm("   ") == ""


def test_norm_distinguishes_genuinely_different_candidates():
    a = _candidate_norm("check on the broker response about the workshop")
    b = _candidate_norm("ask if they finished writing chapter nineteen")
    assert a != b, "different topics should not collapse"
