"""Runner-side steering behavior (spec §4d, §4e, §4f).

Uses the REAL core.agent.session/steering modules (run_turn imports them at
module top), so objects must be isinstance-compatible with those.
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
    content: str = "check the logs"
    metadata: Any = None
    received_at: Any = None


@dataclass
class FakeCtx:
    turn: Any = None
    user_text: str = "check the logs"
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

SESSION_KEY = "discord_text:1"


class SlowRegistry:
    """A fake tool that blocks until released, so a test can act mid-run."""

    def __init__(self, gate: asyncio.Event):
        self.gate = gate

    def all_names(self):
        return ["slow_tool"]

    def schemas_for(self, ctx):
        return [{"name": "slow_tool", "description": "slow",
                 "input_schema": {"type": "object"}}]

    async def dispatch(self, tu, ctx):
        import core.agent.session as sess_mod
        await self.gate.wait()
        return sess_mod.ToolResultBlock(
            tool_use_id=tu.id, content="tool finished", is_error=False)


def _deps(mod, tmp_path, registry=None):
    async def fake_recall(turn):
        return FakeCtx(turn=turn)

    async def fake_cadence_llm(prompt):
        return CADENCE_JSON

    return mod.RunnerDeps(
        build_turn_context=fake_recall, cadence_llm=fake_cadence_llm,
        sessions_root=tmp_path, provider="openai_compat", model="m",
        client=None, tool_registry=registry, polling_tools=set(),
    )


def test_run_phases_published_and_cleared(tmp_path, monkeypatch):
    """Phase-owned routing (2026-08-04): a text-only run stamps "prep" at
    entry and "generating" at model INVOCATION (not completion — an arrival
    during the first model call is already Phase B, spec §4), and the finally
    clears phase + tool count no matter how the run ends."""
    import core.agent.llm_client as llm_mod
    import core.agent.steering as steering

    mod = _load_runner()
    steering.clear(SESSION_KEY)
    steering.clear_run_state(SESSION_KEY)
    observed = {"phases": [], "during_call": []}

    real_set = steering.set_run_phase

    def spy_set(key, phase):
        observed["phases"].append(phase)
        real_set(key, phase)

    monkeypatch.setattr(steering, "set_run_phase", spy_set)

    async def fake_call(**kw):
        observed["during_call"].append(steering.get_run_phase(SESSION_KEY))
        return llm_mod.LLMResponse(text_blocks=["hello"], tool_uses=[],
                                   stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)
    deps = _deps(mod, tmp_path)
    asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))

    assert observed["phases"][0] == "prep"
    assert "generating" in observed["phases"]
    assert observed["during_call"] == ["generating"]
    assert steering.get_run_phase(SESSION_KEY) == ""
    assert steering.get_tool_call_count(SESSION_KEY) == 0


def test_now_doing_is_set_during_tool_work_and_cleared_after(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering

    mod = _load_runner()
    steering.clear(SESSION_KEY)
    steering.clear_now_doing(SESSION_KEY)
    steering.clear_run_state(SESSION_KEY)
    gate = asyncio.Event()
    calls = {"n": 0}

    async def fake_call(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return llm_mod.LLMResponse(
                text_blocks=["working on it"],
                tool_uses=[sess_mod.ToolUseBlock(
                    id="t1", name="slow_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=["done"], tool_uses=[],
                                   stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)
    deps = _deps(mod, tmp_path, SlowRegistry(gate))
    seen = {}

    async def _run():
        task = asyncio.get_running_loop().create_task(
            mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
        await asyncio.sleep(0.15)
        seen["mid"] = steering.get_now_doing(SESSION_KEY)
        # Phase-owned routing: the dispatched tool call is already counted
        # while the run is inside it — is_working() gates the steer lane.
        seen["mid_tool_calls"] = steering.get_tool_call_count(SESSION_KEY)
        gate.set()
        await task

    asyncio.run(_run())
    assert "slow_tool" in seen["mid"]
    assert seen["mid_tool_calls"] == 1
    assert steering.get_now_doing(SESSION_KEY) == ""
    assert steering.get_tool_call_count(SESSION_KEY) == 0


def test_queued_bundle_is_drained_into_the_next_payload(tmp_path, monkeypatch):
    """Spec §4e: at the boundary the run picks up his message AND her interim
    reply, in that order, so the resumed model never answers him twice."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering

    mod = _load_runner()
    steering.clear(SESSION_KEY)
    gate = asyncio.Event()
    payloads = []
    calls = {"n": 0}

    async def fake_call(**kw):
        payloads.append(kw["messages"])
        calls["n"] += 1
        if calls["n"] == 1:
            return llm_mod.LLMResponse(
                text_blocks=["on it"],
                tool_uses=[sess_mod.ToolUseBlock(
                    id="t1", name="slow_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=["all done"], tool_uses=[],
                                   stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)
    deps = _deps(mod, tmp_path, SlowRegistry(gate))

    async def _run():
        task = asyncio.get_running_loop().create_task(
            mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
        await asyncio.sleep(0.15)
        steering.enqueue(SESSION_KEY, steering.SteerBundle(
            turn=FakeTurn(content="actually check the other file first"),
            reply_text="good call, that one next",
            enqueued_at=0.0, replied=True))
        gate.set()
        await task

    asyncio.run(_run())

    last = payloads[-1]
    texts = [b.text for m in last for b in m.content
             if getattr(b, "type", "") == "text"]
    assert any("actually check the other file first" in t for t in texts)
    assert any("good call, that one next" in t for t in texts)
    # His message must arrive before her reply to it.
    joined = " || ".join(texts)
    assert joined.index("actually check the other file") < joined.index("good call")
    assert steering.has_pending(SESSION_KEY) is False


def test_run_end_records_replied_bundles_and_leaves_unreplied_for_fallback(
        tmp_path, monkeypatch):
    """Spec §4f: a bundle she already answered becomes record. One the judge
    never answered was drained into the run before its reply — that reply
    was composed with his words in context, so it IS the answer and nothing
    stays queued (2026-08-28; a run that ends WITHOUT a reply still leaves
    it queued — see test_drained_bundle_is_requeued_when_the_run_ends_without_a_reply)."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering

    mod = _load_runner()
    steering.clear(SESSION_KEY)

    async def fake_call(**kw):
        return llm_mod.LLMResponse(text_blocks=["done"], tool_uses=[],
                                   stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)
    steering.enqueue(SESSION_KEY, steering.SteerBundle(
        turn=FakeTurn(content="answered already"),
        reply_text="yep", enqueued_at=0.0, replied=True))
    steering.enqueue(SESSION_KEY, steering.SteerBundle(
        turn=FakeTurn(content="never answered"),
        reply_text=None, enqueued_at=0.0, replied=False))

    deps = _deps(mod, tmp_path)
    asyncio.run(mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))

    reopened = asyncio.run(
        sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    texts = [b.text for m in reopened.messages for b in m.content
             if getattr(b, "type", "") == "text"]
    assert any("answered already" in t for t in texts)
    assert any("yep" in t for t in texts)
    assert any("never answered" in t for t in texts)      # drained: on the record
    assert steering.peek(SESSION_KEY) == []                # and answered by "done"
    steering.clear(SESSION_KEY)


def test_cancelled_run_drops_drained_unanswered_bundles(tmp_path, monkeypatch):
    """A cancelled run is dead work (spec §5.3): a bundle that drained into it
    but was never answered must NOT be re-queued by the finally — the client
    tears the queue down synchronously at cancel time, and the dying run's
    cleanup (which lands later) would silently resurrect it for the next
    sweep."""
    import pytest

    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering

    mod = _load_runner()
    steering.clear(SESSION_KEY)
    gates = [asyncio.Event(), asyncio.Event()]
    calls = {"n": 0}

    class TwoGateRegistry:
        """Like SlowRegistry, but each dispatch waits on its own gate so the
        test can hold the run inside tool 2 after tool 1 completed."""

        def __init__(self):
            self.i = 0

        def all_names(self):
            return ["slow_tool"]

        def schemas_for(self, ctx):
            return [{"name": "slow_tool", "description": "slow",
                     "input_schema": {"type": "object"}}]

        async def dispatch(self, tu, ctx):
            g = gates[min(self.i, len(gates) - 1)]
            self.i += 1
            await g.wait()
            return sess_mod.ToolResultBlock(
                tool_use_id=tu.id, content="tool finished", is_error=False)

    async def fake_call(**kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            return llm_mod.LLMResponse(
                text_blocks=[f"step {calls['n']}"],
                tool_uses=[sess_mod.ToolUseBlock(
                    id=f"t{calls['n']}", name="slow_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=["done"], tool_uses=[],
                                   stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)
    deps = _deps(mod, tmp_path, TwoGateRegistry())

    async def _run():
        task = asyncio.get_running_loop().create_task(
            mod.run_turn(FakeTurn(), deps=deps, on_chunk=None))
        await asyncio.sleep(0.15)        # inside tool 1
        steering.enqueue(SESSION_KEY, steering.SteerBundle(
            turn=FakeTurn(content="drained but never answered"),
            enqueued_at=0.0, replied=False))
        gates[0].set()                   # iteration 2 begins: drains the bundle
        await asyncio.sleep(0.15)        # inside tool 2
        task.cancel()                    # the stop
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())

    # Dead work: nothing re-queued for the sweep...
    assert steering.has_pending(SESSION_KEY) is False
    # ...but his words are still on the record in the transcript.
    reopened = asyncio.run(
        sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    texts = [b.text for m in reopened.messages for b in m.content
             if getattr(b, "type", "") == "text"]
    assert any("drained but never answered" in t for t in texts)


def test_prerecorded_turn_on_an_answered_exchange_ends_its_payload_on_his_words(tmp_path, monkeypatch):
    """2026-08-28: the deadline fallback / sweep re-dispatch a turn whose
    words and her interim reply are already on the record. The payload the
    model sees must still END on him (thinking mode 400s on an assistant
    tail), and the on-disk transcript must not gain a second user row."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from dataclasses import dataclass as _dc

    @_dc
    class FakeMd:
        user_side_prerecorded: bool = True
        origin: str = "user"

    mod = _load_runner()
    steering.clear(SESSION_KEY)
    steering.clear_run_state(SESSION_KEY)
    asyncio.run(mod._open_session_with_exchange(
        SESSION_KEY, tmp_path, "you stuck?", "hold on, mid-thing"))
    seen = {}

    async def fake_call(**kw):
        seen["messages"] = list(kw["messages"])
        return llm_mod.LLMResponse(text_blocks=["not stuck — here's the real answer"],
                                   tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    asyncio.run(mod.run_turn(FakeTurn(content="you stuck?", metadata=FakeMd()),
                             deps=_deps(mod, tmp_path), on_chunk=None))
    tail = seen["messages"][-1]
    assert tail.role == "user" and tail.content[0].text == "you stuck?"
    session = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    assert [m.role for m in session.messages] == ["user", "assistant", "assistant"]


# --- 2026-08-28: what the run itself answered ------------------------------

class _QuickRegistry:
    def all_names(self): return ["quick_tool"]
    def schemas_for(self, ctx):
        return [{"name": "quick_tool", "description": "q", "input_schema": {"type": "object"}}]
    async def dispatch(self, tu, ctx):
        import core.agent.session as sess_mod
        return sess_mod.ToolResultBlock(tool_use_id=tu.id, content="ok", is_error=False)


def _tool_then_final(llm_mod, sess_mod, final_text):
    calls = {"n": 0}

    async def fake_call(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return llm_mod.LLMResponse(
                text_blocks=[], tool_uses=[sess_mod.ToolUseBlock(id="t1", name="quick_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=([final_text] if final_text else []), tool_uses=[],
                                   stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
    return fake_call


def _run_with_drained_bundle(tmp_path, monkeypatch, *, final_text, **bundle_kw):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    mod = _load_runner()
    steering.clear(SESSION_KEY)
    steering.clear_run_state(SESSION_KEY)
    steering.enqueue(SESSION_KEY, steering.SteerBundle(
        turn=FakeTurn(content="also grab the file"), enqueued_at=1.0, **bundle_kw))
    monkeypatch.setattr(llm_mod, "call", _tool_then_final(llm_mod, sess_mod, final_text))
    asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, _QuickRegistry()), on_chunk=None))
    return steering.has_pending(SESSION_KEY)


def test_drained_bundle_answered_by_the_runs_own_reply_is_not_requeued(tmp_path, monkeypatch):
    """His words entered her context at the boundary (spec §4e) and her final
    reply was composed after them: that reply IS the answer. Re-queuing it
    for the sweep produced a redundant turn on an already-answered exchange
    (08-28 13:23, 08-24 12:59)."""
    assert _run_with_drained_bundle(tmp_path, monkeypatch, final_text="done — and the file too") is False


def test_drained_bundle_is_requeued_when_the_run_ends_without_a_reply(tmp_path, monkeypatch):
    """No reply came after his words: he is still owed one (spec §4f)."""
    assert _run_with_drained_bundle(tmp_path, monkeypatch, final_text="") is True
