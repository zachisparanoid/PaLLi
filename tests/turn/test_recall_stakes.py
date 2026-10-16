"""Recall's stake-ledger wiring, exercised END TO END through
build_turn_context (the caring-memory 'computed but dropped' scar means the
constructor hop must be executed, not assumed), plus the REAL take_turn_read
render path (the str.format KeyError silent-death trap).
"""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest

from ai.stake_ledger import Stake


def _load_recall():
    from tests.agent.conftest import load_module

    return load_module("core/turn/recall.py", "recall_under_test_for_stakes")


def _build_turn(content: str, owner_id: int = 7):
    from core.turn.types import Turn, TurnMetadata

    return Turn(
        surface="dashboard",
        owner_id=owner_id,
        content=content,
        images=[],
        metadata=TurnMetadata(),
    )


def _stub_recall_dependencies(monkeypatch):
    """Stub the heavy recall calls so build_turn_context runs cleanly."""
    import ai.fact_retrieval as fact_retrieval

    monkeypatch.setattr(
        fact_retrieval, "get_decay_weighted_facts", AsyncMock(return_value=[])
    )

    import core.identity.service as identity_service_module

    monkeypatch.setattr(
        identity_service_module.identity_service,
        "compile_for_owner",
        lambda owner_id: None,
    )

    import core.conversation_history_store as chs

    monkeypatch.setattr(
        chs.ConversationHistoryStore,
        "get_conversation_history",
        AsyncMock(return_value=[]),
    )

    import ai.inner_memory_index as ami

    monkeypatch.setattr(ami, "query", AsyncMock(return_value=[]))


    import core.first_contact_targets as fct

    monkeypatch.setattr(fct, "unfulfilled_targets_for_owner", lambda owner_id: [])


def _fake_stake(sid: int = 1) -> Stake:
    import time

    now = time.time()
    return Stake(
        id=sid,
        kind="hope",
        stake="I'm hoping the survey comes back clean.",
        status="open",
        resolution="",
        horizon_at=None,
        source_channel_id="",
        last_surfaced_at=None,
        created_at=now - 86400,
        updated_at=now,
        resolved_at=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("look_in_charge", [False, True])
async def test_flag_on_recall_injects_stakes_into_context_and_turn_read(monkeypatch, look_in_charge):
    """Flag ON: build_turn_context must (a) query the ledger, (b) thread the
    rendered block into take_turn_read's held_stakes kwarg, and (c) surface it
    on TurnContext.held_stakes_text — the constructor hop the caring-memory
    bug silently dropped for a month.

    With her first look in charge (R14, T5: the runner's switch around
    recall), recall takes no read, so (b) does not happen — the look reads
    her stakes in its own slice (core.agent.first_look.gather_slice) — and
    (a) and (c) still do: the reply's stakes band is recall's either way."""
    _stub_recall_dependencies(monkeypatch)

    import ai.stake_ledger as ledger

    monkeypatch.setattr(ledger, "ledger_enabled", lambda: True)
    monkeypatch.setattr(ledger, "open_stakes", AsyncMock(return_value=[_fake_stake()]))

    import ai.turn_read as turn_read_mod

    captured: Dict[str, Any] = {}

    async def fake_take_turn_read(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(turn_read_mod, "take_turn_read", fake_take_turn_read)

    recall = _load_recall()
    token = turn_read_mod.first_look_in_charge.set(True) if look_in_charge else None
    try:
        ctx = await recall.build_turn_context(_build_turn("hey"))
    finally:
        if token is not None:
            turn_read_mod.first_look_in_charge.reset(token)

    assert "I'm hoping the survey comes back clean." in ctx.held_stakes_text
    if look_in_charge:
        assert captured == {}                            # no read was taken
    else:
        assert "I'm hoping the survey comes back clean." in captured.get("held_stakes", "")


@pytest.mark.asyncio
async def test_flag_off_recall_never_touches_ledger(monkeypatch):
    """Flag OFF (the root autouse fixture): the ledger is never queried and
    the context field stays empty — executed through the real recall, not a
    re-stated gate expression."""
    _stub_recall_dependencies(monkeypatch)

    import ai.stake_ledger as ledger

    spy = AsyncMock(return_value=[_fake_stake()])
    monkeypatch.setattr(ledger, "open_stakes", spy)
    # NOTE: ledger_enabled stays False via the root conftest autouse fixture.

    import ai.turn_read as turn_read_mod

    monkeypatch.setattr(turn_read_mod, "take_turn_read", AsyncMock(return_value=None))

    recall = _load_recall()
    ctx = await recall.build_turn_context(_build_turn("hey"))

    assert ctx.held_stakes_text == ""
    spy.assert_not_awaited()


class _CapturingLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_real_take_turn_read_renders_stakes_and_stays_parseable(monkeypatch):
    """Calls the REAL take_turn_read: pins template<->kwarg lockstep (a drift
    raises KeyError -> swallowed -> read silently dead forever), the 1500-char
    clamp, and the unchanged output contract."""
    fake = _CapturingLLM('{"read": "he is buzzing about the survey", "weight": 0.4,'
                         ' "needs_support": 0.0, "carries_forward": "", "memory_worthy": ""}')
    import ai.llm_client as llm_client

    monkeypatch.setattr(llm_client, "llm", fake)
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: "fake::analysis")

    from ai.turn_read import take_turn_read

    # >1500 chars with a distinctive marker past the clamp boundary.
    long_stakes = "- (hope, held 2d) I'm hoping. " + ("x" * 2000) + " TAIL_MARKER"
    result = await take_turn_read(
        message="survey is thursday!!",
        recent_conversation="user: hi",
        relationship_posture="warm",
        emotional_arc="(no recent arc)",
        held_stakes=long_stakes,
    )
    assert result is not None and "buzzing" in result.read

    prompt = fake.calls[0]["messages"][0]["content"]
    assert "WHAT'S ON MY OWN MIND" in prompt
    assert "I'm hoping." in prompt
    # Clamp: content past 1500 chars never reaches the prompt.
    assert "TAIL_MARKER" not in prompt


@pytest.mark.asyncio
async def test_real_take_turn_read_flag_off_prompt_has_no_stakes_section(monkeypatch):
    """Flag-off purity at the FINAL rendered-prompt layer: empty held_stakes
    must leave zero trace of the feature in what the LLM sees."""
    fake = _CapturingLLM('{"read": "ordinary", "weight": 0.1, "needs_support": 0.0,'
                         ' "carries_forward": "", "memory_worthy": ""}')
    import ai.llm_client as llm_client

    monkeypatch.setattr(llm_client, "llm", fake)
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: "fake::analysis")

    from ai.turn_read import take_turn_read

    result = await take_turn_read(
        message="hey",
        recent_conversation="user: hi",
        relationship_posture="warm",
        emotional_arc="(no recent arc)",
        held_stakes="",
    )
    assert result is not None
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "HOLDING" not in prompt
    assert "stake" not in prompt.lower()