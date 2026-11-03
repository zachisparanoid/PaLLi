"""Phase 3 Sentiment Engine — user-state sentiment inference + storage.

Two-layer architecture (see docs/superpowers/specs/
2026-04-22-phase-3-sentiment-engine-design.md):
    inner_monologue (hourly)       → produces baseline reading
    combined_decision (per-turn)    → refines per-message

Both write to a single SQLite row (user_sentiment_state table).
Consumers read the row via ai.sentiment.store.get_current_reading
and get back a valid SentimentReading regardless of table state
(bootstrap defaults on missing, staleness on old rows).

Architectural boundary (mirrors the delegation-bridge voicer rule):
sentiment is USER-STATE. It never mutates PaLLi identity or
personality modules. That's a deliberate separation — future phases
may choose to feed sentiment into personality, but that's a
deliberate future design, not an implicit leak here.
"""
from ai.sentiment.types import (
    ALLOWED_SENTIMENT_TAGS,
    DEFAULT_READING,
    SentimentReading,
)

__all__ = ["ALLOWED_SENTIMENT_TAGS", "DEFAULT_READING", "SentimentReading"]
