"""Bug 5 regression (2026-07-04): ReAct must persist what was IN an image.

Pre-fix, the ReAct path persisted only the "[image attached]" placeholder for
image turns — the vision analysis (ctx.image_descriptions, built inside run_turn)
was used for the reply but never saved. So PaLLi described an image in one turn
and forgot its contents on the next. The fix surfaces the analysis on RunResult
and bakes it into the persisted user row (mirroring the pipeline engine).
"""
import asyncio
from datetime import datetime

import pytest


def _common_stubs(monkeypatch, persisted):
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


@pytest.mark.asyncio
async def test_image_turn_persists_vision_analysis_not_just_placeholder(monkeypatch):
    persisted: list[str] = []
    analysis = "A red barn in a green field with a blue tractor"

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult
        return RunResult(
            responded=True,
            response_text="nice barn!",
            iterations=1,
            image_descriptions=(analysis,),
        )

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)
    _common_stubs(monkeypatch, persisted)

    from core.agent.dispatch import dispatch_turn
    from core.turn.types import ImageRef, Turn, TurnMetadata

    turn = Turn(
        surface="discord_text",
        owner_id=42,
        content="[image attached]",
        images=(ImageRef(url="http://x/img.png", local_path=None, mime="image/png"),),
        metadata=TurnMetadata(channel_id=12345),
        received_at=datetime(2026, 7, 4, 3, 52, 0),
    )
    await dispatch_turn(turn)
    await asyncio.sleep(0)

    assert len(persisted) == 1, f"expected exactly one persist, got {persisted}"
    assert analysis in persisted[0], (
        "the vision analysis was NOT persisted — history kept only the "
        "'[image attached]' placeholder (Bug 5)"
    )
    assert persisted[0] != "[image attached]"


@pytest.mark.asyncio
async def test_text_turn_persists_plain_content(monkeypatch):
    """Non-attachment turns are unaffected — they persist their plain text
    (via the early fire-and-forget path) with no vision expansion."""
    persisted: list[str] = []

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        from core.agent.runner import RunResult
        return RunResult(responded=True, response_text="hi", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)
    _common_stubs(monkeypatch, persisted)

    from core.agent.dispatch import dispatch_turn
    from core.turn.types import Turn, TurnMetadata

    turn = Turn(
        surface="discord_text",
        owner_id=42,
        content="just a normal message",
        metadata=TurnMetadata(channel_id=12345),
        received_at=datetime(2026, 7, 4, 3, 52, 0),
    )
    await dispatch_turn(turn)
    await asyncio.sleep(0)

    assert persisted == ["just a normal message"]
