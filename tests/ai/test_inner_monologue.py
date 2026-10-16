from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai.inner_monologue import InnerMonologueEngine
from ai.situation_context import SituationContext, SituationFragment


def test_engine_is_not_running_by_default():
    engine = InnerMonologueEngine()
    assert engine.is_running is False
    assert engine.is_paused is False
    assert engine.tick_count == 0
    assert engine.last_tick_at is None


def test_engine_pause_and_resume_toggles_paused_flag():
    engine = InnerMonologueEngine()
    engine.pause()
    assert engine.is_paused is True
    engine.resume()
    assert engine.is_paused is False


@pytest.mark.asyncio
async def test_situation_prose_includes_time_of_day_bucket():
    """Baseline behavior preserved via the new pipeline."""
    from ai.situation_context import build_situation_context

    ctx = await build_situation_context(owner_user_id=1)
    assert any(
        bucket in ctx.prose
        for bucket in ("morning", "afternoon", "evening", "late night")
    )


def test_parse_response_extracts_thought_and_substantive_from_json():
    engine = InnerMonologueEngine()
    response = json.dumps({"thought": "He seems focused today.", "substantive": True})
    thought, substantive, sentiment, candidate, candidate_kind, action_hint, _curiosity = engine._parse_response(response)
    assert thought == "He seems focused today."
    assert substantive is True
    # Pre-Phase-3 responses omit sentiment — should pass through as None.
    assert sentiment is None
    # Pre-Phase-4 responses omit proactive_candidate — None by default.
    assert candidate is None


def test_parse_response_falls_back_to_raw_text_when_not_json():
    engine = InnerMonologueEngine()
    thought, substantive, sentiment, candidate, candidate_kind, action_hint, _curiosity = engine._parse_response("Just a thought, not JSON.")
    assert thought == "Just a thought, not JSON."
    assert substantive is True
    assert sentiment is None
    assert candidate is None


def test_parse_response_handles_empty_string():
    engine = InnerMonologueEngine()
    thought, substantive, sentiment, candidate, candidate_kind, action_hint, _curiosity = engine._parse_response("")
    assert thought == "(no response)"
    assert substantive is False
    assert sentiment is None
    assert candidate is None


# ─── Phase 4: proactive_candidate parsing ─────────────────────────────


def test_parse_response_extracts_proactive_candidate():
    """Assertion 1: parser accepts proactive_candidate when present."""
    engine = InnerMonologueEngine()
    response = json.dumps({
        "thought": "He said the broker would call today.",
        "substantive": True,
        "sentiment": {"tag": "curious", "read": "waiting", "confidence": 0.7},
        "proactive_candidate": "gentle check-in about broker response",
    })
    thought, substantive, sentiment, candidate, candidate_kind, action_hint, _curiosity = engine._parse_response(response)
    assert thought.startswith("He said")
    assert substantive is True
    assert isinstance(sentiment, dict)
    assert candidate == "gentle check-in about broker response"


def test_parse_response_returns_none_when_candidate_missing():
    """Assertion 2: parser remains valid when proactive_candidate is absent."""
    engine = InnerMonologueEngine()
    response = json.dumps({
        "thought": "Quiet morning.",
        "substantive": True,
    })
    _, _, _, candidate, _, _, _ = engine._parse_response(response)
    assert candidate is None


def test_parse_response_collapses_trivial_candidates_to_none():
    """Trivial hint strings are dropped pre-storage so they never trigger
    proactive cycles (mitigation for prompt drift)."""
    engine = InnerMonologueEngine()
    for trivial in ["", "   ", "check in", "say hi", "n/a", "nothing", "null"]:
        response = json.dumps({
            "thought": "x",
            "substantive": True,
            "proactive_candidate": trivial,
        })
        _, _, _, candidate, _, _, _ = engine._parse_response(response)
        assert candidate is None, f"expected None for {trivial!r}, got {candidate!r}"


def test_parse_response_collapses_null_literal_to_none():
    """If the LLM emits JSON null, parser should yield None not 'None'."""
    engine = InnerMonologueEngine()
    response = json.dumps({
        "thought": "x",
        "substantive": True,
        "proactive_candidate": None,
    })
    _, _, _, candidate, _, _, _ = engine._parse_response(response)
    assert candidate is None


def test_monologue_entry_from_dict_handles_missing_candidate_field():
    """Assertion 3: historical JSONL rows written before Phase 4 lack the
    field entirely. from_dict must not KeyError."""
    from ai.inner_monologue_store import MonologueEntry

    legacy_row = {
        "timestamp": 1714000000.0,
        "thought": "old thought",
        "situation_snapshot": {},
        "tick_metadata": {"substantive": True},
    }
    entry = MonologueEntry.from_dict(legacy_row)
    assert entry.proactive_candidate is None
    assert entry.thought == "old thought"


def test_monologue_entry_from_dict_preserves_candidate():
    """Storage round-trip: writing a candidate, reading it back preserves it."""
    from ai.inner_monologue_store import MonologueEntry

    row = {
        "timestamp": 1714000000.0,
        "thought": "new thought",
        "situation_snapshot": {},
        "tick_metadata": {"substantive": True},
        "proactive_candidate": "broker check-in",
    }
    entry = MonologueEntry.from_dict(row)
    assert entry.proactive_candidate == "broker check-in"


@pytest.mark.asyncio
async def test_gather_context_uses_build_situation_context_for_situation_text():
    """The monologue's _gather_context must route through the new pipeline."""
    fake_ctx = SituationContext(
        fragments=[
            SituationFragment(source="time", text="It's Tuesday afternoon.", available=True),
        ],
        prose="It's Tuesday afternoon.\n\nUnavailable senses: desktop (not yet implemented).",
        unavailable_sources=["desktop"],
        snapshot={"time_bucket": "afternoon"},
    )

    engine = InnerMonologueEngine()

    with patch("ai.inner_monologue.build_situation_context", new=AsyncMock(return_value=fake_ctx)):
        with patch(
            "ai.inner_monologue.get_local_owner_refs",
            new=AsyncMock(return_value={"palli_user_id": 42}),
        ):
            with patch.object(engine, "_collect_recent_history", new=AsyncMock(return_value=[])):
                with patch.object(
                    engine,
                    "_collect_identity_snapshot",
                    new=AsyncMock(return_value="{}"),
                ):
                    ctx = await engine._gather_context()

    assert ctx is not None
    assert "Tuesday afternoon" in ctx["situation_context"]
    assert "Unavailable senses" in ctx["situation_context"]
    assert ctx["situation_sources"] == ["time"]
    assert ctx["unavailable_sources"] == ["desktop"]
    assert ctx["situation_snapshot"] == {"time_bucket": "afternoon"}


@pytest.mark.asyncio
async def test_run_tick_captures_source_lists_in_tick_metadata():
    """JSONL entries must include situation_sources and unavailable_sources."""
    from ai.inner_monologue_store import MonologueEntry

    fake_ctx_dict = {
        "history_text": "No recent conversation.",
        "identity_snapshot": "{}",
        "situation_context": "It's Tuesday afternoon.",
        "had_recent_conversation": False,
        "history_turn_count": 0,
        "situation_sources": ["time"],
        "unavailable_sources": ["conversation", "desktop", "machine_presence", "operator_agenda"],
        "situation_snapshot": {"time_bucket": "afternoon"},
    }

    captured: dict = {}

    def _capture_entry(entry: MonologueEntry):
        captured["entry"] = entry
        return None

    engine = InnerMonologueEngine()

    with patch.object(engine, "_gather_context", new=AsyncMock(return_value=fake_ctx_dict)):
        with patch(
            "ai.inner_monologue.prompt_loader.load_prompt",
            return_value="System prompt {situation_context}",
        ):
            with patch(
                "ai.inner_monologue.llm.chat_completion",
                new=AsyncMock(return_value='{"thought":"Quiet day.","substantive":true}'),
            ):
                with patch("ai.inner_monologue.write_entry", side_effect=_capture_entry):
                    await engine._run_tick()

    entry = captured["entry"]
    assert entry.tick_metadata.get("situation_sources") == ["time"]
    assert entry.tick_metadata.get("unavailable_sources") == [
        "conversation", "desktop", "machine_presence", "operator_agenda"
    ]
    assert entry.situation_snapshot["time_bucket"] == "afternoon"
    assert entry.situation_snapshot["had_recent_conversation"] is False
    assert entry.situation_snapshot["history_turn_count"] == 0
    assert "substantive" in entry.tick_metadata
    assert "duration_seconds" in entry.tick_metadata
