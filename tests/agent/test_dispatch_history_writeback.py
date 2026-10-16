"""D.3 wave 2 step 1: ReAct dispatch persists user + assistant rows.

Pre-Wave-2 the ReAct branch of dispatch.py skipped conversation-history
persistence — turns vanished from ``conversation_history`` and subsequent
ReAct turns couldn't see their own prior work. The shared helpers in
``core/turn/persistence`` are now called from both pipeline (turn.py) and
react (dispatch.py) paths.

These tests pin the contract that store_conversation gets called with
the right user content + assistant response_text on the react path.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.turn.types import Turn, TurnMetadata


def _make_turn(content: str = "hey") -> Turn:
    return Turn(
        surface="discord_text",
        owner_id=42,
        content=content,
        metadata=TurnMetadata(channel_id=12345, discord_username="sam"),
        received_at=datetime(2026, 5, 13, 14, 30, 0),
    )


@pytest.mark.asyncio
async def test_dispatch_react_writes_user_and_assistant_rows(monkeypatch):
    """When dispatch_turn routes through the react loop and the runner
    returns a responded RunResult, both _persist_user_side_with_content
    and _persist_assistant_side_to_history must fire."""
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult
        return RunResult(
            responded=True,
            response_text="hi back",
            iterations=1,
        )

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""

    async def stub_build_callable():
        return stub_cadence_llm

    monkeypatch.setattr(
        "core.agent.dispatch._build_cadence_llm_callable", stub_build_callable,
    )

    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    user_calls = []
    asst_calls = []

    async def fake_user_persist(turn, content):
        user_calls.append((turn, content))

    async def fake_asst_persist(*, turn, response_text):
        asst_calls.append((turn, response_text))

    monkeypatch.setattr(
        "core.turn.persistence._persist_user_side_with_content",
        fake_user_persist,
    )
    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history",
        fake_asst_persist,
    )

    from core.agent.dispatch import dispatch_turn

    turn = _make_turn(content="hey what's up?")
    result = await dispatch_turn(turn)
    # User-side persist is now fire-and-forget, scheduled BEFORE run_turn so a
    # barge-in/typing cancel can't drop the message (Bug 1 fix, 2026-07-04).
    # Flush the loop once so the background write runs before we assert.
    await asyncio.sleep(0)

    # Both persistence helpers must have been called.
    assert len(user_calls) == 1, (
        f"user-side persist should fire once; got {len(user_calls)}"
    )
    assert user_calls[0][1] == "hey what's up?"
    assert len(asst_calls) == 1, (
        f"assistant-side persist should fire once; got {len(asst_calls)}"
    )
    assert asst_calls[0][1] == "hi back"

    assert result.responded is True
    assert result.response_text == "hi back"


@pytest.mark.asyncio
async def test_dispatch_react_skips_assistant_persist_when_not_responded(monkeypatch):
    """When the runner returns responded=False, only the user-side row
    should be written — the assistant didn't actually reply, so no
    assistant row belongs in history."""
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult
        return RunResult(responded=False, response_text="", iterations=0)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""

    async def stub_build_callable():
        return stub_cadence_llm

    monkeypatch.setattr(
        "core.agent.dispatch._build_cadence_llm_callable", stub_build_callable,
    )

    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    user_calls = []
    asst_calls = []

    async def fake_user_persist(turn, content):
        user_calls.append((turn, content))

    async def fake_asst_persist(*, turn, response_text):
        asst_calls.append((turn, response_text))

    monkeypatch.setattr(
        "core.turn.persistence._persist_user_side_with_content",
        fake_user_persist,
    )
    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history",
        fake_asst_persist,
    )

    from core.agent.dispatch import dispatch_turn

    turn = _make_turn(content="hey")
    await dispatch_turn(turn)
    await asyncio.sleep(0)  # flush fire-and-forget user persist (Bug 1 fix)

    assert len(user_calls) == 1, "user-side write should still happen"
    assert len(asst_calls) == 0, (
        "assistant-side write must not happen when not responded"
    )


@pytest.mark.asyncio
async def test_dispatch_react_skips_user_persist_for_synthesized_origin(monkeypatch):
    """Monologue/reminder/proactive origins must not pollute user history.
    The pipeline path enforces the same gate at _schedule_user_side_persist."""
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="ok", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""

    async def stub_build_callable():
        return stub_cadence_llm

    monkeypatch.setattr(
        "core.agent.dispatch._build_cadence_llm_callable", stub_build_callable,
    )

    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    user_calls = []

    async def fake_user_persist(turn, content):
        user_calls.append((turn, content))

    async def fake_asst_persist(*, turn, response_text):
        pass

    monkeypatch.setattr(
        "core.turn.persistence._persist_user_side_with_content",
        fake_user_persist,
    )
    monkeypatch.setattr(
        "core.turn.persistence._persist_assistant_side_to_history",
        fake_asst_persist,
    )

    from core.agent.dispatch import dispatch_turn

    turn = Turn(
        surface="proactive",
        owner_id=42,
        content="[REMINDER FIRE] take meds",
        metadata=TurnMetadata(origin="reminder"),
        received_at=datetime(2026, 5, 13, 14, 30, 0),
    )
    await dispatch_turn(turn)

    assert len(user_calls) == 0, (
        "user-side persist must skip synthesized (non-user) origins"
    )


def test_persistence_module_exports_helpers():
    """Sanity import test: the new core/turn/persistence module exposes
    the two helpers the dispatch + turn modules rely on."""
    from core.turn import persistence
    assert hasattr(persistence, "_persist_user_side_with_content")
    assert hasattr(persistence, "_persist_assistant_side_to_history")


# (test_turn_module_imports_persistence_helpers retired 2026-07-26: it
# guarded that the PIPELINE's turn.py shared the persistence helpers with
# the ReAct path — core/turn/turn.py was excised with the pipeline, so the
# only implementation left IS the shared module asserted above.)
