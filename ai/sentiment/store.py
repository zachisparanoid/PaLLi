"""Persistence layer for user_sentiment_state + historical trend query.

Public API:
  get_current_reading(brain, user_id, now=None, staleness_hours=None)
      → SentimentReading   (always valid; bootstrap default on miss)
  update_current_reading(brain, user_id, reading)
      → None               (upsert)
  get_trend(brain, user_id, hours=168)
      → TrendSummary       (aggregates monologue JSONL history)

The staleness threshold defaults to config.SENTIMENT_STALENESS_HOURS
when the caller passes None. Tests pass explicit values.
"""
from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai.sentiment.types import DEFAULT_READING, SentimentReading

logger = logging.getLogger(__name__)


def _resolve_staleness_hours(override: Optional[int]) -> int:
    if override is not None:
        return int(override)
    try:
        from core.config import config
        return int(getattr(config, "SENTIMENT_STALENESS_HOURS", 6))
    except Exception:
        return 6


async def get_current_reading(
    *,
    brain: Any,
    user_id: int,
    now: Optional[float] = None,
    staleness_hours: Optional[int] = None,
) -> SentimentReading:
    """Return the current sentiment reading for the user.

    Always returns a valid SentimentReading:
      - missing row → DEFAULT_READING with source='default'
      - fresh row (within staleness window) → row as-is, is_stale=False
      - stale row → tag preserved from row, confidence forced to 0.0,
        is_stale=True. Source preserved for log transparency.
    """
    current_time = now if now is not None else _time.time()
    threshold_hours = _resolve_staleness_hours(staleness_hours)
    threshold_seconds = threshold_hours * 3600.0

    conn = await brain.get_connection()
    try:
        row = await conn.fetchrow(
            "SELECT user_id, tag, read, confidence, source, updated_at "
            "FROM user_sentiment_state WHERE user_id = $1",
            int(user_id),
        )
    except Exception as exc:
        logger.error(
            "sentiment: DB read failed for user %s: %s", user_id, exc,
        )
        try:
            await conn.close()
        except Exception:
            pass
        return DEFAULT_READING
    finally:
        try:
            await conn.close()
        except Exception:
            pass

    if row is None:
        return DEFAULT_READING

    d = dict(row)
    age = float(current_time) - float(d["updated_at"])
    if age > threshold_seconds:
        logger.info(
            "sentiment: row for user %s is stale (age %.1fh > %sh); "
            "preserving tag=%s, confidence→0.0, is_stale=True",
            user_id, age / 3600.0, threshold_hours, d["tag"],
        )
        return SentimentReading(
            tag=str(d["tag"]),
            read=str(d["read"] or ""),
            confidence=0.0,
            source=str(d["source"]),
            updated_at=float(d["updated_at"]),
            is_stale=True,
        )

    return SentimentReading(
        tag=str(d["tag"]),
        read=str(d["read"] or ""),
        confidence=float(d["confidence"]),
        source=str(d["source"]),
        updated_at=float(d["updated_at"]),
        is_stale=False,
    )


async def update_current_reading(
    *,
    brain: Any,
    user_id: int,
    reading: SentimentReading,
) -> None:
    """Upsert the current reading. Last write wins."""
    conn = await brain.get_connection()
    try:
        await conn.execute(
            "INSERT INTO user_sentiment_state "
            "(user_id, tag, read, confidence, source, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  tag=excluded.tag, "
            "  read=excluded.read, "
            "  confidence=excluded.confidence, "
            "  source=excluded.source, "
            "  updated_at=excluded.updated_at",
            int(user_id),
            reading.tag,
            reading.read,
            float(reading.confidence),
            reading.source,
            float(reading.updated_at),
        )
    except Exception as exc:
        logger.error(
            "sentiment: DB write failed for user %s: %s", user_id, exc,
        )
    finally:
        try:
            await conn.close()
        except Exception:
            pass


# ─── Trend query ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TrendSummary:
    user_id: int
    window_hours: int
    tick_count: int
    tag_distribution: Dict[str, float]  # tag → fraction of ticks
    current_streak: str                  # human-readable e.g. "mostly-content for 2 days"
    shift_events: List[Dict[str, Any]]   # [{from, to, at}, ...]


async def get_trend(
    *,
    brain: Any,
    user_id: int,
    hours: int = 168,
    now: Optional[float] = None,
    jsonl_dir: Optional[str] = None,
) -> TrendSummary:
    """Aggregate sentiment readings from monologue JSONL over the window.

    Not an LLM call — pure file read + dict aggregation.

    `jsonl_dir` overrides the default path (for tests). Default reads
    from `data/inner_monologue/`.
    """
    current_time = now if now is not None else _time.time()
    cutoff = current_time - (hours * 3600)

    # Resolve JSONL directory
    if jsonl_dir is None:
        jsonl_dir = "data/inner_monologue"
    base = Path(jsonl_dir)

    tag_counts: Dict[str, int] = {}
    readings_in_order: List[tuple[float, str]] = []

    if base.exists() and base.is_dir():
        for f in sorted(base.glob("*.jsonl")):
            try:
                with f.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = float(obj.get("timestamp") or 0)
                        if ts < cutoff:
                            continue
                        sentiment = obj.get("sentiment")
                        if not isinstance(sentiment, dict):
                            continue
                        tag = str(sentiment.get("tag") or "").strip().lower()
                        if not tag:
                            continue
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                        readings_in_order.append((ts, tag))
            except Exception as exc:
                logger.warning(
                    "sentiment.trend: could not read %s: %s", f, exc,
                )
                continue

    tick_count = len(readings_in_order)
    if tick_count == 0:
        return TrendSummary(
            user_id=int(user_id),
            window_hours=int(hours),
            tick_count=0,
            tag_distribution={},
            current_streak="no data",
            shift_events=[],
        )

    distribution = {tag: count / tick_count for tag, count in tag_counts.items()}

    # Current streak: count consecutive most recent ticks with the same tag
    readings_in_order.sort()
    most_recent_tag = readings_in_order[-1][1]
    streak_count = 0
    for ts, tag in reversed(readings_in_order):
        if tag == most_recent_tag:
            streak_count += 1
        else:
            break

    # Crude duration: streak_count * 1 hour tick cadence → days when > 24
    if streak_count >= 24:
        streak_desc = f"mostly-{most_recent_tag} for {streak_count // 24} days"
    elif streak_count >= 1:
        streak_desc = f"mostly-{most_recent_tag} for {streak_count} hour(s)"
    else:
        streak_desc = "no streak"

    # Shift events: tag transitions
    shift_events: List[Dict[str, Any]] = []
    last_tag: Optional[str] = None
    for ts, tag in readings_in_order:
        if last_tag is not None and tag != last_tag:
            shift_events.append({"from": last_tag, "to": tag, "at": ts})
        last_tag = tag

    return TrendSummary(
        user_id=int(user_id),
        window_hours=int(hours),
        tick_count=tick_count,
        tag_distribution=distribution,
        current_streak=streak_desc,
        shift_events=shift_events,
    )
