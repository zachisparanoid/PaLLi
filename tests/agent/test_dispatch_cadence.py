"""D.3 wave 2 step 4: dispatch.py captures runner chunks into a
CadencedReply for the surface.

Pre-Wave-2 the react branch passed ``on_chunk=None`` to the runner —
CadencedBeatChunker's beat emissions streamed to nothing and the surface
received ``TurnResult.cadenced_reply=None``, forcing a fallback to a
single send of response_text. Wave 2 step 4 captures the chunks and
reassembles them into a CadencedReply with the same shape pipeline mode
produces.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from core.turn.types import Turn, TurnMetadata


def _make_turn(content: str = "hi") -> Turn:
    return Turn(
        surface="discord_text",
        owner_id=42,
        content=content,
        metadata=TurnMetadata(channel_id=12345),
        received_at=datetime(2026, 5, 13, 14, 30, 0),
    )


@pytest.mark.asyncio
async def test_dispatch_react_returns_cadenced_reply_not_none(monkeypatch):
    """Even for a one-shot single-beat reply with no streaming chunks,
    cadenced_reply must be non-None — a single Beat carrying response_text.
    Surface adapters (Discord) check `len(beats) > 1` before fan-out, so
    single-beat replies render identically to legacy path."""
    captured_chunks_received_by_runner = []

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        # Record what dispatch passed in — must NOT be None now.
        captured_chunks_received_by_runner.append(on_chunk)
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="hi back", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)

    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_make_turn(content="hi"))

    # Dispatch must have passed a real on_chunk callable, not None.
    assert len(captured_chunks_received_by_runner) == 1
    assert captured_chunks_received_by_runner[0] is not None, (
        "dispatch must pass an on_chunk callback to capture beats; "
        "passing None loses the cadence stream"
    )
    # cadenced_reply must be non-None and carry the response_text.
    assert result.cadenced_reply is not None
    assert result.cadenced_reply.total_text == "hi back"
    # Single chunk → single beat.
    assert len(result.cadenced_reply.beats) == 1


@pytest.mark.asyncio
async def test_dispatch_react_emits_multibeat_cadenced_reply_when_chunks_arrive(monkeypatch):
    """When the runner's chunker emits multiple chunks via on_chunk,
    each chunk becomes a Beat in the assembled CadencedReply. Surfaces
    that fan-out (Discord at bots/discord/client.py:245) then send each
    beat as a separate message with natural pauses."""

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        # Simulate the chunker emitting three beat-shaped chunks.
        if on_chunk is not None:
            await on_chunk("first beat ")
            await on_chunk("second beat ")
            await on_chunk("third beat")
        from core.agent.runner import RunResult
        return RunResult(
            responded=True,
            response_text="first beat second beat third beat",
            iterations=1,
        )

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)

    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_make_turn(content="long question"))

    assert result.cadenced_reply is not None
    assert len(result.cadenced_reply.beats) == 3
    assert result.cadenced_reply.beats[0].text == "first beat"
    assert result.cadenced_reply.beats[1].text == "second beat"
    assert result.cadenced_reply.beats[2].text == "third beat"
    # total_text from the runner is preserved verbatim — surfaces that
    # don't fan out (or store conversation history) read this.
    assert result.cadenced_reply.total_text == "first beat second beat third beat"


@pytest.mark.asyncio
async def test_dispatch_react_cadenced_reply_carries_attachments(monkeypatch):
    """The CadencedReply.attachments slot mirrors RunResult.attachments
    so surfaces that fan out beats can attach files to the right beat
    without losing them. Wave 1 added the schema; this confirms the wire."""

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        if on_chunk is not None:
            await on_chunk("done")
        from core.agent.runner import RunResult
        return RunResult(
            responded=True,
            response_text="done",
            iterations=1,
            attachments=("/tmp/snap.png",),
        )

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)

    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_make_turn(content="snap"))

    assert result.cadenced_reply is not None
    assert result.cadenced_reply.attachments == ("/tmp/snap.png",)
    # And the redundant top-level slot still mirrors.
    assert result.attachments == ("/tmp/snap.png",)


@pytest.mark.asyncio
async def test_dispatch_react_no_response_means_no_cadenced_reply(monkeypatch):
    """When the runner returns responded=False, cadenced_reply is None.
    Surfaces don't render anything in this case."""

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult
        return RunResult(responded=False, response_text="", iterations=0)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt): return ""
    async def stub_build_callable(): return stub_cadence_llm
    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    async def stub_client(): return object()
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)

    async def noop_user(turn, content): pass
    async def noop_asst(*, turn, response_text): pass
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": None}
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)

    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    result = await dispatch_turn(_make_turn(content="ignored"))

    assert result.responded is False
    assert result.cadenced_reply is None


def test_build_cadenced_reply_from_chunks_handles_empty_chunks():
    """When the runner returned response_text but emitted no chunks
    (non-streaming provider), the helper falls back to a single beat
    containing response_text — same as CadencedReply.from_single."""
    from core.agent.dispatch import _build_cadenced_reply_from_chunks

    cr = _build_cadenced_reply_from_chunks(
        chunks=[],
        response_text="hello",
        attachments=(),
    )
    assert cr.total_text == "hello"
    assert len(cr.beats) == 1
    assert cr.beats[0].text == "hello"
    assert cr.attachments == ()


def test_build_cadenced_reply_from_chunks_strips_whitespace_chunks():
    """Empty / whitespace-only chunks (chunker emitting trailing spaces)
    are filtered out so they don't become empty beats."""
    from core.agent.dispatch import _build_cadenced_reply_from_chunks

    cr = _build_cadenced_reply_from_chunks(
        chunks=["", "  ", "a", "  ", "b"],
        response_text="a\n\nb",
        attachments=(),
    )
    assert len(cr.beats) == 2
    assert cr.beats[0].text == "a"
    assert cr.beats[1].text == "b"


def test_build_cadenced_reply_from_chunks_carries_attachments():
    """Helper signature requires attachments; they should land on the
    CadencedReply slot for downstream surface adapters."""
    from core.agent.dispatch import _build_cadenced_reply_from_chunks

    cr = _build_cadenced_reply_from_chunks(
        chunks=["text"],
        response_text="text",
        attachments=("/tmp/a.png", "/tmp/b.txt"),
    )
    assert cr.attachments == ("/tmp/a.png", "/tmp/b.txt")
