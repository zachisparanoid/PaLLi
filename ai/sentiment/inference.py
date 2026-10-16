"""Build SentimentReading instances from raw LLM output.

The LLM sometimes returns invalid tags, missing fields, or confidence
values outside [0, 1]. This module coerces safely — it never raises
on malformed input; it logs a WARNING and falls back to a neutral
reading. Consumers always get a valid SentimentReading out.
"""
from __future__ import annotations

import logging
from typing import Any

from ai.sentiment.types import ALLOWED_SENTIMENT_TAGS, SentimentReading

logger = logging.getLogger(__name__)


def build_reading_from_llm_output(
    *,
    payload: Any,
    source: str,
    updated_at: float,
) -> SentimentReading:
    """Coerce an LLM's sentiment output dict into a SentimentReading.

    `payload` is the parsed JSON fragment (e.g. the value at the
    "sentiment" key of the combined_decision or inner_monologue
    output). Any structure other than a dict with at least a "tag"
    field produces a neutral fallback reading at the given `source`
    and `updated_at`.
    """
    if not isinstance(payload, dict):
        logger.warning(
            "sentiment: LLM payload was %s, not dict; using neutral fallback",
            type(payload).__name__,
        )
        return SentimentReading(
            tag="neutral",
            read="",
            confidence=0.0,
            source=source,
            updated_at=updated_at,
        )

    raw_tag = str(payload.get("tag") or "").strip().lower()
    read = str(payload.get("read") or "").strip()

    # Confidence: tolerate strings that coerce; fall back to middling
    # when the LLM omits or botches it.
    confidence_raw = payload.get("confidence", 0.5)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        logger.warning(
            "sentiment: non-numeric confidence %r; defaulting to 0.5",
            confidence_raw,
        )
        confidence = 0.5
    # Clamp to [0, 1]
    confidence = max(0.0, min(1.0, confidence))

    if raw_tag not in ALLOWED_SENTIMENT_TAGS:
        if raw_tag:
            logger.warning(
                "sentiment: unknown tag %r (not in %d-tag vocabulary); "
                "coercing to neutral with confidence 0.0",
                raw_tag,
                len(ALLOWED_SENTIMENT_TAGS),
            )
        return SentimentReading(
            tag="neutral",
            read=read,
            confidence=0.0,
            source=source,
            updated_at=updated_at,
        )

    return SentimentReading(
        tag=raw_tag,
        read=read,
        confidence=confidence,
        source=source,
        updated_at=updated_at,
    )
