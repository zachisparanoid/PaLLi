"""Two cost floors on a run that is not moving (second read F2 + F3, 2026-10-04).

DEFECT §56's class, run side: a tool whose answer never changes, and a model
that does not take the taught finish, cost up to the 50-step safety limit —
about fifty full reply calls — and ended with no reply, no record and
nothing on the door. Two brakes, both cost and safety floors, never a
decision about whether she answers:

- F2: the "not yet — decide again" re-decision beside a quiet choice has a
  budget. Two consecutive "not yet"s for the same failing tool, then the
  quiet call is answered "keeps failing — tell them or stay quiet, don't try
  again", and her next stay_quiet ends the run.
- F3: when the same tool round gives the same answer three times in a row,
  the tool rounds are over for this reply: the next step is a reply (no
  work tool on offer; the quiet choice stays hers where it is offered), and
  a tool call made anyway is not run and ends the run. Different tools, or
  the same tool with different answers, keep going.
- F3, second half: while voice replies are on in this chat, ``voice_note`` is
  not on her tool list at all.

Every test drives the REAL spine (dispatch_turn → run_turn) with a fake model
that never takes the taught finish — the live model's 10-01 behaviour.
Stand-in names only.
"""
from __future__ import annotations

from typing import Any

import pytest

import core.agent.builtin_tools.her_hands as hh
from core.agent.session import ToolResultBlock, ToolUseBlock
from core.agent.tools import ToolRegistry
from tests.agent import voice_lane_fakes as lane
from tests.agent.she_decides_harness import DM_CHANNEL, OWNER_APP_ID, DmHarness, Model

_UTCNOW_DEBT = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)

# "Within a few steps": the brakes' own arithmetic is at most four model
# calls (three rounds, then the reply step); five leaves room for the promise
# gate's one extra round. The old bound was the 50-step limit plus one.
A_FEW = 5


class FailingRegistry:
    """The owner's belt reduced to one tool that always fails — with the same
    answer every time, or (``same=False``) a different one each time."""

    def __init__(self, *, same: bool = True) -> None:
        self.dispatched: list[str] = []
        self.same = same

    def all_names(self) -> list[str]:
        return ["create_reminder"]

    def schemas_for(self, ctx: Any) -> list[dict[str, Any]]:
        return [{"name": "create_reminder", "description": "Set a reminder.",
                 "input_schema": {"type": "object", "properties": {}}}]

    def set_approval_hook(self, hook: Any) -> None:
        pass

    async def dispatch(self, tool_use: Any, ctx: Any) -> Any:
        self.dispatched.append(tool_use.name)
        text = ("reminder service is down" if self.same
                else f"reminder service is down (attempt {len(self.dispatched)})")
        return ToolResultBlock(tool_use_id=tool_use.id, content=text, is_error=True)


class ManyToolsRegistry:
    """Ten different tools (tool0..tool9), or one tool whose answer changes
    every call — ordinary multi-tool work."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.dispatched: list[str] = []

    def all_names(self) -> list[str]:
        return sorted(set(self.names))

    def schemas_for(self, ctx: Any) -> list[dict[str, Any]]:
        return [{"name": n, "description": "A tool.",
                 "input_schema": {"type": "object", "properties": {}}}
                for n in sorted(set(self.names))]

    def set_approval_hook(self, hook: Any) -> None:
        pass

    async def dispatch(self, tool_use: Any, ctx: Any) -> Any:
        self.dispatched.append(tool_use.name)
        return ToolResultBlock(tool_use_id=tool_use.id,
                               content=f"{tool_use.name} answer #{len(self.dispatched)}")


class QuietPlusFailingTool(Model):
    """stay_quiet plus a reminder, every step, whatever it is told."""

    def __init__(self) -> None:
        super().__init__()
        self.n = 0

    def next(self) -> Any:
        self.n += 1
        return ("quiet", "they said no need to reply",
                [ToolUseBlock(id=f"r{self.n}", name="create_reminder",
                              input={"when": "5pm", "what": "call mum"})], [])


class VoiceNoteEveryStep(Model):
    """voice_note, reworded, every step, whatever it is told."""

    def __init__(self) -> None:
        super().__init__()
        self.n = 0

    def next(self) -> Any:
        self.n += 1
        return ("tool", ToolUseBlock(id=f"v{self.n}", name="voice_note",
                                     input={"text": f"the answer, worded {self.n}"}), [])


class Scripted(Model):
    """Each tool in ``names`` once, in order, then a reply."""

    def __init__(self, names: list[str], reply: str = "all done") -> None:
        super().__init__()
        self.plan = [("tool", ToolUseBlock(id=f"c{i}", name=n, input={"i": i}), [])
                     for i, n in enumerate(names)] + [("text", reply)]


def _voice_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool in hh.build_her_hands_tools():
        if tool.name in ("voice_note", "voice_replies"):
            reg.register(tool)
    return reg


def _tool_names(call: dict[str, Any]) -> list[str]:
    return [t["name"] for t in (call.get("tools") or [])]


async def _tool_results(h: DmHarness) -> dict[str, ToolResultBlock]:
    return {b.tool_use_id: b for m in await h.transcript() if m.role == "tool"
            for b in m.content if isinstance(b, ToolResultBlock)}


# ── F2: the "not yet" re-decision has a budget ────────────────────────────

@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_tool_failing_beside_every_quiet_choice_ends_within_a_few_steps(
        monkeypatch, tmp_path):
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = FailingRegistry()
    h.model = QuietPlusFailingTool()

    result = await h.says("remind me at 5 to call mum, no need to reply")

    assert len(h.calls) <= A_FEW
    assert result.terminated_by == "quiet" and result.quiet is True
    assert h.sent == []
    rows = h.quiet_record()
    assert len(rows) == 1 and rows[0]["reason"] == "they said no need to reply"


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_two_not_yets_then_keeps_failing_then_her_next_quiet_ends_it(monkeypatch, tmp_path):
    """The budget alone, with answers that differ each time (so the general
    brake never fires): "not yet", "not yet", "keeps failing", then her next
    stay_quiet stands even though the reminder failed beside it again."""
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = FailingRegistry(same=False)
    h.model = QuietPlusFailingTool()

    result = await h.says("remind me at 5 to call mum, no need to reply")

    assert len(h.calls) == 4
    assert result.terminated_by == "quiet" and result.quiet is True
    results = await _tool_results(h)
    quiet = [results[f"q{i}"].content for i in range(1, 5)]
    assert quiet[0].startswith("Not yet — create_reminder didn't go through")
    assert quiet[1].startswith("Not yet — create_reminder didn't go through")
    assert "keeps failing" in quiet[2] and "tell them" in quiet[2]
    assert "stay quiet" in quiet[2] and "Don't try it again" in quiet[2]
    assert quiet[3] == "You stayed quiet. Nothing was sent."
    assert len(h.quiet_record()) == 1


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_after_keeps_failing_she_can_still_tell_them(monkeypatch, tmp_path):
    from core.agent.session import ToolUseBlock as TU

    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = FailingRegistry(same=False)
    for i in range(3):
        h.model_chooses_quiet("no reply wanted", also=[
            TU(id=f"r{i}", name="create_reminder", input={"when": "5pm"})])
    h.model_answers("I couldn't set the reminder — the reminder service is down.")

    result = await h.says("remind me at 5, no need to reply")

    assert len(h.calls) == 4
    assert result.quiet is False and h.quiet_record() == []
    assert h.sent == ["I couldn't set the reminder — the reminder service is down."]


# ── F3: a run that is not moving stops offering tool rounds ───────────────

@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_voice_note_every_step_with_voice_replies_on_ends_within_a_few_steps(
        monkeypatch, tmp_path):
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = _voice_registry()
    h.model = VoiceNoteEveryStep()
    vr.set_on(f"discord:{DM_CHANNEL}", True)

    result = await h.dispatch(h.turn("what were the numbers?"))

    assert len(h.calls) <= A_FEW
    assert tts.calls == [] and result.voice_notes == ()
    # The switch is on here: voice_note is not on her belt for this turn.
    assert "voice_note" not in _tool_names(h.calls[0])
    assert "voice_replies" in _tool_names(h.calls[0])


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_voice_note_every_step_with_voice_replies_off_ends_within_a_few_steps(
        monkeypatch, tmp_path):
    from bots.common.voice_note import plan_voice_reply

    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = _voice_registry()
    h.model = VoiceNoteEveryStep()

    result = await h.dispatch(h.turn("send me a voice message with the numbers"))
    plan = await plan_voice_reply("discord", DM_CHANNEL, result, owner_id=OWNER_APP_ID)

    assert len(h.calls) <= A_FEW
    assert "voice_note" in _tool_names(h.calls[0])       # off here: on her belt
    assert len(tts.calls) == 1 and len(result.voice_notes) == 1
    assert plan is not None and len(plan.notes) == 1


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_the_step_after_the_brake_offers_no_work_tool(monkeypatch, tmp_path):
    """Quiet offered: the braked step's only tool is the quiet choice (hers
    to make), and the trailing note says why. A call made anyway is not run."""
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.model = Model(plan=[("tool", ToolUseBlock(id=f"l{i}", name="look_up", input={}), [])
                          for i in range(5)])

    result = await h.dispatch(h.turn("look it up"))

    assert len(h.calls) == 4 and h.registry.dispatched == ["look_up"] * 3
    assert _tool_names(h.calls[2]) == ["look_up", "stay_quiet"]
    assert _tool_names(h.calls[3]) == ["stay_quiet"]
    note = str(h.calls[3].get("trailing_system_note") or "")
    assert "look_up" in note and "same answer 3 times" in note
    assert result.terminated_by == "tool_brake"
    results = await _tool_results(h)
    assert results["l3"].is_error and results["l3"].content.startswith("Not run")


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_without_the_quiet_choice_the_braked_step_is_asked_for_text(monkeypatch, tmp_path):
    """Quiet not offered (the switch off): the tool list stays as it was (the
    provider's prompt cache keeps it) and the call asks for no tool."""
    h = DmHarness(monkeypatch, tmp_path, switch_on=False)
    h.model = Model(plan=[("tool", ToolUseBlock(id=f"l{i}", name="look_up", input={}), [])
                          for i in range(3)] + [("text", "it's 42")])

    result = await h.dispatch(h.turn("look it up"))

    assert len(h.calls) == 4
    assert all("tool_choice" not in c for c in h.calls[:3])
    assert h.calls[3]["tool_choice"] == "none"
    assert _tool_names(h.calls[3]) == _tool_names(h.calls[0])
    assert result.response_text == "it's 42" and result.terminated_by == "natural"


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_reply_on_the_braked_step_goes_out_as_usual(monkeypatch, tmp_path):
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.model = Model(plan=[("tool", ToolUseBlock(id=f"l{i}", name="look_up", input={}), [])
                          for i in range(3)] + [("text", "found it: it's 42")])

    result = await h.says("look it up")

    assert len(h.calls) == 4
    assert h.sent == ["found it: it's 42"] and result.terminated_by == "natural"


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_two_identical_answers_are_not_a_brake(monkeypatch, tmp_path):
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.model = Model(plan=[("tool", ToolUseBlock(id=f"l{i}", name="look_up", input={}), [])
                          for i in range(2)] + [("text", "done")])

    result = await h.dispatch(h.turn("look it up twice"))

    assert len(h.calls) == 3 and h.registry.dispatched == ["look_up"] * 2
    assert _tool_names(h.calls[2]) == ["look_up", "stay_quiet"]
    assert "tool_choice" not in h.calls[2]
    assert result.response_text == "done"


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_run_of_ten_different_tools_is_untouched(monkeypatch, tmp_path):
    names = [f"tool{i}" for i in range(10)]
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = ManyToolsRegistry(names)
    h.model = Scripted(names)

    result = await h.dispatch(h.turn("do the ten things"))

    assert len(h.calls) == 11 and h.registry.dispatched == names
    assert all("tool_choice" not in c for c in h.calls)
    assert all(set(names) <= set(_tool_names(c)) for c in h.calls)
    assert result.response_text == "all done" and result.terminated_by == "natural"


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_the_same_tool_with_a_new_answer_each_time_keeps_going(monkeypatch, tmp_path):
    names = ["check"] * 10
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = ManyToolsRegistry(names)
    h.model = Scripted(names)

    result = await h.dispatch(h.turn("check it ten times"))

    assert len(h.calls) == 11 and h.registry.dispatched == names
    assert result.response_text == "all done" and result.terminated_by == "natural"
