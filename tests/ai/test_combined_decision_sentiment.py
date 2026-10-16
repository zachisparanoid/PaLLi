"""Phase 3 Task 3.3 — end-to-end shape test for combined_decision sentiment.

Parser-side sentiment coercion is covered in
test_combined_decision_baseline.py (present / absent / invalid tag).
This file locks in the full round-trip shape so downstream consumers
can rely on it.

Apply-layer writeback coverage lives in
tests/core/test_message_should_respond.py.
"""
from __future__ import annotations

import json

import pytest

from ai.llm_decision import parse_combined_decision_response


def test_parse_includes_sentiment_block_shape():
    """End-to-end parser: shape matches spec."""
    raw = json.dumps({
        "should_respond": True,
        "response_reasoning": "direct question",
        "sentiment": {
            "tag": "curious",
            "read": "reads curious and engaged",
            "confidence": 0.8,
            "shift": "confirmed",
        },
    })
    result = parse_combined_decision_response(raw, user_message="what's up?")
    assert result["sentiment"] == {
        "tag": "curious",
        "read": "reads curious and engaged",
        "confidence": pytest.approx(0.8),
        "shift": "confirmed",
    }


def test_parse_sentiment_shift_shifted_preserved():
    raw = json.dumps({
        "should_respond": True,
        "response_reasoning": "tone shift",
        "sentiment": {
            "tag": "frustrated",
            "read": "holding back but clearly fed up",
            "confidence": 0.7,
            "shift": "shifted",
        },
    })
    result = parse_combined_decision_response(raw, user_message="ugh fine")
    assert result["sentiment"]["shift"] == "shifted"
    assert result["sentiment"]["tag"] == "frustrated"


def test_parse_sentiment_shift_unknown_value_defaults_to_confirmed():
    raw = json.dumps({
        "should_respond": True,
        "response_reasoning": "x",
        "sentiment": {
            "tag": "content",
            "read": "x",
            "confidence": 0.5,
            "shift": "maybe-something",
        },
    })
    result = parse_combined_decision_response(raw, user_message="ok")
    assert result["sentiment"]["shift"] == "confirmed"
