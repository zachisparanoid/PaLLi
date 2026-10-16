"""Step 27 Phase A — runner seams: working notes, the stop check, the
problem wake, the overrun watcher, the presence clear.

Same harness as test_runner_steering.py: the REAL steering/session
modules, a fake llm_client.call, a fake tool registry."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest


def _load_runner():
    from tests.agent.conftest import load_module
    return load_module("core/agent/runner.py", "agent_runner_duplex_under_test")


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "build the adapter"
    metadata: Any = None
    received_at: Any = None


@dataclass
class FakeCtx:
    turn: Any = None
    user_text: str = "build the adapter"
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


class QuickRegistry:
    """A tool that returns at once — or errors, when told to."""

    def __init__(self, errors: int = 0):
        self.errors_left = errors

    def all_names(self):
        return ["quick_tool"]

    def schemas_for(self, ctx):
        return [{"name": "quick_tool", "description": "quick",
                 "input_schema": {"type": "object"}}]

    async def dispatch(self, tu, ctx):
        import core.agent.session as sess_mod
        if self.errors_left > 0:
            self.errors_left -= 1
            return sess_mod.ToolResultBlock(
                tool_use_id=tu.id, content="boom", is_error=True)
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


def _two_step_llm(llm_mod, sess_mod, note="working on it", final="done"):
    """Iteration 1: a working note + one tool call. Iteration 2: the reply."""
    calls = {"n": 0}

    async def fake_call(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            resp = llm_mod.LLMResponse(
                text_blocks=[note],
                tool_uses=[sess_mod.ToolUseBlock(id="t1", name="quick_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        else:
            resp = llm_mod.LLMResponse(text_blocks=[final], tool_uses=[],
                                       stop_reason="end_turn",
                                       prompt_tokens=1, completion_tokens=1)
        # The real llm_client.call feeds every text block to on_chunk when
        # the runner hands one over (llm_client.py, the buffered-collection
        # tail) — the fake honors the same contract, so "delivered" means
        # what it means live: whatever the runner let the client emit.
        on_chunk = kw.get("on_chunk")
        if on_chunk:
            for _t in resp.text_blocks:
                if _t:
                    await on_chunk(_t)
        return resp
    return fake_call, calls


def _reset(steering):
    steering.clear(SESSION_KEY)
    steering.clear_run_state(SESSION_KEY)


def _assistant_texts(sess_mod, tmp_path):
    session = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    out = []
    for m in session.messages:
        if m.role == "assistant":
            out.extend(b.text for b in m.content if isinstance(b, sess_mod.TextBlock))
    return out


def test_working_note_is_kept_but_not_delivered_when_duplex_on(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    delivered: list[str] = []

    async def on_chunk(t):
        delivered.append(t)

    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, QuickRegistry()),
                                 on_chunk=on_chunk))
    assert "".join(delivered) == "done"                       # the reply only
    texts = _assistant_texts(sess_mod, tmp_path)
    assert "working on it" in texts and "done" in texts       # the note is on the record


def test_working_note_still_delivered_when_duplex_off(tmp_path, monkeypatch):
    """Flag off = today's behavior, pinned."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    delivered: list[str] = []

    async def on_chunk(t):
        delivered.append(t)

    asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, QuickRegistry()),
                             on_chunk=on_chunk))
    joined = "".join(delivered)
    assert "working on it" in joined and "done" in joined


def test_repeated_tool_error_raises_task_problem_once(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    import ai.present_moment as pm
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    calls = {"n": 0}

    async def fake_call(**kw):
        calls["n"] += 1
        if calls["n"] <= 3:                       # three tool rounds, two of them errors
            return llm_mod.LLMResponse(
                text_blocks=["trying"],
                tool_uses=[sess_mod.ToolUseBlock(id=f"t{calls['n']}", name="quick_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=["gave up"], tool_uses=[],
                                   stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    fired: list[dict] = []
    monkeypatch.setattr(pm, "notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, QuickRegistry(errors=2)),
                                 on_chunk=None))
    problems = [f for f in fired if f["kind"] == "task_problem"]
    assert len(problems) == 1
    assert problems[0]["tool"] == "quick_tool" and problems[0]["what"] == "repeated_error"


def test_no_task_problem_when_duplex_off(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    import ai.present_moment as pm
    mod = _load_runner()
    _reset(steering)
    calls = {"n": 0}

    async def fake_call(**kw):
        calls["n"] += 1
        if calls["n"] <= 3:
            return llm_mod.LLMResponse(
                text_blocks=["trying"],
                tool_uses=[sess_mod.ToolUseBlock(id=f"t{calls['n']}", name="quick_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=["gave up"], tool_uses=[],
                                   stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    fired: list[dict] = []
    monkeypatch.setattr(pm, "notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, QuickRegistry(errors=2)),
                             on_chunk=None))
    assert not [f for f in fired if f["kind"] == "task_problem"]


def test_overrun_watcher_started_at_first_tool_call_and_cancelled_after(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    seen = {"started": 0, "cancelled": False}

    async def fake_watch(session_key, **kw):
        seen["started"] += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise
    monkeypatch.setattr(dx, "overrun_watch", fake_watch)
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, QuickRegistry()),
                                 on_chunk=None))
    assert seen == {"started": 1, "cancelled": True}


def test_presence_she_set_during_the_run_is_cleared_when_it_ends(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    import core.agent.hands_runtime as hr
    from types import SimpleNamespace
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    cleared: list[dict] = []

    async def change_presence(**kw):
        cleared.append(kw)
    hr.register_client(SimpleNamespace(guilds=[], change_presence=change_presence))
    # the hand ran mid-run and noted her signage:
    steering.note_presence_activity(SESSION_KEY, "heads down on the adapter")
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, QuickRegistry()),
                                 on_chunk=None))
    assert cleared == [{"activity": None}]
    hr.register_client(None)


def test_stop_requested_ends_the_run_at_the_boundary_with_nothing_delivered(tmp_path, monkeypatch):
    """Step 27 §3: the round-trip closes (tool result on disk), no second
    model call, nothing delivered, the run reports terminated_by='stopped'."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    calls = {"n": 0}

    async def fake_call(**kw):
        calls["n"] += 1
        return llm_mod.LLMResponse(
            text_blocks=["starting"],
            tool_uses=[sess_mod.ToolUseBlock(id="t1", name="quick_tool", input={})],
            stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)

    class StopMidRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            steering.request_stop(SESSION_KEY)      # "forget it" lands mid-tool
            return await super().dispatch(tu, ctx)

    delivered: list[str] = []

    async def on_chunk(t):
        delivered.append(t)

    with patch.object(dx, "duplex_enabled", lambda: True):
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, StopMidRegistry()),
                                          on_chunk=on_chunk))
    assert calls["n"] == 1                                        # no second model call
    assert delivered == []                                        # nothing reached him
    session = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    assert session.messages[-1].role == "tool"                    # the round-trip closed
    assert getattr(result, "terminated_by", None) == "stopped"
    assert not (getattr(result, "response_text", "") or "")      # no fallback re-send of "starting"


def test_stopped_run_drops_unanswered_steers_like_a_hard_cancel(tmp_path, monkeypatch):
    """Spec §5.3 under the cooperative stop: a steer drained into the run
    but never answered dies with the killed work — never re-queued for the
    sweep to resurrect after he said "forget it"."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    steering.enqueue(SESSION_KEY, steering.SteerBundle(
        turn=FakeTurn(content="also grab the file"), reply_text=None, enqueued_at=1.0))

    async def fake_call(**kw):
        return llm_mod.LLMResponse(
            text_blocks=["starting"],
            tool_uses=[sess_mod.ToolUseBlock(id="t1", name="quick_tool", input={})],
            stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)

    class StopMidRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            steering.request_stop(SESSION_KEY)
            return await super().dispatch(tu, ctx)

    with patch.object(dx, "duplex_enabled", lambda: True):
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, StopMidRegistry()),
                                          on_chunk=None))
    assert result.terminated_by == "stopped"
    assert steering.has_pending(SESSION_KEY) is False


def test_chime_inside_an_open_round_trip_leaves_the_transcript_valid(tmp_path, monkeypatch):
    """2026-08-28 12:59 live: chime_in rides the arbiter's release leg, whose
    custody leg appended her words to the RUN's transcript — a second writer,
    landing between the open tool_use and its result (validity §6 / I1).
    The tool_use + result already are the record; the chime must not write
    a transcript row of its own."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from types import SimpleNamespace
    from core.turn import outbound
    from tests.agent.test_transcript_normalizer import assert_provider_valid
    mod = _load_runner()
    key = "discord:555"
    steering.clear(key); steering.clear_run_state(key)

    class _Runtime:
        sent: list = []
        async def send_envelope(self, *, platform, surface_id, envelope):
            self.sent.append(envelope.text)
    monkeypatch.setattr("core.channel_runtime.channel_runtime", _Runtime())

    async def _persist(*, turn, response_text):
        return None
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", _persist)
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    class ChimeRegistry:
        def all_names(self): return ["chime_in"]
        def schemas_for(self, ctx):
            return [{"name": "chime_in", "description": "c", "input_schema": {"type": "object"}}]
        async def dispatch(self, tu, ctx):
            await outbound.record_outbound(owner_id=1, channel_id=555, text="found it — a 9225",
                                           origin="duplex_chime", extra={"mid_task": True})
            return sess_mod.ToolResultBlock(tool_use_id=tu.id, content="Said (text): found it", is_error=False)

    calls = {"n": 0}
    async def fake_call(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return llm_mod.LLMResponse(
                text_blocks=[], tool_uses=[sess_mod.ToolUseBlock(id="c1", name="chime_in", input={"text": "found it"})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=["done"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    turn = FakeTurn(metadata=SimpleNamespace(channel_id=555, origin="user", user_side_prerecorded=False))
    asyncio.run(mod.run_turn(turn, deps=_deps(mod, tmp_path, ChimeRegistry()), on_chunk=None))
    session = asyncio.run(sess_mod.Session.open_or_create(key, root=tmp_path))
    assert_provider_valid(session.messages)
    assert [m.role for m in session.messages] == ["user", "assistant", "tool", "assistant"]


def test_pause_requested_ends_the_run_at_the_boundary_and_records_paused(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering); steering.clear_paused(SESSION_KEY)
    calls = {"n": 0}

    async def fake_call(**kw):
        calls["n"] += 1
        return llm_mod.LLMResponse(
            text_blocks=["measuring"],
            tool_uses=[sess_mod.ToolUseBlock(id="t1", name="quick_tool", input={})],
            stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)

    class PauseMidRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            steering.request_pause(SESSION_KEY)          # "hold on" lands mid-tool
            return await super().dispatch(tu, ctx)

    delivered: list[str] = []

    async def on_chunk(t):
        delivered.append(t)

    with patch.object(dx, "duplex_enabled", lambda: True):
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, PauseMidRegistry()),
                                          on_chunk=on_chunk))
    assert calls["n"] == 1
    assert result.terminated_by == "paused"
    assert delivered == [] and not (result.response_text or "")        # no fallback re-send
    p = steering.get_paused(SESSION_KEY)
    assert p is not None and "quick_tool" in p.now_doing
    session = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    assert session.messages[-1].role == "tool"                          # the round-trip closed
    steering.clear_paused(SESSION_KEY)


def test_pause_answers_the_steers_it_drained_nothing_requeued(tmp_path, monkeypatch):
    """Correction 2: a paused run must not be swept into a new turn by its
    own 'hold on' bundle — that would un-pause it a second later."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering); steering.clear_paused(SESSION_KEY)
    steering.enqueue(SESSION_KEY, steering.SteerBundle(
        turn=FakeTurn(content="hold on a sec"), reply_text=None, enqueued_at=1.0))

    async def fake_call(**kw):
        return llm_mod.LLMResponse(
            text_blocks=["measuring"],
            tool_uses=[sess_mod.ToolUseBlock(id="t1", name="quick_tool", input={})],
            stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)

    class PauseMidRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            steering.request_pause(SESSION_KEY)
            return await super().dispatch(tu, ctx)

    with patch.object(dx, "duplex_enabled", lambda: True):
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, PauseMidRegistry()),
                                          on_chunk=None))
    assert result.terminated_by == "paused"
    assert steering.has_pending(SESSION_KEY) is False
    steering.clear_paused(SESSION_KEY)


def _note_seen_for(tmp_path, monkeypatch, extra: dict) -> str:
    """Run one plain turn carrying ``extra`` and return the trailing system
    note the runner handed the model."""
    import core.agent.llm_client as llm_mod
    import core.agent.steering as steering
    from types import SimpleNamespace
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    seen = {}

    async def fake_call(**kw):
        seen["note"] = kw.get("trailing_system_note") or ""
        return llm_mod.LLMResponse(text_blocks=["ok"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    turn = FakeTurn(content="go on", metadata=SimpleNamespace(origin="user", extra=dict(extra),
                                                              user_side_prerecorded=False))
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(turn, deps=_deps(mod, tmp_path), on_chunk=None))
    return seen["note"]


def test_resume_turn_carries_a_resume_note(tmp_path, monkeypatch):
    note = _note_seen_for(tmp_path, monkeypatch, {"resume_of": "running local_shell (iteration 3)"})
    assert "[resumed" in note and "local_shell" in note


def test_non_resume_turn_while_paused_carries_a_paused_note(tmp_path, monkeypatch):
    note = _note_seen_for(tmp_path, monkeypatch, {"paused_task": "running local_shell (iteration 3)"})
    assert "[PAUSED" in note and "do not continue" in note


def test_plain_turn_carries_neither_note(tmp_path, monkeypatch):
    note = _note_seen_for(tmp_path, monkeypatch, {})
    assert "[resumed" not in note and "[PAUSED" not in note


def test_parent_run_holds_a_task_slot_while_it_works_and_releases_it(tmp_path, monkeypatch):
    """Correction 1: the run itself is one of the two tasks his cap allows."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx, task_slots as ts
    mod = _load_runner()
    _reset(steering); ts.reset_for_tests()
    seen = {}

    class SlotPeekRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            seen["held_during"] = ts.held(SESSION_KEY)
            return await super().dispatch(tu, ctx)

    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, SlotPeekRegistry()), on_chunk=None))
    assert seen["held_during"] == ["the run"]
    assert ts.held(SESSION_KEY) == []


def test_sidecar_child_run_does_not_take_its_own_slot(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from types import SimpleNamespace
    from core.agent import duplex as dx, task_slots as ts
    mod = _load_runner()
    ts.reset_for_tests()
    child = "discord:1#sidecar-1"
    steering.clear(child); steering.clear_run_state(child)
    seen = {}

    class SlotPeekRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            seen["held_child"] = ts.held(child); seen["held_parent"] = ts.held("discord:1")
            return await super().dispatch(tu, ctx)

    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    turn = FakeTurn(metadata=SimpleNamespace(channel_id=1, origin="user", user_side_prerecorded=False,
                                             extra={"session_key_override": child, "sidecar_of": "discord:1",
                                                    "sidecar_kind": "task"}))
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(turn, deps=_deps(mod, tmp_path, SlotPeekRegistry()), on_chunk=None))
    assert seen["held_child"] == [] and seen["held_parent"] == []   # the launcher owns the slot


def test_drain_seam_leaves_a_running_sidecar_bundle_queued(tmp_path, monkeypatch):
    """Correction 3: the first hand. His words are not written into the
    parent until the sidecar's reply exists."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    b = steering.SteerBundle(turn=FakeTurn(content="what's the wifi again?"), enqueued_at=1.0, sidecar_running=True)
    steering.enqueue(SESSION_KEY, b)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, QuickRegistry()), on_chunk=None))
    assert steering.peek(SESSION_KEY) == [b]                              # untouched, still queued
    texts = _assistant_texts(sess_mod, tmp_path)
    session = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    assert not any("wifi" in (blk.text if hasattr(blk, "text") else "") for m in session.messages for blk in m.content)
    steering.clear(SESSION_KEY)


def test_drained_replied_exchange_gets_a_continuation_tail_not_his_words(tmp_path, monkeypatch):
    """Correction 4: after the boundary writes his side question and her
    sidecar answer, the next model call must be told to carry on — not
    handed his question again."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    steering.enqueue(SESSION_KEY, steering.SteerBundle(
        turn=FakeTurn(content="what's the wifi again?"), enqueued_at=1.0,
        replied=True, reply_text="on the fridge"))
    tails = []

    async def fake_call(**kw):
        tails.append(kw["messages"][-1])
        if len(tails) == 1:
            return llm_mod.LLMResponse(text_blocks=["working"],
                tool_uses=[sess_mod.ToolUseBlock(id="t1", name="quick_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        return llm_mod.LLMResponse(text_blocks=["done"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)

    class DrainLater(QuickRegistry):
        pass
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, DrainLater()), on_chunk=None))
    # the bundle was queued before the run → drained at iteration 1 → call 1's tail is the note
    first = tails[0]
    assert first.role == "user"
    assert "carry on" in first.content[0].text and "wifi" not in first.content[0].text


def test_hand_back_end_to_end_the_parent_writes_the_sidecar_exchange_at_its_boundary(tmp_path, monkeypatch):
    """Correction 9: launcher → on_done → queue → the parent's drain, with
    the real pieces. The child transcript is the sidecar's; the parent gets
    ONE exchange, after its round-trip closed, and stays valid."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from types import SimpleNamespace
    from bots.discord import steering_fork as sf
    from core.agent import duplex as dx, sidecar, task_slots as ts
    from core.agent.steering_reply import SteerJudgment
    from tests.agent.test_transcript_normalizer import assert_provider_valid
    mod = _load_runner()
    PARENT = "discord:66"
    steering.clear(PARENT); steering.clear_run_state(PARENT); ts.reset_for_tests(); sidecar.reset_for_tests()
    monkeypatch.setattr(sf, "_flag_on", lambda: True)
    async def judge(turn, **kw): return SteerJudgment(reply=None, full_turn=True)
    monkeypatch.setattr(sf, "judge_mid_task", judge)

    async def child_dispatch(turn):                      # the sidecar's full turn, faked
        return SimpleNamespace(responded=True, response_text="on the fridge", attachments=())
    async def deliver(result): pass
    async def send(t): raise AssertionError("no holding line")

    async def launch_sidecar(turn, *, kind, on_done):
        return sidecar.run_sidecar(parent_key=PARENT, turn=turn, kind=kind, dispatch=child_dispatch,
                                   deliver=deliver, sessions_root=tmp_path, on_done=on_done)

    gate = asyncio.Event()
    class HeldRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            await gate.wait()
            return await super().dispatch(tu, ctx)

    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    md = SimpleNamespace(channel_id=66, origin="user", user_side_prerecorded=False, extra={})
    parent_turn = FakeTurn(content="build it", metadata=md)
    side_turn = SimpleNamespace(surface="discord_text", owner_id=1, content="what's the wifi again?",
                                metadata=SimpleNamespace(channel_id=66, origin="user", user_side_prerecorded=True, extra={}))

    async def scenario():
        with patch.object(dx, "duplex_enabled", lambda: True):
            parent = asyncio.create_task(mod.run_turn(parent_turn, deps=_deps(mod, tmp_path, HeldRegistry()), on_chunk=None))
            for _ in range(200):
                await asyncio.sleep(0)
                if steering.is_working(PARENT):
                    break
            taken = await sf.handle_mid_task(side_turn, session_key=PARENT, is_owner=True, send=send,
                                             run_in_flight=True, dispatch=None, sessions_root=tmp_path,
                                             launch_sidecar=launch_sidecar)
            assert taken is True
            for _ in range(50):                            # let the sidecar finish and hand back
                await asyncio.sleep(0)
            [b] = steering.peek(PARENT)
            assert b.replied and b.reply_text == "on the fridge" and not b.sidecar_running
            gate.set()
            await parent
    asyncio.run(scenario())
    session = asyncio.run(sess_mod.Session.open_or_create(PARENT, root=tmp_path))
    assert_provider_valid(session.messages)
    roles = [m.role for m in session.messages]
    assert roles.index("user", 1) > roles.index("tool")                 # after the round-trip closed
    texts = [blk.text for m in session.messages for blk in m.content if isinstance(blk, sess_mod.TextBlock)]
    assert texts.count("what's the wifi again?") == 1 and texts.count("on the fridge") == 1
    child = asyncio.run(sess_mod.Session.open_or_create(PARENT + "#sidecar-1", root=tmp_path))
    assert [m.role for m in child.messages] == ["user"]                 # the seed only (dispatch was faked)
    assert ts.talk_held(PARENT) is False


def test_a_drained_interim_never_sits_bare_inside_a_tool_round(tmp_path, monkeypatch):
    """20:09 live (DEFECT_RECORD §13): a replied bundle drained at a
    boundary, then the run kept calling tools. The next payload must not
    carry a bare assistant message after the last user message."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    payloads = []

    async def fake_call(**kw):
        payloads.append(list(kw["messages"]))
        n = len(payloads)
        if n <= 3:
            return llm_mod.LLMResponse(text_blocks=[f"step {n}"],
                tool_uses=[sess_mod.ToolUseBlock(id=f"t{n}", name="quick_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1, reasoning="thought")
        return llm_mod.LLMResponse(text_blocks=["done"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)

    class EnqueueOnFirst(QuickRegistry):
        def __init__(self): super().__init__(); self.n = 0
        async def dispatch(self, tu, ctx):
            self.n += 1
            if self.n == 1:                       # his words + her in-hand reply arrive mid-tool
                steering.enqueue(SESSION_KEY, steering.SteerBundle(
                    turn=FakeTurn(content="also, PLA or PETG?"), enqueued_at=1.0,
                    replied=True, reply_text="PLA for armor, PETG for frame parts"))
            return await super().dispatch(tu, ctx)

    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, EnqueueOnFirst()), on_chunk=None))
    assert len(payloads) == 4
    for k, p in enumerate(payloads[1:], start=2):                      # every call after the drain
        last_user = max(i for i, m in enumerate(p) if m.role == "user")
        bare = [m for m in p[last_user + 1:] if m.role == "assistant"
                and not any(isinstance(b, sess_mod.ToolUseBlock) for b in m.content)]
        assert bare == [], f"call {k}: a bare assistant message inside the round"
    # the fold carried her answer inside his message
    p2 = payloads[1]
    folded = [m for m in p2 if m.role == "user" and "PLA for armor" in m.content[0].text]
    assert folded and folded[0].content[0].text.startswith("also, PLA or PETG?")
    # and the transcript on disk keeps the true shape: user row, then assistant row
    session = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    texts = [(m.role, blk.text) for m in session.messages for blk in m.content if isinstance(blk, sess_mod.TextBlock)]
    assert ("user", "also, PLA or PETG?") in texts and ("assistant", "PLA for armor, PETG for frame parts") in texts


# ── DEFECT_RECORD §17 (2026-09-01): the list that landed on scratch paper ────
# She wrote his 15-line budget list, called note_to_self in the same breath,
# and Phase A withheld the whole thing as a working note — while her own
# transcript made it look sent ("the budget list I sent"). The payload she
# reads now stamps every working note "he has not seen this".

def test_mark_working_notes_is_pure_idempotent_and_targets_only_tool_iterations():
    import core.agent.session as sess_mod
    from core.agent.session import mark_working_notes_for_llm, WORKING_NOTE_MARK
    msgs = [
        sess_mod.AgentMessage(role="user", content=[sess_mod.TextBlock(text="gimme the list")]),
        sess_mod.AgentMessage(role="assistant", content=[
            sess_mod.TextBlock(text="here's the fill-in list: 1. rent 2. power"),
            sess_mod.ToolUseBlock(id="t1", name="note_to_self", input={})]),
        sess_mod.AgentMessage(role="tool", content=[sess_mod.ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)]),
        sess_mod.AgentMessage(role="assistant", content=[sess_mod.TextBlock(text="that's the shape of it")]),
    ]
    out = mark_working_notes_for_llm(msgs)
    assert out[1].content[0].text.startswith(WORKING_NOTE_MARK)          # note + tool → marked
    assert "1. rent" in out[1].content[0].text
    assert out[3].content[0].text == "that's the shape of it"            # the delivered reply, untouched
    assert msgs[1].content[0].text.startswith("here's")                  # pure: input untouched
    assert mark_working_notes_for_llm(out)[1].content[0].text.count(WORKING_NOTE_MARK) == 1   # idempotent
    assert mark_working_notes_for_llm([]) == []


def test_next_turn_payload_stamps_the_prior_working_note_when_duplex_on(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    from core.agent.session import WORKING_NOTE_MARK
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod, note="here's the fill-in list: 1. rent", final="that's the shape of it")
    monkeypatch.setattr(llm_mod, "call", fake_call)
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(content="gimme the list"), deps=_deps(mod, tmp_path, QuickRegistry()), on_chunk=None))
        payloads = []

        async def second_call(**kw):
            payloads.append(kw.get("messages") or [])
            return llm_mod.LLMResponse(text_blocks=["here it is again, in full"], tool_uses=[],
                                       stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
        monkeypatch.setattr(llm_mod, "call", second_call)
        asyncio.run(mod.run_turn(FakeTurn(content="i never got a list?"), deps=_deps(mod, tmp_path, QuickRegistry()), on_chunk=None))
    texts = [b.text for m in payloads[-1] for b in getattr(m, "content", []) if isinstance(b, sess_mod.TextBlock)]
    marked = [t for t in texts if "1. rent" in t]
    assert marked and marked[0].startswith(WORKING_NOTE_MARK)
    session = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    on_disk = [b.text for m in session.messages for b in m.content if isinstance(b, sess_mod.TextBlock) and "1. rent" in b.text]
    assert on_disk and not on_disk[0].startswith(WORKING_NOTE_MARK)      # payload-only, never the record


def test_next_turn_payload_is_unmarked_when_duplex_off(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent.session import WORKING_NOTE_MARK
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod, note="here's the fill-in list: 1. rent", final="done")
    monkeypatch.setattr(llm_mod, "call", fake_call)
    asyncio.run(mod.run_turn(FakeTurn(content="gimme the list"), deps=_deps(mod, tmp_path, QuickRegistry()), on_chunk=None))
    payloads = []

    async def second_call(**kw):
        payloads.append(kw.get("messages") or [])
        return llm_mod.LLMResponse(text_blocks=["ok"], tool_uses=[], stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", second_call)
    asyncio.run(mod.run_turn(FakeTurn(content="thanks"), deps=_deps(mod, tmp_path, QuickRegistry()), on_chunk=None))
    texts = [b.text for m in payloads[-1] for b in getattr(m, "content", []) if isinstance(b, sess_mod.TextBlock)]
    assert not any(t.startswith(WORKING_NOTE_MARK) for t in texts)


def test_duplex_block_carries_the_working_note_law():
    from core.agent import duplex as dx
    with patch.object(dx, "duplex_enabled", lambda: True):
        block = dx.render_duplex_block()
    assert "final message" in block and "haven't seen the note" in block
    # §20: the stamp is the system's (an imitated one is stripped at the
    # model boundary; Batch 7 states that as a fact, not a ban).
    assert "the system stamps" in block and "`[system: working note" in block


# DEFECT_RECORD §20 (2026-09-02): the stamp she learned to write. Within a
# day of §17 going live she copied the payload-only stamp into a real reply
# and it reached his phone verbatim — "(working note — never sent; he has
# not seen this) …". A stamp in her own prose voice is a stamp she can
# imitate. It is now unmistakably the system's, and anything she writes
# that looks like one is stripped at the model boundary: before her record,
# before the chunker, before Discord.

def test_working_note_mark_is_a_system_stamp_and_strip_removes_every_shape():
    from core.agent.session import WORKING_NOTE_MARK, strip_working_note_mark
    assert WORKING_NOTE_MARK.startswith("[system:") and WORKING_NOTE_MARK.rstrip().endswith("]")
    assert strip_working_note_mark(WORKING_NOTE_MARK + "hey, the list") == "hey, the list"
    # the legacy prose stamp — the one she learned live on 09-01
    assert strip_working_note_mark("(working note — never sent; he has not seen this) hey") == "hey"
    assert strip_working_note_mark(WORKING_NOTE_MARK + WORKING_NOTE_MARK + "twice") == "twice"
    assert strip_working_note_mark("  [system: working note — never delivered; he has not seen this]\nhey") == "hey"
    assert strip_working_note_mark("plain reply") == "plain reply"
    assert strip_working_note_mark("") == ""


def test_an_imitated_stamp_never_reaches_his_phone_or_her_record(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    from core.agent.session import WORKING_NOTE_MARK
    mod = _load_runner()
    _reset(steering)
    imitated = WORKING_NOTE_MARK + "here's the list again: 1. rent 2. power"
    fake_call, _ = _two_step_llm(
        llm_mod, sess_mod,
        note="(working note — never sent; he has not seen this) drafting it",
        final=imitated,
    )
    monkeypatch.setattr(llm_mod, "call", fake_call)
    delivered: list[str] = []

    async def on_chunk(s):
        delivered.append(s)

    with patch.object(dx, "duplex_enabled", lambda: True):
        res = asyncio.run(mod.run_turn(FakeTurn(content="the list?"), deps=_deps(mod, tmp_path, QuickRegistry()), on_chunk=on_chunk))
    assert "".join(delivered) == "here's the list again: 1. rent 2. power"
    assert res.response_text == "here's the list again: 1. rent 2. power"
    session = asyncio.run(sess_mod.Session.open_or_create(SESSION_KEY, root=tmp_path))
    texts = [b.text for m in session.messages if m.role == "assistant"
             for b in m.content if isinstance(b, sess_mod.TextBlock)]
    assert texts and not any("working note" in t for t in texts)   # her record holds her words, never the stamp


def test_runner_forwards_deps_call_kwargs_to_the_llm_call(tmp_path, monkeypatch):
    """The runner passes `RunnerDeps.call_kwargs` straight into llm_client.call —
    dispatch fills it (e.g. extra_body for a local Ollama brain); empty by default."""
    import dataclasses
    import core.agent.llm_client as llm_mod
    import core.agent.steering as steering
    mod = _load_runner(); _reset(steering)
    seen = []

    async def fake_call(**kw):
        seen.append(kw)
        return llm_mod.LLMResponse(text_blocks=["ok"], tool_uses=[], stop_reason="end_turn", prompt_tokens=1, completion_tokens=1)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    deps = _deps(mod, tmp_path, QuickRegistry())
    asyncio.run(mod.run_turn(FakeTurn(content="hi"), deps=deps, on_chunk=None))
    assert "extra_body" not in seen[-1]
    deps2 = dataclasses.replace(deps, call_kwargs={"extra_body": {"reasoning_effort": "none"}})
    asyncio.run(mod.run_turn(FakeTurn(content="hi again"), deps=deps2, on_chunk=None))
    assert seen[-1]["extra_body"] == {"reasoning_effort": "none"}


# ── The work note (the owner, 10-12) ─────────────────────────────────────
# Mid-task, PaLLi sees the work as it stands: what was asked, what ran and
# came back, what is running, how long. Facts only.

class PeekingRegistry(QuickRegistry):
    """A tool that reads, while it runs, the note a mid-task reply would see."""

    def __init__(self):
        super().__init__()
        self.seen: list[str] = []

    async def dispatch(self, tu, ctx):
        import core.agent.steering as steering
        self.seen.append(steering.get_now_doing(SESSION_KEY))
        return await super().dispatch(tu, ctx)


def test_the_work_note_names_the_ask_and_the_running_call(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod, note="checking the adapter")
    monkeypatch.setattr(llm_mod, "call", fake_call)
    registry = PeekingRegistry()
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, registry)))
    [seen] = registry.seen
    assert "What they asked: build the adapter" in seen
    assert "Running right now:\n- quick_tool(" in seen
    assert "My latest working note: checking the adapter" in seen
    assert steering.get_now_doing(SESSION_KEY) == ""          # forgotten with the run


def test_after_a_round_the_note_shows_what_came_back(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    seen: list[str] = []

    async def peeking_call(**kw):
        seen.append(steering.get_now_doing(SESSION_KEY))
        return await fake_call(**kw)

    monkeypatch.setattr(llm_mod, "call", peeking_call)
    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, QuickRegistry())))
    assert len(seen) >= 2
    assert seen[0] == ""                                       # nothing has run yet
    assert "Done so far:\n- quick_tool(" in seen[1] and "tool finished" in seen[1]
    assert "Running right now: nothing" in seen[1]


def test_a_cancelled_run_forgets_its_work_note(tmp_path, monkeypatch):
    """Review Focus 2: a run cancelled mid-tool (a barge-in) leaves no note behind."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)

    class StuckRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            await asyncio.sleep(3600)              # cancelled long before this ends

    async def run():
        task = asyncio.create_task(mod.run_turn(
            FakeTurn(), deps=_deps(mod, tmp_path, StuckRegistry())))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if steering.get_now_doing(SESSION_KEY):
                break
        assert "Running right now:\n- quick_tool(" in steering.get_now_doing(SESSION_KEY)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(run())
    assert steering.get_now_doing(SESSION_KEY) == ""


def test_a_pause_keeps_a_short_true_note(tmp_path, monkeypatch):
    """Fable's review (10-12): "hold on" stores a one-line note with no
    durations, not the full work note frozen at the moment of the pause."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    mod = _load_runner()
    _reset(steering)
    steering.clear_paused(SESSION_KEY)
    fake_call, _ = _two_step_llm(llm_mod, sess_mod)
    monkeypatch.setattr(llm_mod, "call", fake_call)

    class PausingRegistry(QuickRegistry):
        async def dispatch(self, tu, ctx):
            steering.request_pause(SESSION_KEY)      # "hold on" while the tool runs
            return await super().dispatch(tu, ctx)

    with patch.object(dx, "duplex_enabled", lambda: True):
        asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, PausingRegistry())))
    paused = steering.get_paused(SESSION_KEY)
    steering.clear_paused(SESSION_KEY)
    assert paused is not None
    assert "\n" not in paused.now_doing and "build the adapter" in paused.now_doing
    assert "Working on it" not in paused.now_doing
