"""Tests for the fact-retrieval plumbing in `core.turn.recall.build_turn_context`.

IM-CRIT-3 / TURN-HIGH-4: the inlined fact-retrieval block in build_turn_context
must thread the turn's content through to `get_decay_weighted_facts` so the
underlying re-ranker can bias the top-k toward facts that mention what the
user is talking about. Pre-fix, the wrapper called get_decay_weighted_facts
without the query kwarg and every turn got the same decay-ranked top-k.

E.2 step 2 (2026-05-13): the per-section `_safe_retrieve_facts` helper was
inlined into ``build_turn_context``. These tests now drive the contract
through the public entry point — they intercept ``get_decay_weighted_facts``
and assert the kwargs the inlined block sends, instead of testing the
removed helper directly.

The recall module is the unit under test (loaded via load_module to avoid
core.config side effects); ai.fact_retrieval is its dependency, imported
canonically.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_recall():
    from tests.agent.conftest import load_module

    return load_module("core/turn/recall.py", "recall_under_test_for_facts")


def _build_turn(content: str, owner_id: int = 7):
    """Construct a minimal Turn the inlined recall pipeline accepts."""
    from core.turn.types import Turn, TurnMetadata

    return Turn(
        surface="dashboard",
        owner_id=owner_id,
        content=content,
        images=[],
        metadata=TurnMetadata(),
    )


def _stub_recall_dependencies(monkeypatch, *, fact_capture: Dict[str, Any], fake_facts):
    """Stub out the non-fact recall calls so build_turn_context returns
    cleanly while we observe the fact-retrieval plumbing."""
    import ai.fact_retrieval as fact_retrieval

    async def fake_get_decay_weighted_facts(**kwargs):
        fact_capture.update(kwargs)
        return fake_facts

    monkeypatch.setattr(
        fact_retrieval, "get_decay_weighted_facts", fake_get_decay_weighted_facts,
    )

    # Identity compile: return None to skip
    import core.identity.service as identity_service_module
    monkeypatch.setattr(
        identity_service_module.identity_service,
        "compile_for_owner",
        lambda owner_id: None,
    )

    # Conversation history: return [] so the loop is a no-op
    import core.conversation_history_store as chs
    monkeypatch.setattr(
        chs.ConversationHistoryStore,
        "get_conversation_history",
        AsyncMock(return_value=[]),
    )

    # Inner-memory query: return [] so the FTS5 block is a no-op
    import ai.inner_memory_index as ami
    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[]))

    # Batch 5, task 4 (ruling R5): the memory-of-you search now feeds the
    # band too (its memory bullets go first) — return no results so the
    # fact half is observed alone; and the owner's refs snapshot is loaded
    # by recall — stub the loader so the brain is never touched.
    import core.memory_service as ms
    monkeypatch.setattr(ms.memory_workspace_service, "search", AsyncMock(return_value={"results": []}))
    from core import memory_refs
    monkeypatch.setattr(memory_refs, "ensure_loaded", AsyncMock(return_value={}))

    # First-contact targets: return []
    import core.first_contact_targets as fct
    monkeypatch.setattr(fct, "unfulfilled_targets_for_owner", lambda owner_id: [])

    # No live model call (R15, T12, fix round 1): the per-turn read is a real
    # chat completion otherwise (the local model server, 127.0.0.1:11434). A
    # test that wants a read re-patches take_turn_read in its own body.
    import ai.turn_read as turn_read_mod
    monkeypatch.setattr(turn_read_mod, "take_turn_read", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_build_turn_context_passes_query_to_decay_weighted_facts(monkeypatch):
    """The inlined fact block must thread turn.content into get_decay_weighted_facts.

    Pre-IM-CRIT-3 fix the wrapper called get_decay_weighted_facts without
    the query kwarg; this test fails on that code path because the captured
    kwargs would not contain `query`.

    Re-premised for Batch 5, task 4 (ruling R5): the user_memory feed is the
    band's SECOND half, after recall's looked-up memory bullets, and it must
    leave out her own ``moment`` rows (154 of 186 live) so the eight slots
    can hold typed facts — the block asks the feed to exclude them. Each
    fact dict carries the row ``id`` and ``memory_type`` alongside key/value
    so Task 5 can bump what the band rendered.
    """
    captured_kwargs: Dict[str, Any] = {}

    class _FakeWeighted:
        def __init__(self, content: str, fact_id: int):
            self.fact = {
                "id": fact_id,
                "content": content,
                "memory_type": "preference",
                "importance_score": 0.8,
            }

    fake_facts = [_FakeWeighted(content="User likes jazz.", fact_id=42)]
    _stub_recall_dependencies(monkeypatch, fact_capture=captured_kwargs, fake_facts=fake_facts)

    recall = _load_recall()
    turn = _build_turn("how is the cat doing?")
    ctx = await recall.build_turn_context(turn)

    # Inlined block must have passed query through.
    assert captured_kwargs.get("query") == "how is the cat doing?"
    assert captured_kwargs.get("user_id") == 7
    assert captured_kwargs.get("max_results") == 8
    assert captured_kwargs.get("exclude_memory_types") == ("moment",)
    # And the result must be shaped onto TurnContext.relevant_facts.
    assert ctx.relevant_facts and ctx.relevant_facts[0]["value"] == "User likes jazz."
    assert ctx.relevant_facts[0]["id"] == 42 and ctx.relevant_facts[0]["memory_type"] == "preference"


@pytest.mark.asyncio
async def test_build_turn_context_passes_empty_query_unchanged(monkeypatch):
    """Empty string query must still flow through; the underlying function is
    responsible for treating it as decay-only. The recall block does not
    second-guess the query — it just plumbs it through verbatim."""
    captured_kwargs: Dict[str, Any] = {}
    _stub_recall_dependencies(monkeypatch, fact_capture=captured_kwargs, fake_facts=[])

    recall = _load_recall()
    turn = _build_turn("")
    await recall.build_turn_context(turn)

    # Empty string still passes through — the block does not silently drop it.
    # ai.fact_retrieval is responsible for deciding what an empty query means.
    assert "query" in captured_kwargs
    assert captured_kwargs["query"] == ""


@pytest.mark.asyncio
async def test_build_turn_context_swallows_fact_retrieval_exceptions(monkeypatch):
    """Recall is best-effort: any failure in the fact-retrieval pipeline
    must degrade to an empty list rather than break turn-context build."""
    import ai.fact_retrieval as fact_retrieval

    async def boom(**kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(fact_retrieval, "get_decay_weighted_facts", boom)
    # Also stub the rest so the rest of recall doesn't trip its own deps.
    _stub_recall_dependencies(
        monkeypatch, fact_capture={}, fake_facts=[],
    )
    # Re-apply boom because _stub_recall_dependencies overwrote it.
    monkeypatch.setattr(fact_retrieval, "get_decay_weighted_facts", boom)

    recall = _load_recall()
    turn = _build_turn("anything")
    ctx = await recall.build_turn_context(turn)
    assert ctx.relevant_facts == ()


@pytest.mark.asyncio
async def test_build_turn_context_surfaces_turn_read_and_arc(monkeypatch):
    """Caring-memory regression (2026-06-13): build_turn_context computes the
    per-turn read + emotional arc (a real LLM call, cost paid every turn) but
    the TurnContext it returned DROPPED them. Consequence: reflect's emotional
    timeline / salience / circle-back writes (read side: ctx.turn_read is None)
    AND the live reply's "your read of this moment" + arc sections
    (context_adapter -> system_prompt) were silently empty on every turn since
    caring_memory shipped. The computed read must reach the returned context."""
    _stub_recall_dependencies(monkeypatch, fact_capture={}, fake_facts=[])

    class _FakeRead:
        read = "He's grieving and deflecting with humor."
        needs_support = 0.8

    sentinel = _FakeRead()

    import ai.turn_read as turn_read_mod
    monkeypatch.setattr(turn_read_mod, "take_turn_read", AsyncMock(return_value=sentinel))
    monkeypatch.setattr(
        turn_read_mod, "render_recent_arc", AsyncMock(return_value="Arc: lighter this week."),
    )

    recall = _load_recall()
    turn = _build_turn("just got back from my grandmother's sister's funeral")
    ctx = await recall.build_turn_context(turn)

    assert ctx.turn_read is sentinel, "turn_read was computed but dropped from TurnContext"
    assert ctx.emotional_arc_text == "Arc: lighter this week."
