"""Runner-level proof that the validity boundary holds end-to-end.

Seeds a POISONED on-disk session (the 2026-07-25 shape), runs a normal turn,
and asserts the payload handed to the LLM was repaired. Uses the REAL
core.agent.session module (not an isolated load): run_turn imports it at
module top, so seeded messages must be isinstance-compatible with it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


def _load_runner():
    from tests.agent.conftest import load_module
    return load_module("core/agent/runner.py", "agent_runner_under_test")


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "you there?"
    metadata: Any = None
    received_at: Any = None


@dataclass
class FakeCtx:
    turn: Any = None
    user_text: str = "you there?"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


CADENCE_JSON = ('{"should_respond": true, "register": "neutral", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "ok"}')


def test_poisoned_history_is_repaired_in_llm_payload(tmp_path, monkeypatch):
    """A session file ending on an orphaned tool_use (the poison pill) must
    reach the provider as a VALID payload with the placeholder adjacent."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from tests.agent.test_transcript_normalizer import assert_provider_valid

    mod = _load_runner()

    async def _seed() -> None:
        # FakeTurn(surface="discord_text", metadata=None) -> key "discord_text:1"
        s = await sess_mod.Session.open_or_create("discord_text:1", root=tmp_path)
        await s.append(sess_mod.AgentMessage(
            role="user", content=[sess_mod.TextBlock(text="scan the ports")]))
        await s.append(sess_mod.AgentMessage(
            role="assistant",
            content=[sess_mod.ToolUseBlock(
                id="call_00_OACeXg", name="local_shell",
                input={"command": "for port in 22 80; do nc -zv host $port; done"})],
        ))  # no tool_result follows — the poison
    asyncio.run(_seed())

    captured: dict[str, Any] = {}

    async def fake_recall(turn):
        return FakeCtx(turn=turn)

    async def fake_cadence_llm(prompt):
        return CADENCE_JSON

    async def fake_call(**kw):
        captured["messages"] = kw["messages"]
        return llm_mod.LLMResponse(text_blocks=["here!"], tool_uses=[],
                                   stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)
    deps = mod.RunnerDeps(
        build_turn_context=fake_recall, cadence_llm=fake_cadence_llm,
        sessions_root=tmp_path, provider="openai_compat", model="m",
        client=None, tool_registry=None, polling_tools=set(),
    )
    result = asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))

    assert result.responded is True
    payload = captured["messages"]
    assert_provider_valid(payload)
    # 2026-08-18: the transcript diet runs before the grammar law — an
    # earlier turn's tool round-trip (poisoned or not) leaves the payload
    # entirely, so the orphan never reaches the provider and there is nothing
    # left to repair. normalize_for_llm's placeholder repair still guards the
    # CURRENT run's round-trips (tests/agent/test_transcript_normalizer.py).
    assert not any(isinstance(b, sess_mod.ToolUseBlock)
                   for m in payload for b in m.content)
    assert [m.role for m in payload][-1] == "user"
    assert "scan the ports" in " ".join(
        b.text for m in payload for b in m.content if isinstance(b, sess_mod.TextBlock))


def test_barge_in_cancel_mid_dispatch_leaves_no_orphan_on_disk(tmp_path, monkeypatch):
    """B1, the incident window: cancel lands between the assistant append
    (runner.py:471) and the results append (:500). With the shield, the
    on-disk transcript closes the round-trip before the cancel propagates."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod

    mod = _load_runner()

    class SlowRegistry:
        def all_names(self):
            return ["local_shell"]

        def schemas_for(self, ctx):
            return [{"name": "local_shell", "description": "sh",
                     "input_schema": {"type": "object"}}]

        async def dispatch(self, tu, ctx):
            await asyncio.sleep(30)          # the slow port scan

    async def fake_recall(turn):
        return FakeCtx(turn=turn, user_text="scan the ports")

    async def fake_cadence_llm(prompt):
        return CADENCE_JSON

    async def fake_call(**kw):
        return llm_mod.LLMResponse(
            text_blocks=["Machine's alive. Let me check."],
            tool_uses=[sess_mod.ToolUseBlock(
                id="call_00_slow", name="local_shell",
                input={"command": "nc -zv host 22"})],
            stop_reason="tool_use", prompt_tokens=1, completion_tokens=1,
        )

    monkeypatch.setattr(llm_mod, "call", fake_call)
    deps = mod.RunnerDeps(
        build_turn_context=fake_recall, cadence_llm=fake_cadence_llm,
        sessions_root=tmp_path, provider="openai_compat", model="m",
        client=None, tool_registry=SlowRegistry(), polling_tools=set(),
    )

    async def _run() -> None:
        task = asyncio.get_running_loop().create_task(
            mod.run_turn(FakeTurn(content="scan the ports"), deps=deps, on_chunk=None))
        await asyncio.sleep(0.25)            # deep inside the 30s dispatch
        task.cancel()                        # the barge-in
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancel should propagate out of run_turn")
    asyncio.run(_run())

    reopened = asyncio.run(
        sess_mod.Session.open_or_create("discord_text:1", root=tmp_path))
    msgs = reopened.messages
    assert msgs[-2].role == "assistant"      # the in-flight call…
    assert msgs[-1].role == "tool"           # …closed by the shield
    block = msgs[-1].content[0]
    assert block.tool_use_id == "call_00_slow"
    assert block.is_error is True
    assert block.content == "[interrupted: a new message arrived before this command finished]"


# --- Step 27 Phase B (task 10): the §6 pin — one writer, under concurrency ---

def test_sidecar_completing_mid_round_trip_never_writes_the_parent_until_the_boundary(tmp_path, monkeypatch):
    """Step 27 §6: a parent run is held INSIDE an open tool round-trip; a
    sidecar (a full turn on the child key) runs to completion meanwhile; the
    parent transcript must stay untouched until the parent's own boundary
    drain writes the exchange — after the round-trip closed — and the
    result must pass the validity boundary's invariants."""
    from types import SimpleNamespace
    from unittest.mock import patch
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    from tests.agent.test_transcript_normalizer import assert_provider_valid
    mod = _load_runner()
    PARENT = "discord:77"
    steering.clear(PARENT); steering.clear_run_state(PARENT)

    async def fake_recall(turn):
        return FakeCtx(turn=turn, user_text=str(getattr(turn, "content", "") or ""))

    async def fake_cadence_llm(prompt):
        return CADENCE_JSON

    def _deps(registry):
        return mod.RunnerDeps(
            build_turn_context=fake_recall, cadence_llm=fake_cadence_llm,
            sessions_root=tmp_path, provider="openai_compat", model="m",
            client=None, tool_registry=registry, polling_tools=set())

    gate = asyncio.Event()

    class HeldRegistry:
        def all_names(self): return ["slow_tool"]
        def schemas_for(self, ctx):
            return [{"name": "slow_tool", "description": "slow", "input_schema": {"type": "object"}}]
        async def dispatch(self, tu, ctx):
            await gate.wait()
            return sess_mod.ToolResultBlock(tool_use_id=tu.id, content="finished", is_error=False)

    calls = {"n": 0}

    async def parent_call(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return llm_mod.LLMResponse(text_blocks=["on it"],
                tool_uses=[sess_mod.ToolUseBlock(id="t1", name="slow_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=["done"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    async def child_call(**kw):
        return llm_mod.LLMResponse(text_blocks=["on the fridge"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", parent_call)
    md = SimpleNamespace(channel_id=77, extra={}, user_side_prerecorded=False, origin="user")
    parent_turn = SimpleNamespace(surface="discord_text", owner_id=1, content="build it", metadata=md, received_at=None)

    async def scenario():
        with patch.object(dx, "duplex_enabled", lambda: True):
            parent = asyncio.create_task(mod.run_turn(parent_turn, deps=_deps(HeldRegistry()), on_chunk=None))
            for _ in range(200):                     # let the parent reach the held tool
                await asyncio.sleep(0)
                if steering.is_working(PARENT):
                    break
            assert steering.is_working(PARENT)
            parent_path = tmp_path / "discord_77.jsonl"
            mid_bytes = parent_path.read_bytes()

            # the sidecar: a plain full turn on the CHILD key (its own transcript)
            child_md = SimpleNamespace(channel_id=77, origin="user", user_side_prerecorded=False,
                                       extra={"session_key_override": PARENT + "#sidecar-1", "sidecar_of": PARENT,
                                              "sidecar_kind": "talk"})
            child_turn = SimpleNamespace(surface="discord_text", owner_id=1, content="what's the wifi again?",
                                         metadata=child_md, received_at=None)
            monkeypatch.setattr(llm_mod, "call", child_call)
            child_result = await mod.run_turn(child_turn, deps=_deps(None), on_chunk=None)
            assert child_result.response_text == "on the fridge"
            assert parent_path.read_bytes() == mid_bytes            # the parent was NOT written by the sidecar
            # hand the exchange to the parent as a replied bundle (what on_done does)
            steering.enqueue(PARENT, steering.SteerBundle(
                turn=child_turn, enqueued_at=0.0, replied=True, reply_text=child_result.response_text))
            monkeypatch.setattr(llm_mod, "call", parent_call)
            gate.set()
            await parent
        return parent_path

    parent_path = asyncio.run(scenario())
    session = asyncio.run(sess_mod.Session.open_or_create(PARENT, root=tmp_path))
    assert_provider_valid(session.messages)
    roles = [m.role for m in session.messages]
    assert roles.index("user", 1) > roles.index("tool")             # the exchange landed AFTER the round-trip closed
    texts = [b.text for m in session.messages for b in m.content if isinstance(b, sess_mod.TextBlock)]
    assert "what's the wifi again?" in texts and "on the fridge" in texts
    child = asyncio.run(sess_mod.Session.open_or_create(PARENT + "#sidecar-1", root=tmp_path))
    assert_provider_valid(child.messages)
    assert [m.role for m in child.messages] == ["user", "assistant"]
