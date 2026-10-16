"""Step 27 Phase B — a sidecar is a full turn on a CHILD session that sees a
read-only snapshot of the parent."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest


def _load_runner():
    from tests.agent.conftest import load_module
    return load_module("core/agent/runner.py", "agent_runner_sidecar_under_test")


def test_child_key_shape():
    from core.agent import sidecar
    assert sidecar.child_key("discord:123", 1) == "discord:123#sidecar-1"


def test_session_key_override_rides_metadata_extra():
    mod = _load_runner()
    md = SimpleNamespace(channel_id=123, extra={"session_key_override": "discord:123#sidecar-1"})
    turn = SimpleNamespace(surface="discord_text", owner_id=1, metadata=md)
    assert mod._derive_session_key(turn) == "discord:123#sidecar-1"
    md2 = SimpleNamespace(channel_id=123, extra={})
    assert mod._derive_session_key(SimpleNamespace(surface="discord_text", owner_id=1, metadata=md2)) == "discord:123"


def test_parent_snapshot_reads_the_parent_transcript_read_only(tmp_path):
    import core.agent.session as sess_mod
    import core.agent.steering as st
    from core.agent import sidecar

    async def seed():
        s = await sess_mod.Session.open_or_create("discord:5", root=tmp_path)
        await s.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="build the adapter")]))
        await s.append(sess_mod.AgentMessage(role="assistant", content=[
            sess_mod.TextBlock(text="measuring the fan"),
            sess_mod.ToolUseBlock(id="t1", name="local_shell", input={})]))
    asyncio.run(seed())
    st.set_now_doing("discord:5", "running local_shell (iteration 1)")
    before = (tmp_path / "discord_5.jsonl").read_bytes()
    text = sidecar.parent_snapshot("discord:5", sessions_root=tmp_path)
    assert text.startswith("What you are in the middle of")
    assert "running local_shell" in text and "build the adapter" in text and "[tool: local_shell]" in text
    assert (tmp_path / "discord_5.jsonl").read_bytes() == before        # read-only
    st.clear_now_doing("discord:5")


def test_parent_snapshot_works_inside_a_running_loop(tmp_path):
    """Correction 10: the snapshot is read from the file directly — it must
    work from inside a coroutine (dispatch calls it there)."""
    import core.agent.session as sess_mod
    from core.agent import sidecar

    async def go():
        s = await sess_mod.Session.open_or_create("discord:6", root=tmp_path)
        await s.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="hello there")]))
        return sidecar.parent_snapshot("discord:6", sessions_root=tmp_path)
    assert "hello there" in asyncio.run(go())


def test_wrap_context_builder_appends_snapshot_to_runtime_text(tmp_path):
    import core.agent.session as sess_mod
    from core.agent import sidecar

    @dataclass
    class Ctx:
        runtime_text: str = "the runtime"
        user_text: str = "hey"

    async def seed():
        s = await sess_mod.Session.open_or_create("discord:5", root=tmp_path)
        await s.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="build the adapter")]))
    asyncio.run(seed())

    async def build(turn):
        return Ctx()
    wrapped = sidecar.wrap_context_builder(build, parent_key="discord:5", sessions_root=tmp_path)
    ctx = asyncio.run(wrapped(SimpleNamespace()))
    assert ctx.runtime_text.startswith("the runtime") and "build the adapter" in ctx.runtime_text
    assert ctx.user_text == "hey"


def test_snapshot_says_to_name_the_task_when_two_are_alive(tmp_path):
    """Spec §4: results name their task when two are alive — her wording,
    prompted, never templated. The snapshot carries the prompt."""
    import core.agent.session as sess_mod
    from core.agent import sidecar, task_slots as ts
    ts.reset_for_tests()

    async def seed():
        s = await sess_mod.Session.open_or_create("discord:9", root=tmp_path)
        await s.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="build the adapter")]))
    asyncio.run(seed())
    assert "name which one" not in sidecar.parent_snapshot("discord:9", sessions_root=tmp_path)
    ts.acquire("discord:9", "the run"); ts.acquire("discord:9", "sidecar-1")
    text = sidecar.parent_snapshot("discord:9", sessions_root=tmp_path)
    assert "two things are running" in text and "name which one" in text
    ts.reset_for_tests()


def test_wrap_context_builder_survives_the_real_frozen_turn_context(tmp_path):
    """2026-09-09 10:50, her first live task sidecar: TurnContext is a frozen
    slots dataclass with no runtime_text field, so replace() raised and the
    setattr fallback raised too — the sidecar's turn died before its prompt
    was built and his message went unanswered. The snapshot must ride a real
    field, and the adapter must fold it into runtime_text."""
    from datetime import datetime
    import core.agent.session as sess_mod
    from core.agent import sidecar
    from core.agent.context_adapter import build_agent_loop_context
    from core.turn.types import Turn, TurnContext, TurnMetadata

    async def seed():
        s = await sess_mod.Session.open_or_create("discord:7", root=tmp_path)
        await s.append(sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="rebuild the index")]))
    asyncio.run(seed())

    turn = Turn(surface="discord_text", owner_id=1, content="also, can you enable ssh on the switch?",
                metadata=TurnMetadata(), received_at=datetime(2026, 9, 9, 10, 50))

    async def build(_turn):
        return TurnContext(turn=turn)

    wrapped = sidecar.wrap_context_builder(build, parent_key="discord:7", sessions_root=tmp_path)
    ctx = asyncio.run(wrapped(turn))
    assert isinstance(ctx, TurnContext)
    assert "rebuild the index" in ctx.sidecar_snapshot_text
    out = build_agent_loop_context(turn, ctx)
    assert "rebuild the index" in out.runtime_text
