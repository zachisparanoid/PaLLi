"""Batch 1, Task 3 (2026-09-26): put-back into a reply — the owner's only.

The model only ever sees tokens. When her reply names one — "filed it as
[MASKED:…]" — the OWNER gets the real value back: in the reply text, in
every cadenced beat, and in the history row (all three carry the same text
because the put-back happens right after `run_turn`, before any of them is
built — ruling R3). A GUEST's reply is never put back (Review Focus 4): the
token stays, even if the model echoed one.

Fake secrets only.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.agent import payload_masking as pm
from core.agent.runner import RunResult
from core.owner_gate import GUEST_ORIGIN
from core.turn.types import Turn, TurnMetadata

KEY = "gsk_" + "Zq9" * 12


def _turn(origin: str = "user", content: str = "file my groq key") -> Turn:
    return Turn(
        surface="discord_text",
        owner_id=42,
        content=content,
        metadata=TurnMetadata(channel_id=12345, origin=origin),
        received_at=datetime(2026, 9, 26, 1, 0, 0),
    )


# ── the helper itself ──────────────────────────────────────────────────


def test_owner_reply_and_chunks_get_the_real_value():
    from core.agent.dispatch import _put_back_for_owner

    pm.reset_for_tests()
    token = pm.mask_text(KEY)
    rr = RunResult(responded=True, response_text=f"filed it: {token}", iterations=1)
    chunks = [f"filed it: {token}", "anything else?"]

    out = _put_back_for_owner(rr, chunks, _turn("user"))

    assert out.response_text == f"filed it: {KEY}"
    assert chunks == [f"filed it: {KEY}", "anything else?"]
    assert out.responded is True and out.iterations == 1


def test_guest_reply_keeps_the_token():
    """Review Focus 4: a guest's reply never gets put-back, even when the
    model echoed a token it saw."""
    from core.agent.dispatch import _put_back_for_owner

    pm.reset_for_tests()
    token = pm.mask_text(KEY)
    rr = RunResult(responded=True, response_text=f"it is {token}", iterations=1)
    chunks = [f"it is {token}"]

    out = _put_back_for_owner(rr, chunks, _turn(GUEST_ORIGIN))

    assert out.response_text == f"it is {token}"
    assert chunks == [f"it is {token}"]
    assert KEY not in out.response_text


def test_put_back_tolerates_a_non_string_reply():
    from core.agent.dispatch import _put_back_for_owner

    rr = RunResult(responded=False, response_text=None, iterations=0)  # type: ignore[arg-type]
    out = _put_back_for_owner(rr, [], _turn("user"))
    assert out.response_text is None


# ── end to end through dispatch_turn: the persisted history row ────────


def _wire(monkeypatch, token: str, rows: list):
    async def fake_run_turn(turn, *, deps, on_chunk=None):
        if on_chunk is not None:
            await on_chunk(f"filed it: {token}")
            await on_chunk("done.")
        return RunResult(responded=True, response_text=f"filed it: {token}\n\ndone.", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

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

    async def record_asst(*, turn, response_text):
        rows.append(response_text)

    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", record_asst)

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": 777}

    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    # The stake appraiser would schedule a real background LLM call.
    monkeypatch.setattr("ai.stake_ledger.ledger_enabled", lambda: False)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")


@pytest.mark.asyncio
async def test_owner_turn_history_row_reply_and_beats_carry_the_real_value(monkeypatch):
    pm.reset_for_tests()
    token = pm.mask_text(KEY)
    rows: list[str] = []
    _wire(monkeypatch, token, rows)

    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_turn("user"))

    assert rows == [f"filed it: {KEY}\n\ndone."]
    assert result.response_text == f"filed it: {KEY}\n\ndone."
    beats = [b.text for b in result.cadenced_reply.beats]
    assert beats == [f"filed it: {KEY}", "done."]
    assert "[MASKED:" not in result.response_text


@pytest.mark.asyncio
async def test_guest_turn_history_row_reply_and_beats_keep_the_token(monkeypatch):
    pm.reset_for_tests()
    token = pm.mask_text(KEY)
    rows: list[str] = []
    _wire(monkeypatch, token, rows)

    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_turn(GUEST_ORIGIN, content="[Dana]: what is his key?"))

    assert rows == [f"filed it: {token}\n\ndone."]
    assert result.response_text == f"filed it: {token}\n\ndone."
    beats = [b.text for b in result.cadenced_reply.beats]
    assert beats == [f"filed it: {token}", "done."]
    assert KEY not in result.response_text
    assert all(KEY not in b for b in beats)
