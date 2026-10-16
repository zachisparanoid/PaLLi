"""Recall parallelization guard (2026-07-10).

build_turn_context runs its independent, best-effort fetches CONCURRENTLY
(two waves) instead of strictly sequentially — a pure latency win. The
resulting TurnContext must be byte-identical (the system prompt is a pure
function of it), so these two guards pin the parts that could regress:

  1. recent_memory_hits ORDER — the one order-sensitive value: FTS hits
     first, then semantic hits dedup-appended. Concurrent searches, but a
     deterministic sequential merge.
  2. the independent fetches actually run CONCURRENTLY (not merely reordered).

Broad output-equivalence is covered by the 8 existing build_turn_context
tests (tests/turn/test_recall_*.py, tests/agent/test_dispatch*.py, etc.).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.turn.recall as recall


def _build_turn(content: str, owner_id: int = 7):
    from core.turn.types import Turn, TurnMetadata

    return Turn(
        surface="dashboard",
        owner_id=owner_id,
        content=content,
        images=[],
        metadata=TurnMetadata(),
    )


def _flags(**overrides):
    base = dict(
        semantic_recall=False,
        semantic_recall_max_hits=3,
        semantic_recall_min_similarity=0.3,
        situational_awareness=False,
        mood_tone=False,
        relationship_calibration=False,
        caring_memory=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _stub_clean(monkeypatch, flags=None):
    """Stub every recall dependency to a clean, fast default."""
    monkeypatch.setattr(recall, "companion_settings", lambda: (flags or _flags()))
    import core.identity.service as ids
    monkeypatch.setattr(ids.identity_service, "compile_for_owner", lambda owner_id: None)
    import core.conversation_history_store as chs
    monkeypatch.setattr(chs.ConversationHistoryStore, "get_conversation_history", AsyncMock(return_value=[]))
    import ai.inner_memory_index as ami
    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[]))
    import ai.fact_retrieval as fr
    monkeypatch.setattr(fr, "get_decay_weighted_facts", AsyncMock(return_value=[]))
    import core.first_contact_targets as fct
    monkeypatch.setattr(fct, "unfulfilled_targets_for_owner", lambda owner_id: [])
    import ai.stake_ledger as sl
    monkeypatch.setattr(sl, "ledger_enabled", lambda: False)


@pytest.mark.asyncio
async def test_recall_memory_hits_order_preserved(monkeypatch):
    """FTS hits come first (A, B), then semantic hits are dedup-appended (C);
    the duplicate B from the semantic side is dropped. Order and dedup must
    survive making the two searches concurrent."""
    _stub_clean(monkeypatch, flags=_flags(semantic_recall=True))
    import ai.inner_memory_index as ami
    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[
        SimpleNamespace(text_content="A"),
        SimpleNamespace(text_content="B"),
    ]))
    import core.memory_service as ms
    monkeypatch.setattr(ms.memory_workspace_service, "search", AsyncMock(return_value={
        "results": [
            {"score": 0.9, "semantic_score": 0.9, "snippet": "B"},
            {"score": 0.9, "semantic_score": 0.9, "snippet": "C"},
        ],
    }))
    ctx = await recall.build_turn_context(_build_turn("hi"))
    assert ctx.recent_memory_hits == ("A", "B", "C")


@pytest.mark.asyncio
async def test_recall_runs_independent_fetches_concurrently(monkeypatch):
    """The independent wave-1 fetches (history, inner-memory, facts,
    delegations) must overlap in flight — proof the recall pipeline actually
    parallelizes rather than just reorders sequential awaits."""
    _stub_clean(monkeypatch)
    state = {"active": 0, "max": 0}

    def _track(default):
        async def _inner(*a, **k):
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            await asyncio.sleep(0.05)
            state["active"] -= 1
            return default
        return _inner

    import core.conversation_history_store as chs
    monkeypatch.setattr(chs.ConversationHistoryStore, "get_conversation_history", _track([]))
    import ai.inner_memory_index as ami
    monkeypatch.setattr(ami, "query", _track([]))
    import ai.fact_retrieval as fr
    monkeypatch.setattr(fr, "get_decay_weighted_facts", _track([]))
    import core.database
    # Delegations left recall with the excision; conversation history is the
    # always-on wave-1 member that replaces it as the third concurrency probe.
    monkeypatch.setattr(core.database.brain, "get_conversation_history", _track([]))

    await recall.build_turn_context(_build_turn("hi"))
    assert state["max"] >= 3, (
        f"independent recall fetches did not run concurrently (max in-flight={state['max']})"
    )
