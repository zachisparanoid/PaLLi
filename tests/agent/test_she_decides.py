"""She decides whether to answer, for anyone (Batch 8, task 2a — B8-2).

The decision core, behind ``companion.she_decides`` (rulings T2-1..T2-3,
T2a-1, T2a-2):

- the reply call itself can choose to stay quiet, as a first-class outcome:
  a ``stay_quiet`` control call the RUNNER offers on human conversation
  turns (the owner's, the dashboard's, a guest's) and recognises — never a
  registry tool, so a guest keeps no tool registry at all;
- code makes the choice binding: nothing is sent on any door (no words, no
  emoji), the message stays in her transcript, and the choice counts as
  answered (no re-queue, no re-ask);
- in a full turn the quick vote no longer decides silence — her fuller self
  does (the 07-06 lesson: a vote once dropped a real request);
- the quick vote's text is participant-framed (an AI that knows it is one)
  and answers answer / wait / stay_quiet;
- reminders, her workbench and the rest keep today's behaviour exactly.

Stand-in names only: Robin is the owner, Sam a guest.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from tests.agent.she_decides_harness import (
    DM_CHANNEL,
    GUEST_ID,
    OWNER_APP_ID,
    ROOM_CHANNEL,
    DmHarness,
    vote,
)


# The door builds its Turn without a received_at (core/turn/ingest.py, the
# Telegram intake), whose `datetime.utcnow()` is production debt pytest.ini
# leaves unfiltered on purpose. Not this test's finding, so it adds nothing to
# the gate's warning count.
_UTCNOW_DEBT = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


@pytest.fixture
def dm_harness(monkeypatch, tmp_path) -> DmHarness:
    return DmHarness(monkeypatch, tmp_path)


def _tool_names(call: dict[str, Any]) -> list[str]:
    return [t["name"] for t in (call.get("tools") or [])]


# ── the reply call chooses quiet, and it binds ────────────────────────────

@pytest.mark.asyncio
async def test_do_not_respond_sends_nothing_not_even_an_emoji_and_is_still_recorded(dm_harness):
    # The model writes an emoji AND calls stay_quiet: the emoji is never sent.
    dm_harness.model_chooses_quiet("they asked me not to", text=["🤐"])
    result = await dm_harness.says("do not respond to this message")

    assert dm_harness.sent == []
    assert result.responded is False and result.response_text == ""
    assert result.quiet is True and result.quiet_reason == "they asked me not to"
    assert result.cadenced_reply is None
    assert await dm_harness.transcript_has("do not respond to this message")
    assert len(dm_harness.calls) == 1          # no further model call after the choice


@pytest.mark.asyncio
async def test_the_quiet_choice_is_offered_to_the_owner_as_a_control_call(dm_harness):
    dm_harness.model_answers("sure")
    await dm_harness.says("what's up")
    names = _tool_names(dm_harness.calls[0])
    assert names == ["look_up", "stay_quiet"]          # the owner's belt, then the control call
    schema = dm_harness.calls[0]["tools"][-1]
    assert schema["input_schema"]["required"] == ["reason"]


@pytest.mark.asyncio
async def test_a_real_question_is_never_lost_when_the_quick_vote_says_quiet(dm_harness):
    # The 07-06 lesson: in a full turn the quick vote no longer decides silence.
    from core.agent.runner import SILENCE_NOTE

    dm_harness.cadence_reply = vote("stay_quiet")
    dm_harness.model_answers("Here are the last three commits.")
    result = await dm_harness.says("check the git commits")

    assert dm_harness.sent == ["Here are the last three commits."]
    assert await dm_harness.transcript_has("check the git commits")
    note = dm_harness.calls[0].get("trailing_system_note") or ""
    assert SILENCE_NOTE not in note          # the vote no longer steers the reply toward silence
    assert result.quiet is False


@pytest.mark.asyncio
async def test_a_wait_vote_in_a_full_turn_is_not_a_silence_either(dm_harness):
    from core.agent.runner import SILENCE_NOTE

    dm_harness.cadence_reply = vote("wait")
    dm_harness.model_answers("go on, I'm listening")
    await dm_harness.says("you won't believe this")
    assert SILENCE_NOTE not in (dm_harness.calls[0].get("trailing_system_note") or "")
    assert dm_harness.sent == ["go on, I'm listening"]


# Moved deliberately (R14, T8; plan §2b.6): "a guest has no registry" holds
# while features.guest_web_search is off (as shipped); with it on a guest's
# belt is web_search and nothing else (the twins below). Both run with her
# first look off and on (a guest's turn straight into the spine has no room
# pass, so with the look on the runner takes it).
@pytest.mark.asyncio
@pytest.mark.parametrize("first_look", [False, True])
async def test_a_guest_can_choose_quiet_and_still_has_no_tool_registry(monkeypatch, tmp_path,
                                                                       first_look):
    """Ruling T2-2: the control call is the runner's, so a guest — who gets no
    registry at all — can still choose quiet, and still reaches no tool."""
    dm_harness = DmHarness(monkeypatch, tmp_path, first_look=first_look)
    captured = {}
    import core.agent.runner as runner_mod

    real_run_turn = runner_mod.run_turn

    async def spy(turn, *, deps, on_chunk=None):
        captured["registry"] = deps.tool_registry
        return await real_run_turn(turn, deps=deps, on_chunk=on_chunk)

    dm_harness.mp.setattr(runner_mod, "run_turn", spy)
    dm_harness.model_chooses_quiet("Sam asked me not to answer this one")
    result = await dm_harness.says("polly don't respond", who="Sam", channel=ROOM_CHANNEL,
                                   is_dm=False)

    assert captured["registry"] is None
    assert _tool_names(dm_harness.calls[0]) == ["stay_quiet"]
    assert dm_harness.sent == [] and result.quiet is True
    assert await dm_harness.transcript_has("[Sam]: polly don't respond", channel=ROOM_CHANNEL,
                                           owner_id=GUEST_ID)
    assert len(dm_harness.look_prompts) == (1 if first_look else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_look", [False, True])
async def test_with_guest_search_on_a_guest_can_choose_quiet_and_has_web_search_only(
        monkeypatch, tmp_path, first_look):
    dm_harness = DmHarness(monkeypatch, tmp_path, first_look=first_look, guest_search=True)
    captured = {}
    import core.agent.runner as runner_mod

    real_run_turn = runner_mod.run_turn

    async def spy(turn, *, deps, on_chunk=None):
        captured["registry"] = deps.tool_registry
        return await real_run_turn(turn, deps=deps, on_chunk=on_chunk)

    dm_harness.mp.setattr(runner_mod, "run_turn", spy)
    dm_harness.model_chooses_quiet("Sam asked me not to answer this one")
    result = await dm_harness.says("polly don't respond", who="Sam", channel=ROOM_CHANNEL,
                                   is_dm=False)

    assert captured["registry"].all_names() == ["web_search"]
    assert sorted(_tool_names(dm_harness.calls[0])) == ["stay_quiet", "web_search"]
    assert dm_harness.sent == [] and result.quiet is True
    assert dm_harness.registry.dispatched == []          # the owner's belt, never built for her


@pytest.mark.asyncio
async def test_quiet_without_a_reason_still_binds(dm_harness):
    dm_harness.model_chooses_quiet(None)
    result = await dm_harness.says("shh")
    assert dm_harness.sent == [] and result.quiet is True and result.quiet_reason == ""


# ── a call made beside the quiet choice still runs (ruling T2-2a) ──────────

class _StandInReminders:
    """A reminder tool on a REAL ToolRegistry, so the same-step call goes
    through the normal path (the registry and its approval hook)."""

    name = "create_reminder"
    description = "Set a reminder."
    input_schema = {"type": "object", "properties": {"when": {"type": "string"}}}
    requires_approval = True          # the dispatch-built approval hook is consulted
    owner_only = True

    def __init__(self, *, fail: bool = False) -> None:
        self.created: list[dict] = []
        self.fail = fail

    async def execute(self, input, ctx):
        from core.agent.tools import ToolFailure

        if self.fail:
            return ToolFailure("that time has already passed")
        self.created.append(dict(input))
        return f"Reminder set for {input.get('when')}."


class _StandInDelivery:
    name = "deliver_artifact"
    description = "Send a file."
    input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    requires_approval = False
    owner_only = True

    async def execute(self, input, ctx):
        ctx.deliveries.append(str(input["path"]))
        return "delivered"


def _real_registry(*tools):
    from core.agent.tools import ToolRegistry

    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


@pytest.mark.asyncio
async def test_a_reminder_asked_for_beside_the_quiet_choice_is_set_and_nothing_is_sent(dm_harness):
    """'remind me at 5 to call mum, no need to reply': the reminder is real
    work she was asked for — it runs through the registry, then her quiet
    choice stands with no further model call."""
    from core.agent.session import ToolResultBlock, ToolUseBlock

    reminders = _StandInReminders()
    dm_harness.registry = _real_registry(reminders)
    dm_harness.model_chooses_quiet("they only wanted the reminder", also=[
        ToolUseBlock(id="r1", name="create_reminder", input={"when": "17:00"})])
    result = await dm_harness.says("remind me at 5 to call mum, no need to reply")

    assert reminders.created == [{"when": "17:00"}]
    assert dm_harness.sent == [] and result.quiet is True
    assert len(dm_harness.calls) == 1
    row = dm_harness.quiet_record()[-1]
    assert row["reason"] == "they only wanted the reminder"
    assert row["excerpt"].startswith("remind me at 5")
    msgs = await dm_harness.transcript()
    results = {b.tool_use_id: b for m in msgs if m.role == "tool" for b in m.content
               if isinstance(b, ToolResultBlock)}
    assert results["r1"].content == "Reminder set for 17:00." and not results["r1"].is_error
    assert results["q1"].content == "You stayed quiet. Nothing was sent."


@pytest.mark.asyncio
async def test_a_file_sent_beside_the_quiet_choice_goes_out_with_no_words(dm_harness, tmp_path):
    """'send me the notes, don't reply', in one step: the file is delivered,
    no words go with it."""
    from core.agent.session import ToolUseBlock

    art = tmp_path / "notes.txt"
    art.write_text("x")
    dm_harness.registry = _real_registry(_StandInDelivery())
    dm_harness.model_chooses_quiet("they only wanted the file", also=[
        ToolUseBlock(id="d1", name="deliver_artifact", input={"path": str(art)})],
        text=["here you go"])
    result = await dm_harness.says("send me the notes, don't reply")

    assert result.quiet is True and result.response_text == ""
    assert result.attachments == (str(art),)
    assert dm_harness.sent == [""] and dm_harness.sent_attachments == [(str(art),)]
    assert len(dm_harness.calls) == 1


@pytest.mark.asyncio
async def test_a_second_stay_quiet_in_the_same_step_is_the_same_choice(dm_harness):
    """Final fix wave (ruling FW-5): the quiet call is the runner's, by name.
    A second stay_quiet in the same step never goes to the registry (where it
    would come back unknown and turn her choice into a "not yet"): it gets the
    same plain result, and the call beside them still runs."""
    from core.agent.session import ToolResultBlock, ToolUseBlock

    reminders = _StandInReminders()
    dm_harness.registry = _real_registry(reminders)
    dm_harness.model_chooses_quiet("they asked me not to reply", also=[
        ToolUseBlock(id="q0", name="stay_quiet", input={"reason": "they asked me not to reply"}),
        ToolUseBlock(id="r1", name="create_reminder", input={"when": "17:00"})])
    result = await dm_harness.says("remind me at 5, don't reply")

    assert result.quiet is True and dm_harness.sent == []
    assert len(dm_harness.calls) == 1
    assert reminders.created == [{"when": "17:00"}]
    msgs = await dm_harness.transcript()
    results = {b.tool_use_id: b for m in msgs if m.role == "tool" for b in m.content
               if isinstance(b, ToolResultBlock)}
    assert results["q0"].content == results["q1"].content == "You stayed quiet. Nothing was sent."
    assert not results["q0"].is_error and not results["q1"].is_error
    assert len(dm_harness.quiet_record()) == 1


@pytest.mark.asyncio
async def test_a_guests_second_stay_quiet_in_the_same_step_still_stands(dm_harness):
    """A guest has no registry: a second stay_quiet is still her one quiet
    choice, never a tool "not available" that makes her decide again."""
    from core.agent.session import ToolUseBlock

    dm_harness.model_chooses_quiet("Sam asked me not to", also=[
        ToolUseBlock(id="q0", name="stay_quiet", input={"reason": "Sam asked me not to"})])
    result = await dm_harness.says("polly don't answer", who="Sam", channel=ROOM_CHANNEL,
                                   is_dm=False)
    assert result.quiet is True and dm_harness.sent == []
    assert len(dm_harness.calls) == 1


@pytest.mark.asyncio
async def test_a_call_that_fails_beside_the_quiet_choice_makes_her_decide_again(dm_harness):
    """Something did not go through: the quiet call is answered "not yet",
    the run takes one more model step with the failure in view, and she can
    answer — nothing is lost silently."""
    from core.agent.session import ToolResultBlock, ToolUseBlock

    dm_harness.registry = _real_registry(_StandInReminders(fail=True))
    dm_harness.model_chooses_quiet("they only wanted the reminder", also=[
        ToolUseBlock(id="r1", name="create_reminder", input={"when": "yesterday"})])
    dm_harness.model_answers("I couldn't set that one: that time has already passed.")
    result = await dm_harness.says("remind me yesterday, no need to reply")

    assert len(dm_harness.calls) == 2
    assert dm_harness.sent == ["I couldn't set that one: that time has already passed."]
    assert result.quiet is False and dm_harness.quiet_record() == []
    seen = [b for m in dm_harness.calls[1]["messages"] for b in m.content
            if isinstance(b, ToolResultBlock)]
    by_id = {b.tool_use_id: b for b in seen}
    assert by_id["r1"].is_error
    assert by_id["q1"].content == (
        "Not yet — create_reminder didn't go through; decide again with that in view.")


@pytest.mark.asyncio
async def test_she_can_choose_quiet_again_after_a_not_yet(dm_harness):
    from core.agent.session import ToolUseBlock

    dm_harness.registry = _real_registry(_StandInReminders(fail=True))
    dm_harness.model_chooses_quiet("first try", also=[
        ToolUseBlock(id="r1", name="create_reminder", input={"when": "yesterday"})])
    dm_harness.model_chooses_quiet("they still asked me not to reply")
    result = await dm_harness.says("remind me yesterday, no need to reply")
    assert len(dm_harness.calls) == 2 and dm_harness.sent == []
    assert result.quiet is True and result.quiet_reason == "they still asked me not to reply"


@pytest.mark.asyncio
@pytest.mark.parametrize("first_look", [False, True])
async def test_a_guest_naming_a_tool_beside_the_quiet_choice_still_reaches_no_tool(
        monkeypatch, tmp_path, first_look):
    """A guest has no registry: a tool named beside the quiet choice does not
    exist there, so it is 'not available', she decides again, and quiet on
    its own then stands. (features.guest_web_search off, as shipped; the
    twin below has it on — R14, T8, moved deliberately.)"""
    from core.agent.session import ToolResultBlock, ToolUseBlock

    dm_harness = DmHarness(monkeypatch, tmp_path, first_look=first_look)

    dm_harness.model_chooses_quiet("Sam asked me not to", also=[
        ToolUseBlock(id="x1", name="local_shell", input={"command": "ls"})])
    dm_harness.model_chooses_quiet("Sam asked me not to")
    result = await dm_harness.says("polly don't answer", who="Sam", channel=ROOM_CHANNEL,
                                   is_dm=False)
    assert result.quiet is True and dm_harness.sent == []
    assert len(dm_harness.calls) == 2
    seen = [b for m in dm_harness.calls[1]["messages"] for b in m.content
            if isinstance(b, ToolResultBlock)]
    assert {b.tool_use_id: b.content for b in seen}["x1"] == \
        "Not available: you have no tools on this turn."


@pytest.mark.asyncio
@pytest.mark.parametrize("first_look", [False, True])
async def test_with_guest_search_on_a_tool_beside_the_quiet_choice_still_reaches_no_owner_tool(
        monkeypatch, tmp_path, first_look):
    """With features.guest_web_search on a guest's belt holds web_search only:
    an owner's tool named beside the quiet choice is not in it, so it does not
    run, she decides again, and quiet on its own then stands."""
    from core.agent.session import ToolResultBlock, ToolUseBlock

    dm_harness = DmHarness(monkeypatch, tmp_path, first_look=first_look, guest_search=True)
    dm_harness.model_chooses_quiet("Sam asked me not to", also=[
        ToolUseBlock(id="x1", name="local_shell", input={"command": "ls"})])
    dm_harness.model_chooses_quiet("Sam asked me not to")
    result = await dm_harness.says("polly don't answer", who="Sam", channel=ROOM_CHANNEL,
                                   is_dm=False)
    assert result.quiet is True and dm_harness.sent == []
    assert len(dm_harness.calls) == 2
    seen = {b.tool_use_id: b for m in dm_harness.calls[1]["messages"] for b in m.content
            if isinstance(b, ToolResultBlock)}
    assert seen["x1"].is_error is True
    assert seen["x1"].content == "Unknown tool: 'local_shell'. Available: web_search"
    assert dm_harness.registry.dispatched == [] and dm_harness.exa.search.await_count == 0


@pytest.mark.asyncio
async def test_a_delivery_made_earlier_in_a_quiet_run_still_goes_out_with_no_text(dm_harness, tmp_path):
    """Owner-asked deliveries are not replies (ruling T2-3, B#25): a file she
    delivered earlier in the run still goes out; no words go with it."""
    from core.agent.session import ToolResultBlock, ToolUseBlock

    art = tmp_path / "notes.txt"
    art.write_text("x")

    async def deliver(tool_use, ctx):
        ctx.deliveries.append(str(art))
        return ToolResultBlock(tool_use_id=tool_use.id, content="delivered")

    dm_harness.registry.dispatch = deliver
    dm_harness.model_calls(ToolUseBlock(id="d1", name="look_up", input={}),
                           text=["sending it now"])
    dm_harness.model_chooses_quiet("they only wanted the file")
    result = await dm_harness.says("send me the notes, no need to reply")

    assert result.quiet is True and result.response_text == ""
    assert result.attachments == (str(art),)
    assert dm_harness.sent == [""] and dm_harness.sent_attachments == [(str(art),)]


# ── what her record says afterwards ───────────────────────────────────────

@pytest.mark.asyncio
async def test_the_quiet_step_keeps_her_call_and_its_result_but_no_unsent_words(dm_harness):
    from core.agent.session import TextBlock, ToolResultBlock, ToolUseBlock

    dm_harness.model_chooses_quiet("they asked me not to", text=["🤐"])
    await dm_harness.says("don't reply to this")
    msgs = await dm_harness.transcript()
    assert [m.role for m in msgs] == ["user", "assistant", "tool"]
    assert not any(isinstance(b, TextBlock) for b in msgs[1].content)   # the emoji never happened
    assert [b.name for b in msgs[1].content if isinstance(b, ToolUseBlock)] == ["stay_quiet"]
    assert isinstance(msgs[2].content[0], ToolResultBlock) and not msgs[2].content[0].is_error


@pytest.mark.asyncio
async def test_her_next_reply_remembers_she_chose_quiet(dm_harness):
    from core.agent.session import RECEIPT_MARK

    dm_harness.model_chooses_quiet("they asked me not to")
    await dm_harness.says("don't reply to this")
    dm_harness.model_answers("hey!")
    await dm_harness.says("ok you can talk now")
    payload = "\n".join(
        b.text for m in dm_harness.calls[-1]["messages"] for b in m.content
        if hasattr(b, "text"))
    assert RECEIPT_MARK in payload and "stay_quiet(" in payload


# ── a quiet choice counts as answered ─────────────────────────────────────

@pytest.mark.asyncio
async def test_a_quiet_run_does_not_requeue_the_steers_it_drained(tmp_path, monkeypatch):
    """Ruling T2-3 (B#5): `_answered_by_run` is true when quiet — a drained
    steer is not handed back to the sweep to be asked again."""
    import core.agent.llm_client as llm_mod
    from core.agent import quiet_choice, steering
    from core.agent.runner import RunnerDeps, _derive_session_key, run_turn
    from core.agent.session import ToolUseBlock
    from core.agent.steering import SteerBundle

    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    h = DmHarness.__new__(DmHarness)   # only the turn builder is needed
    turn = DmHarness.turn(h, "first")
    key = _derive_session_key(turn)
    steering.clear(key)
    steering.enqueue(key, SteerBundle(turn=DmHarness.turn(h, "and also this")))

    async def fake_call(**kw):
        return llm_mod.LLMResponse(text_blocks=[], tool_uses=[ToolUseBlock(
            id="q", name="stay_quiet", input={"reason": "nothing to add"})],
            stop_reason="tool_use", prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)

    async def recall(t):
        return SimpleNamespace(turn=t, user_text=t.content, recent_history_text="",
                               identity_core_text="I'm PaLLi.", is_owner=True)

    async def cadence(prompt):
        return vote("answer")

    deps = RunnerDeps(build_turn_context=recall, cadence_llm=cadence, sessions_root=tmp_path,
                      provider="openai_compat", model="m", client=None, tool_registry=None,
                      polling_tools=set(), is_owner=True)
    try:
        result = await run_turn(turn, deps=deps)
        assert result.quiet is True
        assert steering.peek(key) == []
    finally:
        steering.clear(key)


@pytest.mark.asyncio
async def test_a_quiet_talk_sidecar_tells_its_bundle_it_was_answered(tmp_path):
    """Ruling T2-3: a quiet talk sidecar hands back an answered bundle, not an
    unreplied one, so nothing re-asks it — and nothing is delivered."""
    from core.agent.sidecar import run_sidecar
    from core.turn.types import TurnResult

    delivered: list[Any] = []
    done: list[tuple[str, dict]] = []

    async def dispatch(turn):
        return TurnResult(responded=False, response_text="", decision=None,
                          reflection_applied=False, delegation_tier_used=0,
                          quiet=True, quiet_reason="nothing to add")

    async def deliver(result):
        delivered.append(result)

    async def on_done(reply, **kw):
        done.append((reply, kw))

    h = DmHarness.__new__(DmHarness)
    handle = run_sidecar(parent_key=f"discord:{DM_CHANNEL}", turn=DmHarness.turn(h, "ok"),
                         kind="talk", dispatch=dispatch, deliver=deliver,
                         sessions_root=tmp_path, on_done=on_done)
    await handle
    assert delivered == [] and done == [("", {"quiet": True})]


@pytest.mark.asyncio
async def test_a_quiet_bundle_is_answered_in_hand_for_the_sweep_and_the_watchdog(tmp_path):
    from bots.common import steering as fork
    from core.agent import steering
    from core.agent.session import Session, TextBlock
    from core.agent.steering import SteerBundle

    h = DmHarness.__new__(DmHarness)
    bundle = SteerBundle(turn=DmHarness.turn(h, "never mind, carry on"), quiet=True)
    assert fork._answered_in_hand(bundle) is True

    key = f"discord:{DM_CHANNEL}"
    steering.clear(key)
    steering.enqueue(key, bundle)
    dispatched: list[Any] = []

    async def dispatch(turn):
        dispatched.append(turn)

    n = await fork.sweep_after_run(key, dispatch=dispatch, sessions_root=tmp_path)
    assert n == 0 and dispatched == []
    s = await Session.open_or_create(key, root=tmp_path)
    texts = [b.text for m in s.messages for b in m.content if isinstance(b, TextBlock)]
    assert texts == ["never mind, carry on"]      # on the record, never answered twice


@pytest.mark.asyncio
async def test_a_drained_quiet_bundle_is_not_owed_a_reply_by_the_run(tmp_path, monkeypatch):
    import core.agent.llm_client as llm_mod
    from core.agent import steering
    from core.agent.runner import RunnerDeps, _derive_session_key, run_turn
    from core.agent.steering import SteerBundle

    h = DmHarness.__new__(DmHarness)
    turn = DmHarness.turn(h, "first")
    key = _derive_session_key(turn)
    steering.clear(key)
    steering.enqueue(key, SteerBundle(turn=DmHarness.turn(h, "carry on"), quiet=True))

    async def fake_call(**kw):
        return llm_mod.LLMResponse(text_blocks=[], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=0)

    monkeypatch.setattr(llm_mod, "call", fake_call)

    async def recall(t):
        return SimpleNamespace(turn=t, user_text=t.content, recent_history_text="",
                               identity_core_text="I'm PaLLi.", is_owner=True)

    async def cadence(prompt):
        return vote("answer")

    deps = RunnerDeps(build_turn_context=recall, cadence_llm=cadence, sessions_root=tmp_path,
                      provider="openai_compat", model="m", client=None, tool_registry=None,
                      polling_tools=set(), is_owner=True)
    try:
        await run_turn(turn, deps=deps)
        assert steering.peek(key) == []           # answered by her quiet, not owed again
    finally:
        steering.clear(key)


# ── every door ─────────────────────────────────────────────────────────────

@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_dont_reply_on_a_telegram_dm_sends_nothing_and_keeps_the_message(dm_harness, tmp_path):
    from bots.common.guest_gate import GuestGate
    from bots.telegram.client import PalliTelegramClient
    from tests.bots.test_telegram_client import (
        FakeApplication, FakeHandlerFactory, FakeMessage, FakeUpdate,
    )
    from tests.agent.she_decides_harness import OWNER_TELEGRAM_ID

    app = FakeApplication()
    client = PalliTelegramClient(application=app, bot_user_id=999,
                                 bot_username="palli_test_bot", owner_id=OWNER_APP_ID,
                                 guest_gate=GuestGate(), sessions_root=dm_harness.sessions)
    client._handler_factory = FakeHandlerFactory

    async def owner_tg_id():
        return OWNER_TELEGRAM_ID

    client._owner_telegram_id = owner_tg_id
    dm_harness.model_chooses_quiet("they asked me not to", text=["👍"])
    chat = 31337
    await client.on_message(FakeUpdate(FakeMessage(text="do not respond", chat_id=chat,
                                                   author_id=OWNER_TELEGRAM_ID)))
    await client._fire_burst(chat)

    assert app.bot.messages == []
    assert await dm_harness.transcript_has("do not respond", channel=chat,
                                           surface="telegram_text")
    rows = dm_harness.quiet_record()
    assert rows[-1]["door"] == "telegram_text" and rows[-1]["conversation"] == f"telegram:{chat}"


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_dont_reply_on_the_dashboard_shows_no_bubble_and_keeps_the_message(dm_harness):
    import httpx
    from fastapi import FastAPI

    from api import chat as chat_mod

    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api/chat")

    async def owner(request):
        return {"id": OWNER_APP_ID}

    async def registered():
        return None

    async def owner_id():
        return OWNER_APP_ID

    dm_harness.mp.setattr(chat_mod, "_require_user", owner)
    dm_harness.mp.setattr(chat_mod, "require_csrf", lambda request: None)
    dm_harness.mp.setattr(chat_mod, "_ensure_dashboard_adapter_registered", registered)
    dm_harness.mp.setattr(chat_mod, "_resolve_local_owner_id", owner_id)
    dm_harness.model_chooses_quiet("they asked me not to", text=["🙂"])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://testserver") as http:
        response = await http.post("/api/chat/message", json={"message": "do not respond"})

    body = response.json()
    assert response.status_code == 200
    assert body["quiet"] is True and body["response"] == "" and body["beats"] == []
    assert await dm_harness.transcript_has("do not respond", channel=None, surface="dashboard")


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_an_ordinary_dashboard_reply_says_it_was_not_quiet(dm_harness):
    import httpx
    from fastapi import FastAPI

    from api import chat as chat_mod

    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api/chat")

    async def owner(request):
        return {"id": OWNER_APP_ID}

    async def nothing():
        return None

    async def owner_id():
        return OWNER_APP_ID

    dm_harness.mp.setattr(chat_mod, "_require_user", owner)
    dm_harness.mp.setattr(chat_mod, "require_csrf", lambda request: None)
    dm_harness.mp.setattr(chat_mod, "_ensure_dashboard_adapter_registered", nothing)
    dm_harness.mp.setattr(chat_mod, "_resolve_local_owner_id", owner_id)
    dm_harness.model_answers("hello!")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://testserver") as http:
        body = (await http.post("/api/chat/message", json={"message": "hi"})).json()
    assert body["quiet"] is False and body["response"] == "hello!"


def test_the_door_page_draws_no_trouble_bubble_for_a_quiet_choice():
    """The page renders nothing when `quiet` is set — never "She answered with
    nothing at all" (ruling T2-3). A source pin: the website has no test runner
    in this repo."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "website" / "src"
    panel = (root / "components" / "door" / "ChatPanel.tsx").read_text(encoding="utf-8")
    setup = (root / "lib" / "setup.ts").read_text(encoding="utf-8")
    assert "quiet: boolean" in setup and "body.quiet === true" in setup
    trouble_at = panel.index("She answered with nothing at all")
    guard = panel.rindex("if (", 0, trouble_at)
    assert "reply.quiet" in panel[guard:trouble_at]


# ── what is offered where (ruling T2-2, T2a-2) ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("origin,surface", [
    ("reminder", "reminder_fire"),
    ("workbench", "workbench"),
    ("monologue", "proactive"),
])
async def test_her_own_runs_are_never_offered_quiet_and_keep_todays_vote(
        tmp_path, monkeypatch, origin, surface):
    import core.agent.llm_client as llm_mod
    from core.agent import quiet_choice
    from core.agent.cadence import SHAPE_RESPONSE_PROMPT
    from core.agent.runner import SILENCE_NOTE, RunnerDeps, run_turn
    from core.turn.types import Turn, TurnMetadata

    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    seen: list[dict] = []
    prompts: list[str] = []

    async def fake_call(**kw):
        seen.append(kw)
        return llm_mod.LLMResponse(text_blocks=["done"], tool_uses=[], stop_reason="end_turn",
                                   prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_mod, "call", fake_call)

    async def recall(t):
        return SimpleNamespace(turn=t, user_text=t.content, recent_history_text="",
                               identity_core_text="I'm PaLLi.", is_owner=True)

    async def cadence(prompt):
        prompts.append(prompt)
        return json.dumps({"should_respond": False, "register": "neutral", "length": "short",
                           "phase": "settled_conversation", "target_beats": 1,
                           "observation_text": "x"})

    turn = Turn(surface=surface, owner_id=OWNER_APP_ID, content="[REMINDER FIRE] stretch",
                metadata=TurnMetadata(origin=origin),  # type: ignore[arg-type]
                received_at=datetime(2026, 10, 1, 9, 0, 0))
    deps = RunnerDeps(build_turn_context=recall, cadence_llm=cadence, sessions_root=tmp_path,
                      provider="openai_compat", model="m", client=None, tool_registry=None,
                      polling_tools=set(), is_owner=True)
    result = await run_turn(turn, deps=deps)

    expected_prompt = SHAPE_RESPONSE_PROMPT.replace("<<USER_TEXT>>", "[REMINDER FIRE] stretch") \
        .replace("<<RECENT_HISTORY>>", "(no prior context)")
    assert prompts[0].startswith(expected_prompt)          # today's vote text, verbatim
    if origin == "monologue":
        assert seen == [] and result.responded is False   # her own impulse: the vote still decides
        return
    assert _tool_names(seen[0]) == []
    assert SILENCE_NOTE in (seen[0].get("trailing_system_note") or "")
    assert "silence is the reply they asked for" in seen[0]["system"]   # today's sentence


@pytest.mark.asyncio
async def test_a_forge_session_is_never_offered_quiet(tmp_path, monkeypatch):
    from core.agent import quiet_choice
    from core.turn.types import Turn, TurnMetadata

    monkeypatch.setattr(quiet_choice, "she_decides_enabled", lambda: True)
    forge = Turn(surface="workbench", owner_id=1, content="build it",
                 metadata=TurnMetadata(origin="workbench", extra={}),
                 received_at=datetime(2026, 10, 1, 9, 0, 0))
    forge_origin = SimpleNamespace(surface="workbench", metadata=SimpleNamespace(origin="forge"))
    assert quiet_choice.offered(forge) is False and quiet_choice.offered(forge_origin) is False
    for origin in ("user", "dashboard_user", "guest"):
        t = SimpleNamespace(surface="discord_text", metadata=SimpleNamespace(origin=origin))
        assert quiet_choice.offered(t) is True
    assert quiet_choice.offered(SimpleNamespace(surface="discord_text", metadata=None)) is True


def test_with_the_switch_off_nothing_is_offered(monkeypatch):
    from core.agent import quiet_choice

    t = SimpleNamespace(surface="discord_text", metadata=SimpleNamespace(origin="user"))
    assert quiet_choice.offered(t) is False          # the conftest pin holds it off


# ── the quick vote's text and its answer (ruling T2a-1) ───────────────────

def test_the_participant_vote_text_frames_an_ai_that_knows_it_and_drops_the_always_rule():
    from core.agent.cadence import PARTICIPANT_SHAPE_PROMPT, SHAPE_RESPONSE_PROMPT

    p = PARTICIPANT_SHAPE_PROMPT
    assert "ALWAYS true" not in p and "should_respond" not in p
    assert "participant who knows she is an AI" in p
    assert "a person" not in p.lower()
    for word in ('"answer"', '"wait"', '"stay_quiet"', '"decision"', '"reason"'):
        assert word in p
    assert "only that guest's own messages" in p
    assert "real safety reason" in p
    # The shaping half is today's text, unchanged, but for item 6, the
    # observation: in the participant vote it describes and never guesses
    # (step 2, part 6; the who-is-talking plan, A5).
    s = SHAPE_RESPONSE_PROMPT
    assert s[s.index("2. register:"):s.index("6. observation_text:")] in p
    assert s[s.index("7. emotional_context:"):s.index("Return ONLY")] in p
    assert "never guess facts, answers," in p
    assert "<<USER_TEXT>>" in p and "<<RECENT_HISTORY>>" in p


def test_the_vote_text_is_participant_framed_only_when_asked():
    from core.agent import cadence

    prompts: list[str] = []

    async def llm(prompt):
        prompts.append(prompt)
        return vote("answer")

    ctx = SimpleNamespace(user_text="hi", recent_history_text="")
    asyncio.run(cadence.shape_response(ctx, llm=llm))
    asyncio.run(cadence.shape_response(ctx, llm=llm, participant=True))
    assert prompts[0].startswith(cadence.SHAPE_RESPONSE_PROMPT.split("<<RECENT_HISTORY>>")[0])
    assert prompts[1].startswith(cadence.PARTICIPANT_SHAPE_PROMPT.split("<<RECENT_HISTORY>>")[0])


@pytest.mark.parametrize("body,decision,should_respond", [
    ({"decision": "answer"}, "answer", True),
    ({"decision": "wait"}, "wait", False),
    ({"decision": "stay_quiet", "reason": "not mine"}, "stay_quiet", False),
    ({"decision": " Stay_Quiet "}, "stay_quiet", False),
    ({"should_respond": True}, "answer", True),            # an old fixture: derived
    ({"should_respond": False}, "stay_quiet", False),
    ({"decision": "maybe", "should_respond": False}, "stay_quiet", False),
    ({}, "answer", True),
])
def test_the_vote_parser_derives_the_decision(body, decision, should_respond):
    from core.agent import cadence

    async def llm(prompt):
        return json.dumps(body)

    cfg = asyncio.run(cadence.shape_response(SimpleNamespace(user_text="x"), llm=llm))
    assert cfg.decision == decision and cfg.should_respond is should_respond
    assert cfg.reason == str(body.get("reason", ""))


def test_a_failed_vote_is_an_answer():
    from core.agent import cadence

    async def broken(prompt):
        raise RuntimeError("down")

    cfg = asyncio.run(cadence.shape_response(SimpleNamespace(user_text="x"), llm=broken,
                                             participant=True))
    assert cfg.decision == "answer" and cfg.should_respond is True


# ── the reply call's instruction (ruling T2a-2) ───────────────────────────

def test_the_reply_instruction_replaces_the_silence_sentence_only_when_quiet_is_offered():
    from core.agent import system_prompt as sp

    off = sp._texting_voice_block()
    assert off.endswith("an acknowledgment is a response; silence is the reply they asked for.")
    on = sp._texting_voice_block(quiet_choice=True)
    head = off[: off.index(" And when they ask me not to respond")]
    assert on.startswith(head)
    assert "silence is the reply they asked for" not in on
    tail = on[len(head):]
    assert sp.QUIET_CHOICE_INSTRUCTION in tail
    assert "participant who knows I'm an AI" in tail     # as her since the fix wave (F2)
    assert "`stay_quiet`" in tail and "one-line reason" in tail
    assert "only that guest's own messages" in tail and "real safety reason" in tail
    assert "a person" not in tail.lower()
    # The whole prompt: the same switch, nothing else moves.
    ctx = SimpleNamespace(identity_core_text="I'm PaLLi.")
    stable_off, tail_off = sp.build_system_prompt_parts(ctx, cadence_directive="d")
    stable_on, tail_on = sp.build_system_prompt_parts(ctx, cadence_directive="d", quiet_choice=True)
    assert tail_on == tail_off and stable_on == stable_off.replace(off, on)


@pytest.mark.asyncio
async def test_the_reply_call_carries_the_instruction_on_a_human_turn(dm_harness):
    from core.agent import system_prompt as sp

    dm_harness.model_answers("hi")
    await dm_harness.says("hey")
    assert sp.QUIET_CHOICE_INSTRUCTION in dm_harness.calls[0]["system"]
    assert "silence is the reply they asked for" not in dm_harness.calls[0]["system"]


def test_the_control_calls_description_is_plain_and_framed_right():
    from core.agent import quiet_choice

    d = quiet_choice.STAY_QUIET_SCHEMA["description"]
    assert quiet_choice.STAY_QUIET_SCHEMA["name"] == "stay_quiet"
    assert "nothing is sent" in d.lower() and "a person" not in d.lower()
    reason = quiet_choice.STAY_QUIET_SCHEMA["input_schema"]["properties"]["reason"]
    assert reason["type"] == "string"


def test_the_new_prompt_texts_avoid_the_plain_words_family():
    from core.agent import quiet_choice
    from core.agent import system_prompt as sp
    from core.agent.cadence import PARTICIPANT_SHAPE_PROMPT
    from tests.prompts.test_plain_words import PHRASE_FAMILY

    new_cadence = PARTICIPANT_SHAPE_PROMPT[: PARTICIPANT_SHAPE_PROMPT.index("2. register:")]
    for text in (new_cadence, sp.QUIET_CHOICE_INSTRUCTION,
                 json.dumps(quiet_choice.STAY_QUIET_SCHEMA)):
        assert not PHRASE_FAMILY.search(text), text
