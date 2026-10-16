"""Bug 1 regression (2026-07-04): a cancelled turn must NOT lose the user's message.

Barge-in / typing-debounce in bots/discord/client.py cancels the in-flight task
(which is awaiting dispatch_turn -> run_turn). Pre-fix, dispatch.py persisted the
user-side row only AFTER run_turn, so the CancelledError skipped it and the user's
words vanished from conversation_history — PaLLi never received the message.

The fix persists the user's message BEFORE the cancellable run_turn (fire-and-forget,
mirroring the pipeline engine), so a cancelled reply can never delete their words.
"""
import asyncio
from datetime import datetime

import pytest


@pytest.mark.asyncio
async def test_user_message_persisted_even_when_run_turn_cancelled(monkeypatch):
    persisted: list[str] = []

    async def cancelled_run_turn(turn, *, deps, on_chunk=None):
        # Simulate the barge-in cancel landing inside the ReAct loop.
        raise asyncio.CancelledError()

    monkeypatch.setattr("core.agent.runner.run_turn", cancelled_run_turn)

    async def stub_cadence_llm(prompt):
        return ""

    async def stub_build_callable():
        return stub_cadence_llm

    monkeypatch.setattr(
        "core.agent.dispatch._build_cadence_llm_callable", stub_build_callable
    )

    async def stub_client():
        return object()

    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def record_user(turn, content):
        persisted.append(content)

    async def noop_asst(*, turn, response_text):
        pass

    monkeypatch.setattr(
        "core.turn.persistence._persist_user_side_with_content", record_user
    )
    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", noop_asst
    )

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn
    from core.turn.types import Turn, TurnMetadata

    question = "Is it possible that past drinking damaged my memory?"
    turn = Turn(
        surface="discord_text",
        owner_id=42,
        content=question,
        metadata=TurnMetadata(channel_id=12345),
        received_at=datetime(2026, 7, 4, 3, 52, 0),
    )

    with pytest.raises(asyncio.CancelledError):
        await dispatch_turn(turn)

    # Let the fire-and-forget persist task run.
    await asyncio.sleep(0)

    assert persisted == [question], (
        "the user's message was NOT persisted before the cancellable run_turn — "
        "a cancelled reply silently dropped it (Bug 1)"
    )


@pytest.mark.asyncio
async def test_no_double_persist_on_normal_text_turn(monkeypatch):
    """On a normal (uncancelled) text turn, the user row is written exactly once —
    the early persist fires and the post-run persist must not duplicate it."""
    persisted: list[str] = []

    async def ok_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="ok", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", ok_run_turn)

    async def stub_cadence_llm(prompt):
        return ""

    async def stub_build_callable():
        return stub_cadence_llm

    monkeypatch.setattr(
        "core.agent.dispatch._build_cadence_llm_callable", stub_build_callable
    )

    async def stub_client():
        return object()

    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def record_user(turn, content):
        persisted.append(content)

    async def noop_asst(*, turn, response_text):
        pass

    monkeypatch.setattr(
        "core.turn.persistence._persist_user_side_with_content", record_user
    )
    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history", noop_asst
    )

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn
    from core.turn.types import Turn, TurnMetadata

    turn = Turn(
        surface="discord_text",
        owner_id=42,
        content="hey there",
        metadata=TurnMetadata(channel_id=12345),
        received_at=datetime(2026, 7, 4, 3, 52, 0),
    )
    await dispatch_turn(turn)
    await asyncio.sleep(0)

    assert persisted == ["hey there"], f"expected exactly one persist, got {persisted}"
