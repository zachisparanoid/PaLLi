"""Stake-appraisal dispatch hook (2026-07-06): fires post-reply, fire-and-forget,
for genuine user exchanges only; never delays, breaks, or is required by the turn.
"""
import asyncio
from datetime import datetime

import pytest


def _wire_dispatch_stubs(monkeypatch, run_turn_impl, appraise_calls: list, *, flag_on=True):
    monkeypatch.setattr("core.agent.runner.run_turn", run_turn_impl)

    async def stub_cadence_llm(prompt):
        return ""

    async def stub_build_callable():
        return stub_cadence_llm

    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)

    async def stub_client():
        return object()

    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content):
        pass

    async def noop_asst(*, turn, response_text):
        pass

    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    # The hook gates on ledger_enabled() (imported at call time from
    # ai.stake_ledger) and then imports appraise_exchange from
    # ai.stake_appraiser — patch both at their source modules.
    import ai.stake_ledger as ledger
    import ai.stake_appraiser as appraiser

    monkeypatch.setattr(ledger, "ledger_enabled", lambda: flag_on)

    async def record_appraise(**kwargs):
        appraise_calls.append(kwargs)
        return {"applied": 0}

    monkeypatch.setattr(appraiser, "appraise_exchange", record_appraise)


def _turn(content="hey", origin="user"):
    from core.turn.types import Turn, TurnMetadata

    return Turn(
        surface="discord_text",
        owner_id=42,
        content=content,
        metadata=TurnMetadata(channel_id=777, origin=origin),
        received_at=datetime(2026, 7, 6, 3, 0, 0),
    )


async def _ok_run_turn(turn, *, deps, on_chunk=None):
    from core.agent.runner import RunResult

    return RunResult(responded=True, response_text="warm reply", iterations=1)


async def _drain_background():
    # Let fire-and-forget tasks run to completion.
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_appraiser_fires_for_user_exchange(monkeypatch):
    calls: list = []
    _wire_dispatch_stubs(monkeypatch, _ok_run_turn, calls)
    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_turn("survey came back clean!"))
    assert result.responded
    await _drain_background()

    assert len(calls) == 1
    assert calls[0]["user_message"] == "survey came back clean!"
    assert calls[0]["palli_reply"] == "warm reply"
    assert calls[0]["channel_id"] == "777"


@pytest.mark.asyncio
async def test_appraiser_skipped_for_synthesized_origins(monkeypatch):
    """Reminder / monologue / proactive_action turns never appraise —
    PaLLi doesn't form stakes from talking to itself."""
    calls: list = []
    _wire_dispatch_stubs(monkeypatch, _ok_run_turn, calls)
    from core.agent.dispatch import dispatch_turn

    for origin in ("reminder", "monologue", "proactive_action"):
        await dispatch_turn(_turn(f"synthesized {origin} turn", origin=origin))
    await _drain_background()

    assert calls == []


@pytest.mark.asyncio
async def test_appraiser_skipped_when_flag_off(monkeypatch):
    calls: list = []
    _wire_dispatch_stubs(monkeypatch, _ok_run_turn, calls, flag_on=False)
    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(_turn())
    await _drain_background()

    assert calls == []


@pytest.mark.asyncio
async def test_appraiser_skipped_when_not_responded(monkeypatch):
    calls: list = []

    async def silent_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult

        return RunResult(responded=False, response_text="", iterations=1)

    _wire_dispatch_stubs(monkeypatch, silent_run_turn, calls)
    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(_turn())
    await _drain_background()

    assert calls == []


@pytest.mark.asyncio
async def test_appraiser_failure_cannot_break_the_turn(monkeypatch, caplog):
    """An exploding appraisal task must not affect the TurnResult — the reply
    already happened. The retention set's done-callback path also must not
    leak the exception into the loop."""
    calls: list = []
    _wire_dispatch_stubs(monkeypatch, _ok_run_turn, calls)

    import ai.stake_appraiser as appraiser

    async def exploding_appraise(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("appraiser blew up")

    monkeypatch.setattr(appraiser, "appraise_exchange", exploding_appraise)
    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_turn())
    assert result.responded and result.response_text == "warm reply"
    await _drain_background()
    assert len(calls) == 1  # it ran and died in the background, turn unaffected


@pytest.mark.asyncio
async def test_appraisal_task_is_detached_and_survives_scheduling(monkeypatch):
    """The task must be strongly referenced in _BACKGROUND_PERSIST_TASKS while
    pending (the Bug-1 GC lesson) and discard itself when done."""
    calls: list = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_appraise(**kwargs):
        started.set()
        await release.wait()
        calls.append(kwargs)
        return {"applied": 0}

    _wire_dispatch_stubs(monkeypatch, _ok_run_turn, calls)
    import ai.stake_appraiser as appraiser

    monkeypatch.setattr(appraiser, "appraise_exchange", slow_appraise)

    import core.agent.dispatch as dispatch_mod

    before = set(dispatch_mod._BACKGROUND_PERSIST_TASKS)
    await dispatch_mod.dispatch_turn(_turn())
    await asyncio.wait_for(started.wait(), timeout=2)
    pending = dispatch_mod._BACKGROUND_PERSIST_TASKS - before
    assert len(pending) == 1  # strongly held while running

    release.set()
    await _drain_background()
    assert calls  # completed
    assert not (dispatch_mod._BACKGROUND_PERSIST_TASKS - before)  # self-discarded


@pytest.mark.asyncio
async def test_appraiser_receives_vision_analysis_for_image_turns(monkeypatch):
    """Review finding: image turns must appraise against the vision analysis,
    not the '[image attached]' placeholder — same expansion the history row
    gets (Bug 5)."""
    calls: list = []

    async def image_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult

        return RunResult(
            responded=True,
            response_text="she's a beauty",
            iterations=1,
            image_descriptions=("a lathe bed with visible surface rust",),
        )

    _wire_dispatch_stubs(monkeypatch, image_run_turn, calls)
    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(_turn("[image attached]"))
    await _drain_background()

    assert len(calls) == 1
    assert "surface rust" in calls[0]["user_message"]
