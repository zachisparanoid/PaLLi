"""Phase 3 Task 2.2 — sentiment baseline integration in monologue tick.

These are unit-level tests focused on the new wiring:
  - _parse_response surfaces the sentiment block from the LLM
  - _run_tick stamps sentiment onto the MonologueEntry
  - _run_tick calls update_user_sentiment_state when a payload is present
  - missing/invalid sentiment is handled gracefully (no persist, no crash)

End-to-end verification (real LLM + real DB) happens in Phase 7.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai.inner_monologue import InnerMonologueEngine
from ai.sentiment.types import DEFAULT_READING, SentimentReading


# ─── _parse_response: sentiment pass-through ─────────────────────────


def test_parse_response_returns_sentiment_payload_when_present():
    engine = InnerMonologueEngine()
    response = json.dumps({
        "thought": "He's deep in something.",
        "substantive": True,
        "sentiment": {
            "tag": "focused",
            "read": "short replies, long gaps — heads-down mode",
            "confidence": 0.7,
        },
    })
    thought, substantive, sentiment, _candidate, _kind, _hint, _curiosity = engine._parse_response(response)
    assert thought == "He's deep in something."
    assert substantive is True
    assert sentiment == {
        "tag": "focused",
        "read": "short replies, long gaps — heads-down mode",
        "confidence": 0.7,
    }


def test_parse_response_returns_none_when_sentiment_is_not_dict():
    """Malformed sentiment (wrong shape) must not leak through as garbage."""
    engine = InnerMonologueEngine()
    response = json.dumps({
        "thought": "Something.",
        "substantive": True,
        "sentiment": "not a dict",
    })
    _, _, sentiment, _candidate, _kind, _hint, _cur = engine._parse_response(response)
    assert sentiment is None


def test_parse_response_returns_none_when_sentiment_absent():
    """Pre-Phase-3 responses must still parse cleanly."""
    engine = InnerMonologueEngine()
    response = json.dumps({"thought": "x", "substantive": False})
    _, _, sentiment, _candidate, _kind, _hint, _cur = engine._parse_response(response)
    assert sentiment is None


# ─── _run_tick: MonologueEntry + update_user_sentiment_state ─────────


def _fake_context_with_owner(owner_user_id: int = 42) -> dict:
    return {
        "owner_user_id": owner_user_id,
        "history_text": "No recent conversation.",
        "identity_snapshot": "{}",
        "situation_context": "It's Tuesday afternoon.",
        "had_recent_conversation": False,
        "history_turn_count": 0,
        "situation_sources": ["time"],
        "unavailable_sources": [],
        "situation_snapshot": {"time_bucket": "afternoon"},
    }


@pytest.mark.asyncio
async def test_run_tick_stamps_sentiment_onto_entry():
    """The sentiment block from the LLM should land on MonologueEntry.sentiment."""
    from ai.inner_monologue_store import MonologueEntry

    captured: dict = {}

    def _capture(entry: MonologueEntry):
        captured["entry"] = entry
        return None

    engine = InnerMonologueEngine()
    llm_output = json.dumps({
        "thought": "Quiet afternoon.",
        "substantive": True,
        "sentiment": {"tag": "content", "read": "ok", "confidence": 0.6},
    })

    mock_brain = MagicMock()
    mock_brain.get_user_sentiment_state = AsyncMock(return_value=DEFAULT_READING)
    mock_brain.update_user_sentiment_state = AsyncMock(return_value=None)

    with patch.object(
        engine, "_gather_context", new=AsyncMock(return_value=_fake_context_with_owner()),
    ), patch(
        "ai.inner_monologue.prompt_loader.load_prompt", return_value="System {situation_context}",
    ), patch(
        "ai.inner_monologue.llm.chat_completion", new=AsyncMock(return_value=llm_output),
    ), patch(
        "ai.inner_monologue.write_entry", side_effect=_capture,
    ), patch(
        "ai.inner_monologue.brain", mock_brain,
    ):
        await engine._run_tick()

    entry = captured["entry"]
    assert entry.sentiment == {"tag": "content", "read": "ok", "confidence": 0.6}


@pytest.mark.asyncio
async def test_run_tick_persists_sentiment_when_payload_present():
    """Valid sentiment → update_user_sentiment_state called with coerced reading."""
    engine = InnerMonologueEngine()
    llm_output = json.dumps({
        "thought": "Seems tired.",
        "substantive": True,
        "sentiment": {"tag": "tired", "read": "slow replies", "confidence": 0.8},
    })

    mock_brain = MagicMock()
    mock_brain.get_user_sentiment_state = AsyncMock(return_value=DEFAULT_READING)
    mock_brain.update_user_sentiment_state = AsyncMock(return_value=None)

    with patch.object(
        engine, "_gather_context",
        new=AsyncMock(return_value=_fake_context_with_owner(owner_user_id=7)),
    ), patch(
        "ai.inner_monologue.prompt_loader.load_prompt", return_value="System",
    ), patch(
        "ai.inner_monologue.llm.chat_completion", new=AsyncMock(return_value=llm_output),
    ), patch(
        "ai.inner_monologue.write_entry", return_value=None,
    ), patch(
        "ai.inner_monologue.brain", mock_brain,
    ):
        await engine._run_tick()

    mock_brain.update_user_sentiment_state.assert_awaited_once()
    call = mock_brain.update_user_sentiment_state.await_args
    assert call.args[0] == 7
    reading: SentimentReading = call.kwargs["reading"]
    assert reading.tag == "tired"
    assert reading.read == "slow replies"
    assert reading.confidence == 0.8
    assert reading.source == "monologue_tick"


@pytest.mark.asyncio
async def test_run_tick_skips_persist_when_no_sentiment():
    """LLM omits sentiment block → no write attempted."""
    engine = InnerMonologueEngine()
    # Pre-Phase-3-style response, no sentiment field
    llm_output = json.dumps({"thought": "x", "substantive": True})

    mock_brain = MagicMock()
    mock_brain.get_user_sentiment_state = AsyncMock(return_value=DEFAULT_READING)
    mock_brain.update_user_sentiment_state = AsyncMock(return_value=None)

    with patch.object(
        engine, "_gather_context",
        new=AsyncMock(return_value=_fake_context_with_owner()),
    ), patch(
        "ai.inner_monologue.prompt_loader.load_prompt", return_value="System",
    ), patch(
        "ai.inner_monologue.llm.chat_completion", new=AsyncMock(return_value=llm_output),
    ), patch(
        "ai.inner_monologue.write_entry", return_value=None,
    ), patch(
        "ai.inner_monologue.brain", mock_brain,
    ):
        await engine._run_tick()

    mock_brain.update_user_sentiment_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_tick_coerces_invalid_tag_to_neutral_and_still_persists():
    """Invalid tag from LLM → inference layer coerces to neutral — we still write."""
    engine = InnerMonologueEngine()
    llm_output = json.dumps({
        "thought": "x",
        "substantive": True,
        "sentiment": {"tag": "effervescent", "read": "ok", "confidence": 0.9},
    })

    mock_brain = MagicMock()
    mock_brain.get_user_sentiment_state = AsyncMock(return_value=DEFAULT_READING)
    mock_brain.update_user_sentiment_state = AsyncMock(return_value=None)

    with patch.object(
        engine, "_gather_context",
        new=AsyncMock(return_value=_fake_context_with_owner()),
    ), patch(
        "ai.inner_monologue.prompt_loader.load_prompt", return_value="System",
    ), patch(
        "ai.inner_monologue.llm.chat_completion", new=AsyncMock(return_value=llm_output),
    ), patch(
        "ai.inner_monologue.write_entry", return_value=None,
    ), patch(
        "ai.inner_monologue.brain", mock_brain,
    ):
        await engine._run_tick()

    mock_brain.update_user_sentiment_state.assert_awaited_once()
    reading: SentimentReading = mock_brain.update_user_sentiment_state.await_args.kwargs["reading"]
    assert reading.tag == "neutral"
    assert reading.confidence == 0.0


@pytest.mark.asyncio
async def test_run_tick_swallows_persist_errors():
    """A DB failure in sentiment persist must not abort the tick."""
    engine = InnerMonologueEngine()
    llm_output = json.dumps({
        "thought": "x",
        "substantive": True,
        "sentiment": {"tag": "focused", "read": "deep", "confidence": 0.7},
    })

    mock_brain = MagicMock()
    mock_brain.get_user_sentiment_state = AsyncMock(side_effect=RuntimeError("DB down"))
    mock_brain.update_user_sentiment_state = AsyncMock(return_value=None)

    with patch.object(
        engine, "_gather_context",
        new=AsyncMock(return_value=_fake_context_with_owner()),
    ), patch(
        "ai.inner_monologue.prompt_loader.load_prompt", return_value="System",
    ), patch(
        "ai.inner_monologue.llm.chat_completion", new=AsyncMock(return_value=llm_output),
    ), patch(
        "ai.inner_monologue.write_entry", return_value=None,
    ), patch(
        "ai.inner_monologue.brain", mock_brain,
    ):
        # Should not raise despite the DB failure.
        await engine._run_tick()

    # last_error should be empty — the tick itself succeeded.
    assert engine.last_error == ""
