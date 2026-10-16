"""The promise gate (2026-09-11): a reply that promises work this run did not do
is held back and she gets ONE more round to do it — or to say what she needs.

The morning of 2026-09-11, three replies in two hours narrated a state she was
not in: "give me a sec and I'll have your consent link" (no tool ran, nothing
started, forty minutes of silence); "I'm logged in and watching the bus" (one
presence call, no watcher); a false confession that a running SMART test was
not running. The first two are the same defect — a promise with nothing behind
it — and this gate is the application's half of the fix: the judge (an LLM
lane, `promise_check`) decides whether the draft commits her to something the
run has not done; the runner then re-asks her with the draft withheld.

Same harness as test_duplex_runner.py: the REAL steering/session modules, a
fake llm_client.call, a fake tool registry, a fake judge."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest


def _load_runner():
    from tests.agent.conftest import load_module
    return load_module("core/agent/runner.py", "agent_runner_promise_gate_under_test")


@dataclass
class FakeTurn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "can you get me the consent link?"
    metadata: Any = None
    received_at: Any = None


@dataclass
class FakeCtx:
    turn: Any = None
    user_text: str = "can you get me the consent link?"
    recent_history_text: str = ""
    identity_core_text: str = "I'm PaLLi."
    skills_text: str = ""
    runtime_text: str = ""
    memory_text: str = ""
    facts_text: str = ""
    is_owner: bool = True


CADENCE_JSON = ('{"should_respond": true, "register": "action_request", '
                '"length": "short", "phase": "settled_conversation", '
                '"target_beats": 1, "observation_text": "ok"}')
SESSION_KEY = "discord_text:1"
PROMISE = "Give me a sec and I'll have your consent link."
KEPT = "Here it is: https://example.test/authorize?x=1"


class QuickRegistry:
    def all_names(self):
        return ["quick_tool"]

    def schemas_for(self, ctx):
        return [{"name": "quick_tool", "description": "quick",
                 "input_schema": {"type": "object"}}]

    async def dispatch(self, tu, ctx):
        import core.agent.session as sess_mod
        return sess_mod.ToolResultBlock(tool_use_id=tu.id, content="link built", is_error=False)


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


def _scripted_llm(llm_mod, sess_mod, script):
    """`script` is a list of responses: a str = text-only reply; ("tool", text)
    = text + one quick_tool call. Records every call's kwargs."""
    calls: list[dict] = []

    async def fake_call(**kw):
        calls.append(kw)
        step = script[min(len(calls) - 1, len(script) - 1)]
        if isinstance(step, tuple):
            resp = llm_mod.LLMResponse(
                text_blocks=[step[1]],
                tool_uses=[sess_mod.ToolUseBlock(id=f"t{len(calls)}", name="quick_tool", input={})],
                stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)
        else:
            resp = llm_mod.LLMResponse(text_blocks=[step], tool_uses=[], stop_reason="end_turn",
                                       prompt_tokens=1, completion_tokens=1)
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


_QUICK = object()   # the default registry: one quick tool, no reminders


def _run(mod, tmp_path, fake_call, monkeypatch, *, judge, duplex=True, registry=_QUICK):
    import core.agent.llm_client as llm_mod
    import core.agent.steering as steering
    from core.agent import duplex as dx
    from core.agent import promise_gate as pg
    _reset(steering)
    monkeypatch.setattr(llm_mod, "call", fake_call)
    monkeypatch.setattr(pg, "check_empty_promise", judge)
    delivered: list[str] = []

    async def on_chunk(t):
        delivered.append(t)

    with patch.object(dx, "duplex_enabled", lambda: duplex):
        reg = QuickRegistry() if registry is _QUICK else registry
        result = asyncio.run(mod.run_turn(FakeTurn(), deps=_deps(mod, tmp_path, reg),
                                          on_chunk=on_chunk))
    return result, "".join(delivered)


def _judge_saying(verdict, seen: list | None = None):
    async def judge(*, user_text, draft, tool_names, **_kw):
        if seen is not None:
            seen.append({"user_text": user_text, "draft": draft, "tool_names": list(tool_names)})
        return verdict
    return judge


def test_an_empty_promise_is_held_back_and_she_gets_one_more_round(tmp_path, monkeypatch):
    """Draft 1 promises with no tool behind it → withheld; the second round
    (a tool call, then the real answer) is what reaches him. The promise never
    enters the transcript; the nudge rides the trailing system note."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [PROMISE, ("tool", "building it"), KEPT])
    seen: list = []
    verdict = pg.PromiseVerdict(empty_promise=True, promised="the consent link", why="nothing ran")
    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict, seen))

    assert delivered == KEPT
    assert result.response_text == KEPT
    assert PROMISE not in _assistant_texts(sess_mod, tmp_path)
    assert len(calls) == 3
    note = str(calls[1].get("trailing_system_note") or "")
    assert "the consent link" in note and "do it now" in note.lower()
    # the judge saw the draft and the (empty) list of tools this run had used
    assert seen and seen[0]["draft"] == PROMISE and seen[0]["tool_names"] == []


def test_a_reply_the_judge_clears_goes_out_untouched(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, ["Sure — what's the client id?"])
    verdict = pg.PromiseVerdict(empty_promise=False, promised="", why="")
    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict))
    assert delivered == "Sure — what's the client id?" and len(calls) == 1


def test_the_gate_fires_at_most_once_per_run(tmp_path, monkeypatch):
    """If she promises again after the nudge, the second draft is delivered —
    one extra round is the whole budget; the gate never loops."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [PROMISE, "I'll get to it in a bit."])
    verdict = pg.PromiseVerdict(empty_promise=True, promised="the link", why="nothing ran")
    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict))
    assert delivered == "I'll get to it in a bit." and len(calls) == 2


def test_a_judge_failure_never_blocks_the_reply(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [PROMISE])

    async def broken_judge(*, user_text, draft, tool_names, **_kw):
        raise RuntimeError("provider down")

    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=broken_judge)
    assert delivered == PROMISE and len(calls) == 1


def test_the_judge_sees_the_tools_this_run_used(tmp_path, monkeypatch):
    """"I'm watching the bus" after one set_presence call: the judge gets the
    names, and decides — the runner never hard-codes which tools are work."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent import promise_gate as pg
    mod = _load_runner()
    fake_call, _ = _scripted_llm(llm_mod, sess_mod, [("tool", "setting signage"), "I'm watching it."])
    seen: list = []
    verdict = pg.PromiseVerdict(empty_promise=False, promised="", why="")
    _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(verdict, seen))
    assert seen and seen[0]["tool_names"] == ["quick_tool"]


def test_duplex_off_means_no_gate(tmp_path, monkeypatch):
    """With duplex off the text streams as it is generated — nothing can be
    held back, so the judge is not even asked."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [PROMISE])
    asked: list = []

    async def judge(*, user_text, draft, tool_names, **_kw):
        asked.append(draft)
        from core.agent import promise_gate as pg
        return pg.PromiseVerdict(empty_promise=True, promised="x", why="y")

    result, delivered = _run(mod, tmp_path, fake_call, monkeypatch, judge=judge, duplex=False)
    assert delivered == PROMISE and asked == [] and len(calls) == 1


# --- The fourth way out (2026-10-08, the release basics) -------------------------
# 23 of the 33 empty promises the gate held back (every log on disk, 10-06) were
# one sentence: "I'll do X when Y happens". She has create_reminder and uses it
# well, and a fired reminder runs a full turn with her tools, so a scheduled check
# is a kept promise. The nudge named only three ways out and ended "Never a
# promise for later", so for the commonest catch the advice excluded the right
# answer. It now names the scheduled check, only on a turn that offers the tool.
# The same nudge told a turn with NO tools (a guest's) that "tools are always
# allowed"; it now says only what is true on that turn.

OLD_EMPTY_PROMISE_NUDGE = (
    "[system: your draft promised the consent link, but this turn did nothing toward it — no tool "
    "ran for it and nothing was started. You have no later: either do it now (tools are always "
    "allowed, as many rounds as it takes), or tell them plainly what you need from them, or that "
    "you can't. Never a promise for later. Do not mention this note.]"
)


def _empty(promised="the consent link"):
    from core.agent import promise_gate as pg
    return pg.PromiseVerdict(empty_promise=True, promised=promised, why="nothing ran")


def _ungrounded():
    from core.agent import promise_gate as pg
    return pg.PromiseVerdict(empty_promise=False, ungrounded=True, unsupported="that the drive finished",
                             why="the log was empty")


def test_without_the_new_facts_the_nudge_reads_exactly_as_before():
    """A work session has tools but no create_reminder; it keeps today's text."""
    from core.agent import promise_gate as pg
    assert pg.nudge_text(_empty()) == OLD_EMPTY_PROMISE_NUDGE
    assert pg.nudge_text(_empty(), can_act=True, can_schedule=False) == OLD_EMPTY_PROMISE_NUDGE


def test_with_create_reminder_the_nudge_names_a_scheduled_check():
    from core.agent import promise_gate as pg
    note = pg.nudge_text(_empty("a ping when the transfer lands"), can_act=True, can_schedule=True)
    assert "a ping when the transfer lands" in note
    assert "do it now" in note.lower()
    assert "create_reminder" in note
    assert "what you need from them" in note and "can't" in note
    assert note.startswith("[system: ") and note.endswith(" Do not mention this note.]")


@pytest.mark.parametrize("kind", ["empty_promise", "ungrounded", "both"])
def test_a_turn_with_no_tools_is_never_told_tools_are_allowed(kind):
    from core.agent import promise_gate as pg
    v = pg.PromiseVerdict(
        empty_promise=kind in ("empty_promise", "both"), promised="the consent link",
        ungrounded=kind in ("ungrounded", "both"), unsupported="that the drive finished",
        why="nothing ran",
    )
    note = pg.nudge_text(v, can_act=False, can_schedule=False)
    assert "tools are" not in note.lower()
    assert "create_reminder" not in note
    if v.empty_promise:
        assert "the consent link" in note
    if v.ungrounded:
        assert "that the drive finished" in note


class ReminderRegistry(QuickRegistry):
    def all_names(self):
        return ["quick_tool", "create_reminder"]

    def schemas_for(self, ctx):
        return [*super().schemas_for(ctx),
                {"name": "create_reminder", "description": "schedule a check",
                 "input_schema": {"type": "object"}}]


def test_the_runner_offers_the_scheduled_check_only_when_the_turn_has_the_tool(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent.builtin_tools import create_reminder as cr

    async def a_chat(**_kw):
        return ("discord", 123)

    monkeypatch.setattr(cr, "_resolve_door", a_chat)
    mod = _load_runner()
    with_tool, calls_a = _scripted_llm(llm_mod, sess_mod, [PROMISE, KEPT])
    _run(mod, tmp_path / "a", with_tool, monkeypatch, judge=_judge_saying(_empty()),
         registry=ReminderRegistry())
    assert "create_reminder" in str(calls_a[1].get("trailing_system_note") or "")

    without_tool, calls_b = _scripted_llm(llm_mod, sess_mod, [PROMISE, KEPT])
    _run(mod, tmp_path / "b", without_tool, monkeypatch, judge=_judge_saying(_empty()))
    note_b = str(calls_b[1].get("trailing_system_note") or "")
    assert "create_reminder" not in note_b and "do it now" in note_b.lower()


def test_the_runner_tells_a_turn_with_no_tools_only_what_is_true(tmp_path, monkeypatch):
    """A guest's run has no registry: the gate still judges the draft, and the
    nudge no longer claims tools that run does not have."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [PROMISE, KEPT])
    _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(_empty()), registry=None)
    trailing = str(calls[1].get("trailing_system_note") or "")
    # Only the gate's own note: other trailing notes (the length note's "tools are
    # always allowed") are approved wording outside this change, listed for the owner.
    note = trailing[trailing.index("[system: your draft"):]
    assert "the consent link" in note
    assert "tools are" not in note.lower() and "create_reminder" not in note


def test_reminders_are_offered_only_when_a_reminder_could_reach_a_chat(tmp_path, monkeypatch):
    """Fable's second read (10-08): create_reminder refuses when there is no
    Discord or Telegram chat to post in (a web-page-only install, the first door
    a stranger walks through). The advice must not steer her into a tool that
    can only say no."""
    import core.agent.llm_client as llm_mod
    import core.agent.session as sess_mod
    from core.agent.builtin_tools import create_reminder as cr

    async def no_chat(**_kw):
        return None

    monkeypatch.setattr(cr, "_resolve_door", no_chat)
    mod = _load_runner()
    fake_call, calls = _scripted_llm(llm_mod, sess_mod, [PROMISE, KEPT])
    _run(mod, tmp_path, fake_call, monkeypatch, judge=_judge_saying(_empty()), registry=ReminderRegistry())
    note = str(calls[1].get("trailing_system_note") or "")
    assert "create_reminder" not in note and "do it now" in note.lower()


def test_can_deliver_from_follows_the_tools_own_rule(monkeypatch):
    import asyncio

    from core.agent.builtin_tools import create_reminder as cr

    class ChatTurn:
        surface, channel_id, turn_owner_id = "discord_text", 42, 1

    class WebTurn:
        surface, channel_id, turn_owner_id = "dashboard", 0, 1

    assert asyncio.run(cr.can_deliver_from(ChatTurn())) is True   # the chat it was made in

    async def none(owner_id):
        return None

    monkeypatch.setattr(cr, "last_used_door", none)
    # 2026-10-10: no chat on record means the web page, which can take it now
    assert asyncio.run(cr.can_deliver_from(WebTurn())) is True

    async def down(owner_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(cr, "last_used_door", down)
    assert asyncio.run(cr.can_deliver_from(WebTurn())) is False   # an error never offers it
