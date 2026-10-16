"""Sentiment reading dataclass + vocabulary.

Immutable by design. Consumers construct via ai.sentiment.inference
(for LLM output) or read via ai.sentiment.store (for persisted state).
"""
from __future__ import annotations

from dataclasses import dataclass

ALLOWED_SENTIMENT_TAGS = frozenset({
    "playful", "curious", "focused", "content", "open",
    "vulnerable", "reflective", "tired", "quiet",
    "frustrated", "stressed", "testing", "neutral",
})


@dataclass(frozen=True, slots=True)
class SentimentReading:
    """One sentiment snapshot — a tag, a free-text read, confidence, source, time.

    is_stale is set by the store's reader when the reading's updated_at
    is older than SENTIMENT_STALENESS_HOURS. Callers never set it
    directly on creation unless they're testing stale-handling.
    """
    tag: str
    read: str
    confidence: float
    source: str
    updated_at: float
    is_stale: bool = False


DEFAULT_READING = SentimentReading(
    tag="neutral",
    read="no sentiment data yet",
    confidence=0.0,
    source="default",
    updated_at=0.0,
    is_stale=False,
)
