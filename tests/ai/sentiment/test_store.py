from __future__ import annotations

import json as _json

import pytest

from ai.sentiment.store import (
    get_current_reading,
    get_trend,
    update_current_reading,
)
from ai.sentiment.types import SentimentReading


class _StubConnection:
    """In-memory stand-in for the Brain's async connection.

    Matches the interface ai/sentiment/store.py needs: execute() and
    fetchrow(). UPSERT is simulated as overwrite.
    """
    def __init__(self, rows: dict):
        self._rows = rows

    async def execute(self, sql: str, *args):
        s = sql.strip().lower()
        if s.startswith("insert into user_sentiment_state"):
            # Matches production INSERT arg order: user_id, tag, read,
            # confidence, source, updated_at
            user_id, tag, read, confidence, source, updated_at = args[:6]
            self._rows[int(user_id)] = {
                "user_id": int(user_id),
                "tag": str(tag),
                "read": str(read),
                "confidence": float(confidence),
                "source": str(source),
                "updated_at": float(updated_at),
            }
            return None
        return None

    async def fetchrow(self, sql: str, *args):
        s = sql.strip().lower()
        if "from user_sentiment_state where user_id" in s and args:
            row = self._rows.get(int(args[0]))
            return dict(row) if row else None
        return None

    async def close(self):
        return None


class _StubBrain:
    def __init__(self):
        self._rows: dict = {}

    async def get_connection(self):
        return _StubConnection(self._rows)


@pytest.fixture
def stub_brain():
    return _StubBrain()


@pytest.mark.asyncio
async def test_get_returns_bootstrap_default_on_missing_row(stub_brain):
    """Bootstrap invariant: no row → DEFAULT_READING shape."""
    reading = await get_current_reading(
        brain=stub_brain, user_id=42, now=1700000000.0,
    )
    assert reading.tag == "neutral"
    assert reading.confidence == 0.0
    assert reading.source == "default"


@pytest.mark.asyncio
async def test_update_and_read_roundtrip(stub_brain):
    r = SentimentReading(
        tag="focused", read="deep work", confidence=0.8,
        source="monologue_tick", updated_at=1700000000.0,
    )
    await update_current_reading(brain=stub_brain, user_id=42, reading=r)
    got = await get_current_reading(
        brain=stub_brain, user_id=42, now=1700000100.0,
    )
    assert got.tag == "focused"
    assert got.read == "deep work"
    assert got.confidence == 0.8
    assert got.source == "monologue_tick"
    assert got.is_stale is False


@pytest.mark.asyncio
async def test_stale_reading_preserves_tag_but_zeros_confidence(stub_brain):
    """6h+ old row → tag preserved for log transparency, confidence→0,
    is_stale=True."""
    r = SentimentReading(
        tag="frustrated", read="hit a wall", confidence=0.85,
        source="monologue_tick", updated_at=1700000000.0,
    )
    await update_current_reading(brain=stub_brain, user_id=42, reading=r)

    # Read 7 hours later
    now = 1700000000.0 + (7 * 3600)
    got = await get_current_reading(
        brain=stub_brain, user_id=42, now=now, staleness_hours=6,
    )
    assert got.tag == "frustrated"         # preserved for transparency
    assert got.confidence == 0.0           # forced to 0 on stale
    assert got.is_stale is True


@pytest.mark.asyncio
async def test_fresh_reading_within_threshold_is_not_stale(stub_brain):
    r = SentimentReading(
        tag="content", read="ok", confidence=0.7,
        source="monologue_tick", updated_at=1700000000.0,
    )
    await update_current_reading(brain=stub_brain, user_id=42, reading=r)

    now = 1700000000.0 + (3 * 3600)  # 3 hours — well within default 6
    got = await get_current_reading(
        brain=stub_brain, user_id=42, now=now, staleness_hours=6,
    )
    assert got.is_stale is False
    assert got.confidence == 0.7


@pytest.mark.asyncio
async def test_update_overwrites_prior_row(stub_brain):
    r1 = SentimentReading(
        tag="content", read="a", confidence=0.5,
        source="monologue_tick", updated_at=1000.0,
    )
    r2 = SentimentReading(
        tag="frustrated", read="b", confidence=0.9,
        source="per_turn_refinement", updated_at=2000.0,
    )
    await update_current_reading(brain=stub_brain, user_id=42, reading=r1)
    await update_current_reading(brain=stub_brain, user_id=42, reading=r2)

    got = await get_current_reading(brain=stub_brain, user_id=42, now=2100.0)
    assert got.tag == "frustrated"
    assert got.source == "per_turn_refinement"
    assert got.updated_at == 2000.0


# ─── Trend tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_trend_empty_when_no_jsonl(stub_brain, tmp_path):
    """No JSONL files → tick_count=0, empty distribution."""
    trend = await get_trend(
        brain=stub_brain, user_id=42, hours=168,
        jsonl_dir=str(tmp_path),
    )
    assert trend.tick_count == 0
    assert trend.tag_distribution == {}
    assert trend.current_streak == "no data"


@pytest.mark.asyncio
async def test_get_trend_aggregates_from_jsonl(stub_brain, tmp_path):
    """Parse JSONL records; aggregate tag distribution + streak + shifts."""
    day = tmp_path / "2026-04-20.jsonl"
    now = 1700000000.0
    records = [
        {"timestamp": now - 5 * 3600, "sentiment": {"tag": "content"}},
        {"timestamp": now - 4 * 3600, "sentiment": {"tag": "content"}},
        {"timestamp": now - 3 * 3600, "sentiment": {"tag": "content"}},
        {"timestamp": now - 2 * 3600, "sentiment": {"tag": "tired"}},
        {"timestamp": now - 1 * 3600, "sentiment": {"tag": "tired"}},
    ]
    with day.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(_json.dumps(r) + "\n")

    trend = await get_trend(
        brain=stub_brain, user_id=42, hours=168, now=now,
        jsonl_dir=str(tmp_path),
    )
    assert trend.tick_count == 5
    assert trend.tag_distribution["content"] == 0.6
    assert trend.tag_distribution["tired"] == 0.4
    # Current streak: most recent is 'tired', preceded by 'tired' → 2 hours
    assert "tired" in trend.current_streak
    # Shift events: one shift from content → tired
    assert len(trend.shift_events) == 1
    assert trend.shift_events[0]["from"] == "content"
    assert trend.shift_events[0]["to"] == "tired"


@pytest.mark.asyncio
async def test_get_trend_ignores_malformed_records(stub_brain, tmp_path):
    day = tmp_path / "2026-04-20.jsonl"
    now = 1700000000.0
    with day.open("w", encoding="utf-8") as f:
        f.write("this is not json\n")
        f.write('{"timestamp": 0, "sentiment": {"tag": "content"}}\n')  # outside window
        f.write(_json.dumps({"timestamp": now - 100, "sentiment": {"tag": "content"}}) + "\n")
        f.write('{"timestamp": 123}\n')  # no sentiment
        f.write("\n")  # blank

    trend = await get_trend(
        brain=stub_brain, user_id=42, hours=1, now=now,
        jsonl_dir=str(tmp_path),
    )
    assert trend.tick_count == 1  # only the in-window record with a tag
    assert trend.tag_distribution == {"content": 1.0}
