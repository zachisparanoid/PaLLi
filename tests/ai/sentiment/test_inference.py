from __future__ import annotations

import pytest

from ai.sentiment.inference import build_reading_from_llm_output
from ai.sentiment.types import ALLOWED_SENTIMENT_TAGS


def test_build_reading_happy_path():
    r = build_reading_from_llm_output(
        payload={"tag": "focused", "read": "deep work mode", "confidence": 0.8},
        source="monologue_tick",
        updated_at=1700000000.0,
    )
    assert r.tag == "focused"
    assert r.read == "deep work mode"
    assert r.confidence == 0.8
    assert r.source == "monologue_tick"
    assert r.updated_at == 1700000000.0


def test_build_reading_invalid_tag_coerced_to_neutral():
    """If the LLM outputs a tag outside the vocabulary, coerce to neutral
    and zero the confidence so consumers see 'no signal.'"""
    r = build_reading_from_llm_output(
        payload={"tag": "zesty", "read": "odd read", "confidence": 0.9},
        source="monologue_tick",
        updated_at=1700000000.0,
    )
    assert r.tag == "neutral"
    assert r.confidence == 0.0
    # Read text is preserved — useful for debugging even on coerced reads
    assert r.read == "odd read"


def test_build_reading_missing_fields_use_defaults():
    """Missing confidence defaults to 0.5 (middling); missing read to ''."""
    r = build_reading_from_llm_output(
        payload={"tag": "tired"},
        source="per_turn_refinement",
        updated_at=1700000000.0,
    )
    assert r.tag == "tired"
    assert r.read == ""
    assert r.confidence == 0.5


def test_build_reading_confidence_bounds_clamped():
    r_high = build_reading_from_llm_output(
        payload={"tag": "playful", "confidence": 5.0},
        source="monologue_tick",
        updated_at=0.0,
    )
    assert r_high.confidence == 1.0

    r_low = build_reading_from_llm_output(
        payload={"tag": "playful", "confidence": -0.3},
        source="monologue_tick",
        updated_at=0.0,
    )
    assert r_low.confidence == 0.0


def test_build_reading_non_dict_payload_returns_default():
    """Malformed LLM output (not a dict) → default reading, log WARNING."""
    r = build_reading_from_llm_output(
        payload="not a dict",  # type: ignore[arg-type]
        source="monologue_tick",
        updated_at=1700000000.0,
    )
    assert r.tag == "neutral"
    assert r.confidence == 0.0
    assert r.source == "monologue_tick"
    assert r.updated_at == 1700000000.0


def test_build_reading_confidence_not_a_number():
    r = build_reading_from_llm_output(
        payload={"tag": "curious", "confidence": "pretty high"},
        source="monologue_tick",
        updated_at=0.0,
    )
    # String confidence → default 0.5
    assert r.confidence == 0.5


def test_all_vocab_tags_accepted():
    for tag in ALLOWED_SENTIMENT_TAGS:
        r = build_reading_from_llm_output(
            payload={"tag": tag, "confidence": 0.5},
            source="monologue_tick",
            updated_at=0.0,
        )
        assert r.tag == tag
