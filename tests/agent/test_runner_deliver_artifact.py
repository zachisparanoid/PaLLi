"""Week 2, task 2: a delivery is a fact the deliver tool hands over.

The runner used to SCAN every tool result for a ``deliver:<path>`` line and
attach whatever followed it. Any tool that happened to print that phrase — a
file listing, an error message, the model quoting itself — could put a file
on the wire. Now ``deliver_artifact`` appends the resolved path to
``ctx.deliveries`` (one ToolContext per turn, built at runner.py:487) and the
runner reads that list after each tool round. The scan is deleted, so the
phrase means nothing. bots/discord/client.py already reads
result.attachments end-to-end.
"""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "give me the snap"
    metadata: Any = None
    received_at: Any = None


@dataclass
class FakeCtx:
    turn: FakeTurn = field(default_factory=FakeTurn)
    user_text: str = "give me the snap"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


@pytest.mark.asyncio
async def test_the_deliver_tools_hand_off_flows_into_attachments(tmp_path):
    """A tool that hands a path to ``ctx.deliveries`` gets it onto
    RunResult.attachments. The hand-off IS the delivery; what the tool says
    in its result text is only words.
    """
    import core.agent.runner as runner_mod
    import core.agent.llm_client as llm_mod_real
    from core.agent.session import ToolUseBlock
    from core.agent.tools import ToolRegistry

    # Stand-in for deliver_artifact: it hands the path over on the turn's
    # ToolContext. The runner doesn't care which tool did it — the list is
    # the hand-off.
    snap_path = str(tmp_path / "snap.png")
    Path(snap_path).write_bytes(b"\x89PNG\r\n\x1a\n")

    class StubDeliverTool:
        name = "snap_and_deliver"
        description = "Capture and deliver"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False

        async def execute(self, input, ctx):
            ctx.deliveries.append(snap_path)
            return f"queued for delivery: {snap_path}\nhere's the snap"

    registry = ToolRegistry()
    registry.register(StubDeliverTool())

    call_n = {"i": 0}

    async def fake_call(**kw):
        call_n["i"] += 1
        if call_n["i"] == 1:
            return llm_mod_real.LLMResponse(
                text_blocks=[],
                tool_uses=[ToolUseBlock(id="t1", name="snap_and_deliver", input={})],
                stop_reason="tool_use",
                prompt_tokens=1, completion_tokens=1,
            )
        return llm_mod_real.LLMResponse(
            text_blocks=["here you go"],
            tool_uses=[],
            stop_reason="end_turn",
            prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({
            "should_respond": True, "register": "neutral", "length": "short",
            "phase": "opening", "target_beats": 1, "observation_text": "ok",
        })

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = runner_mod.RunnerDeps(
            build_turn_context=fake_recall,
            cadence_llm=fake_cadence,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=registry,
            polling_tools=set(),
        )
        result = await runner_mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert result.attachments == (snap_path,), (
        f"the deliver tool's hand-off should have flowed into attachments; "
        f"got {result.attachments!r}"
    )


@pytest.mark.asyncio
async def test_several_hand_offs_in_one_turn_all_arrive(tmp_path):
    """Several hand-offs across several tool calls all accumulate. She can
    call deliver_artifact more than once in a turn (a screenshot AND a log
    file)."""
    import core.agent.runner as runner_mod
    import core.agent.llm_client as llm_mod_real
    from core.agent.session import ToolUseBlock
    from core.agent.tools import ToolRegistry

    p1 = str(tmp_path / "a.png")
    p2 = str(tmp_path / "b.txt")
    Path(p1).write_bytes(b"x")
    Path(p2).write_text("y")

    paths_to_serve = iter([p1, p2])

    class StubDeliverTool:
        name = "d"
        description = "deliver"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False

        async def execute(self, input, ctx):
            path = next(paths_to_serve)
            ctx.deliveries.append(path)
            return f"queued for delivery: {path}"

    registry = ToolRegistry()
    registry.register(StubDeliverTool())

    call_n = {"i": 0}

    async def fake_call(**kw):
        call_n["i"] += 1
        if call_n["i"] == 1:
            return llm_mod_real.LLMResponse(
                text_blocks=[],
                tool_uses=[
                    ToolUseBlock(id="t1", name="d", input={}),
                    ToolUseBlock(id="t2", name="d", input={}),
                ],
                stop_reason="tool_use",
                prompt_tokens=1, completion_tokens=1,
            )
        return llm_mod_real.LLMResponse(
            text_blocks=["done"], tool_uses=[],
            stop_reason="end_turn", prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({
            "should_respond": True, "register": "neutral", "length": "short",
            "phase": "opening", "target_beats": 1, "observation_text": "ok",
        })

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = runner_mod.RunnerDeps(
            build_turn_context=fake_recall,
            cadence_llm=fake_cadence,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=registry,
            polling_tools=set(),
        )
        result = await runner_mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert result.attachments == (p1, p2)


@pytest.mark.asyncio
async def test_a_deliver_phrase_in_tool_text_delivers_nothing(tmp_path):
    """A SUCCESSFUL tool whose result text says ``deliver:<a real file>``
    delivers nothing at all. There is no scan left to fool: a delivery is a
    fact the deliver tool hands over, never a phrase in tool output."""
    import core.agent.runner as runner_mod
    import core.agent.llm_client as llm_mod_real
    from core.agent.session import ToolUseBlock
    from core.agent.tools import ToolRegistry

    loot = tmp_path / "loot.txt"
    loot.write_text("whatever is on his disk", encoding="utf-8")

    class ChattyTool:
        name = "chatty"
        description = "says the word"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False

        async def execute(self, input, ctx):
            # A plain SUCCESS whose text carries the phrase on a line of its
            # own — a file listing, a command echoed back, a note read aloud.
            # The old scan attached whatever followed it.
            return f"$ cat note.txt\ndeliver:{loot}\n(that is all it says)"

    registry = ToolRegistry()
    registry.register(ChattyTool())

    call_n = {"i": 0}

    async def fake_call(**kw):
        call_n["i"] += 1
        if call_n["i"] == 1:
            return llm_mod_real.LLMResponse(
                text_blocks=[],
                tool_uses=[ToolUseBlock(id="t1", name="chatty", input={})],
                stop_reason="tool_use",
                prompt_tokens=1, completion_tokens=1,
            )
        return llm_mod_real.LLMResponse(
            text_blocks=["nothing to send"], tool_uses=[],
            stop_reason="end_turn", prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({
            "should_respond": True, "register": "neutral", "length": "short",
            "phase": "opening", "target_beats": 1, "observation_text": "ok",
        })

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = runner_mod.RunnerDeps(
            build_turn_context=fake_recall,
            cadence_llm=fake_cadence,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=registry,
            polling_tools=set(),
        )
        result = await runner_mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    # The text said the word; nobody handed anything over. Nothing goes.
    assert result.attachments == ()


@pytest.mark.asyncio
async def test_no_hand_off_means_empty_attachments(tmp_path):
    """When no tool hands anything over, the attachments tuple is empty."""
    import core.agent.runner as runner_mod
    import core.agent.llm_client as llm_mod_real
    from core.agent.session import ToolUseBlock
    from core.agent.tools import ToolRegistry

    class PlainTool:
        name = "echo"
        description = "echo"
        input_schema = {"type": "object", "properties": {}}
        requires_approval = False
        owner_only = False
        async def execute(self, input, ctx): return "just a string"

    registry = ToolRegistry()
    registry.register(PlainTool())

    call_n = {"i": 0}

    async def fake_call(**kw):
        call_n["i"] += 1
        if call_n["i"] == 1:
            return llm_mod_real.LLMResponse(
                text_blocks=[], tool_uses=[ToolUseBlock(id="t1", name="echo", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1,
            )
        return llm_mod_real.LLMResponse(
            text_blocks=["ok"], tool_uses=[],
            stop_reason="end_turn", prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({
            "should_respond": True, "register": "neutral", "length": "short",
            "phase": "opening", "target_beats": 1, "observation_text": "ok",
        })

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = runner_mod.RunnerDeps(
            build_turn_context=fake_recall,
            cadence_llm=fake_cadence,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=registry,
            polling_tools=set(),
        )
        result = await runner_mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert result.attachments == ()


@pytest.mark.asyncio
async def test_the_real_deliver_tool_hands_its_path_over(tmp_path, monkeypatch):
    """End-to-end with the canonical DeliverArtifactTool, no stub: the path
    lands on the turn's ToolContext and on RunResult.attachments."""
    # Premise updated 2026-09-08 (stranger-proofing week 1, task 2): the real
    # tool is scoped to allowed roots and tmp_path is outside them, so the
    # snapshot's home is declared a root the documented way.
    monkeypatch.setenv("FILE_TOOL_EXTRA_ROOTS", str(tmp_path))
    import core.agent.runner as runner_mod
    import core.agent.llm_client as llm_mod_real
    from core.agent.builtin_tools.deliver_artifact import DeliverArtifactTool
    from core.agent.session import ToolUseBlock
    from core.agent.tools import ToolRegistry

    snap = tmp_path / "snap.png"
    snap.write_bytes(b"\x89PNG")
    snap_path = str(snap.resolve())

    tool = DeliverArtifactTool()
    seen: dict = {}
    real_execute = tool.execute

    async def spy(input, ctx):
        # The runner builds ONE ToolContext per turn; hold on to it so the
        # hand-off itself can be asserted, not only its effect.
        seen["ctx"] = ctx
        return await real_execute(input, ctx)

    tool.execute = spy
    registry = ToolRegistry()
    registry.register(tool)

    call_n = {"i": 0}

    async def fake_call(**kw):
        call_n["i"] += 1
        if call_n["i"] == 1:
            return llm_mod_real.LLMResponse(
                text_blocks=[],
                tool_uses=[ToolUseBlock(id="t1", name="deliver_artifact",
                                          input={"path": snap_path,
                                                 "caption": "snap"})],
                stop_reason="tool_use",
                prompt_tokens=1, completion_tokens=1,
            )
        return llm_mod_real.LLMResponse(
            text_blocks=["delivered"], tool_uses=[],
            stop_reason="end_turn", prompt_tokens=1, completion_tokens=1,
        )

    async def fake_recall(turn): return FakeCtx()
    async def fake_cadence(prompt):
        import json
        return json.dumps({
            "should_respond": True, "register": "neutral", "length": "short",
            "phase": "opening", "target_beats": 1, "observation_text": "ok",
        })

    orig_call = llm_mod_real.call
    llm_mod_real.call = fake_call
    try:
        deps = runner_mod.RunnerDeps(
            build_turn_context=fake_recall,
            cadence_llm=fake_cadence,
            sessions_root=tmp_path,
            provider="openai_compat",
            model="m",
            client=None,
            tool_registry=registry,
            polling_tools=set(),
        )
        result = await runner_mod.run_turn(FakeTurn(), deps=deps, on_chunk=None)
    finally:
        llm_mod_real.call = orig_call

    assert result.responded is True
    assert seen["ctx"].deliveries == [snap_path]
    assert result.attachments == (snap_path,)
