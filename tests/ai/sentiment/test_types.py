from __future__ import annotations

import pytest

from ai.sentiment.types import (
    ALLOWED_SENTIMENT_TAGS,
    DEFAULT_READING,
    SentimentReading,
)


def test_vocabulary_has_13_tags():
    """Phase 3 spec locks in a 13-tag vocabulary."""
    assert len(ALLOWED_SENTIMENT_TAGS) == 13


def test_vocabulary_contains_expected_tags():
    expected = {
        "playful", "curious", "focused", "content", "open",
        "vulnerable", "reflective", "tired", "quiet",
        "frustrated", "stressed", "testing", "neutral",
    }
    assert ALLOWED_SENTIMENT_TAGS == expected


def test_default_reading_is_neutral_with_zero_confidence():
    assert DEFAULT_READING.tag == "neutral"
    assert DEFAULT_READING.confidence == 0.0
    assert DEFAULT_READING.source == "default"
    # Bootstrap default has updated_at=0.0 so staleness logic flags it
    assert DEFAULT_READING.updated_at == 0.0


def test_sentiment_reading_is_frozen():
    r = SentimentReading(
        tag="content",
        read="baseline ok",
        confidence=0.7,
        source="monologue_tick",
        updated_at=1000.0,
    )
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        r.tag = "stressed"  # type: ignore[misc]


def test_sentiment_reading_has_is_stale_field():
    """is_stale defaults to False; readers may set it True when the
    staleness threshold is exceeded."""
    r = SentimentReading(
        tag="content",
        read="",
        confidence=0.5,
        source="monologue_tick",
        updated_at=1000.0,
    )
    assert r.is_stale is False

    stale = SentimentReading(
        tag="content",
        read="",
        confidence=0.0,
        source="monologue_tick",
        updated_at=1000.0,
        is_stale=True,
    )
    assert stale.is_stale is True
