"""The voice message, made last (R15, memory that reaches, Group V).

On 10-05 PaLLi sent a voice note made early in a reply, which was right, and
a text written after the checks, which was wrong: ``voice_note`` made the
note the moment it was called. Now the tool only notes the words (the last
call wins), the runner makes the one note when the reply is finished
(``core.agent.voice_note_final.finish``), and every later round's trailing
note shows PaLLi what the note will say (item 36), so it can replace it.

The words are Fable's items 30-36, Draft 2, approved by the owner on 10-13,
pinned here verbatim. The rulings that still hold, each with a test: FW-3 (a
quiet choice beside pending words still makes the note), F3 (with voice
replies on in the chat the tool is not offered), DEFECT §56 (a run of
voice_note calls makes one note and ends within a few steps),
``MAX_VOICE_NOTES_PER_REPLY`` stays 1, and both doors read
``result.voice_notes`` as before.

Nothing here reaches a real TTS: the speech facade and ffmpeg are fakes at
their own modules (tests/agent/voice_lane_fakes.py), or ``finish`` is handed
a fake synthesizer. Stand-in names only: Robin is the owner.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import pytest

import core.agent.builtin_tools.her_hands as hh
from core.agent import voice_note_final as vnf
from core.agent.session import TextBlock, ToolResultBlock, ToolUseBlock
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext, ToolRegistry
from tests.agent import voice_lane_fakes as lane
from tests.agent.she_decides_harness import DM_CHANNEL, OWNER_APP_ID, DmHarness, Model

_UTCNOW_DEBT = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)

# ── Draft 2, Part C, items 30-36, verbatim ────────────────────────────────
ITEM_30 = ("Send a voice message in your own voice in this chat: an audio message on "
           "Discord, a voice note on Telegram.")
ITEM_31 = ("One per reply: the words you give are spoken once your reply is finished, so "
           "they say what you concluded; if you change your mind before you finish, call it "
           "again with the new words and the last call wins.")
ITEM_32 = "Your text reply, if you write one, goes beside it as a text message."
ITEM_33 = ("Use it when they ask for a voice message. While voice replies are on here you "
           "don't need it: just write your reply and it is spoken.")
ITEM_34 = ('Voice message noted; it will say: "{words}". It is made when you finish, after '
           "your text, so the two agree. Finish now: no text needed, or only a short line if "
           "there's a link, a code or a number to read.")
ITEM_35 = 'Replaced; your voice message will now say: "{words}". Finish now.'
ITEM_36 = ('(Your voice message will say: "{words}". Call voice_note again if what you\'ve '
           "found changes it.)")

# The brakes' bound (tests/agent/test_run_brakes.py): at most four model
# calls of tool rounds, then the reply step.
A_FEW = 5


def _ctx(surface: str = "discord_text", channel_id: int = 201) -> ToolContext:
    return ToolContext(turn_owner_id=OWNER_APP_ID, is_owner=True, surface=surface,
                       channel_id=channel_id)


def _tool() -> Any:
    return hh.VoiceNoteTool()


def _run(coro):
    return asyncio.run(coro)


class FakeSynth:
    """``finish``'s synthesizer seam: records the words, returns bytes."""

    def __init__(self, audio: bytes | None = lane.OGG, *, boom: Exception | None = None):
        self.audio = audio
        self.boom = boom
        self.words: list[str] = []

    async def __call__(self, text: str) -> bytes | None:
        self.words.append(text)
        if self.boom is not None:
            raise self.boom
        return self.audio


@pytest.fixture
def voice_on(monkeypatch):
    lane.voice_notes_on(monkeypatch)


# ── the words ─────────────────────────────────────────────────────────────

def test_the_description_is_items_30_to_33():
    assert hh.VoiceNoteTool.description == " ".join([ITEM_30, ITEM_31, ITEM_32, ITEM_33])


def test_the_first_call_notes_the_words_and_answers_item_34(voice_on, monkeypatch):
    tts, _ = lane.install(monkeypatch)
    ctx = _ctx()

    result = _run(_tool().execute({"text": "the numbers are 4 8 15"}, ctx))

    assert result == ITEM_34.format(words="the numbers are 4 8 15")
    assert ctx.voice_note_words == ["the numbers are 4 8 15"]
    assert ctx.voice_notes == []                         # made last, never here
    assert tts.calls == []


def test_a_second_call_replaces_the_words_and_answers_item_35(voice_on, monkeypatch):
    tts, _ = lane.install(monkeypatch)
    ctx = _ctx()
    tool = _tool()

    _run(tool.execute({"text": "the numbers are 4 8 15"}, ctx))
    second = _run(tool.execute({"text": "the numbers are 4 8 16"}, ctx))
    third = _run(tool.execute({"text": "the numbers are 4 8 23"}, ctx))

    assert second == ITEM_35.format(words="the numbers are 4 8 16")
    assert third == ITEM_35.format(words="the numbers are 4 8 23")
    assert ctx.voice_note_words == ["the numbers are 4 8 23"]   # the last call wins
    assert ctx.voice_notes == [] and tts.calls == []


def test_the_answers_quote_long_words_short(voice_on):
    words = "word " * 200
    ctx = _ctx()

    first = _run(_tool().execute({"text": words}, ctx))
    second = _run(_tool().execute({"text": words + "more"}, ctx))

    for said in (first, second):
        assert len(said) < 600
        assert "…" in said
    assert ctx.voice_note_words == [(words + "more").strip()]   # the whole words are kept


def test_the_pending_line_is_item_36_with_the_last_words():
    ctx = _ctx()
    assert vnf.pending_line(ctx) == ""

    ctx.voice_note_words.append("the numbers are 4 8 15")
    assert vnf.pending_line(ctx) == ITEM_36.format(words="the numbers are 4 8 15")

    ctx.voice_note_words[:] = ["word " * 100]
    line = vnf.pending_line(ctx)
    quoted = line.split('"')[1]
    assert len(quoted) <= 240 and quoted.endswith("…")


def test_on_a_step_that_cannot_call_voice_note_the_line_is_36s_first_sentence():
    """W3 (Fable's whole-branch review, M1): on a braked step voice_note
    can't be called, so the line is item 36's first sentence alone."""
    ctx = _ctx()
    assert vnf.pending_line(ctx, can_call=False) == ""
    ctx.voice_note_words.append("the numbers are 4 8 15")
    assert vnf.pending_line(ctx, can_call=False) == (
        '(Your voice message will say: "the numbers are 4 8 15".)')
    assert vnf.pending_line(ctx, can_call=True) == ITEM_36.format(words="the numbers are 4 8 15")
    ctx.voice_note_words[:] = ["word " * 100]
    quoted = vnf.pending_line(ctx, can_call=False).split('"')[1]
    assert len(quoted) <= 240 and quoted.endswith("…")


def test_the_pending_line_is_gone_once_the_note_is_made():
    ctx = _ctx()
    ctx.voice_note_words.append("good night")
    ctx.voice_notes.append(lane.OGG)
    assert vnf.pending_line(ctx) == ""


# ── the branches that stay as they were ───────────────────────────────────

def test_nothing_to_say_notes_nothing(voice_on):
    ctx = _ctx()
    assert _run(_tool().execute({"text": "  "}, ctx)) == "Nothing to say."
    assert ctx.voice_note_words == []


@pytest.mark.parametrize("surface", ["reminder_fire", "", "discord_voice", "dashboard"])
def test_off_a_chat_door_it_is_still_a_failure_and_notes_nothing(voice_on, surface):
    ctx = _ctx(surface)
    result = _run(_tool().execute({"text": "hello"}, ctx))
    assert isinstance(result, ToolFailure)
    assert "isn't in one" in str(result)
    assert ctx.voice_note_words == [] and ctx.voice_notes == []


def test_with_voice_replies_on_here_it_still_notes_nothing(voice_on):
    from core import voice_replies as vr

    ctx = _ctx()
    vr.set_on("discord:201", True)
    result = _run(_tool().execute({"text": "hello there"}, ctx))
    assert result.startswith("Nothing made — voice replies are on here")
    assert ctx.voice_note_words == []


# ── finish: one note, from the last words ─────────────────────────────────

def test_finish_makes_one_note_from_the_last_words():
    ctx = _ctx()
    ctx.voice_note_words.append("the numbers are 4 8 23")
    synth = FakeSynth()

    made = _run(vnf.finish(ctx, synthesize=synth))

    assert made == 1
    assert synth.words == ["the numbers are 4 8 23"]
    assert ctx.voice_notes == [lane.OGG]


def test_finish_with_nothing_pending_makes_nothing():
    ctx = _ctx()
    synth = FakeSynth()
    assert _run(vnf.finish(ctx, synthesize=synth)) == 0
    assert synth.words == [] and ctx.voice_notes == []


def test_finish_never_makes_a_second_note():
    ctx = _ctx()
    ctx.voice_note_words.append("good night")
    synth = FakeSynth()
    assert _run(vnf.finish(ctx, synthesize=synth)) == 1
    assert _run(vnf.finish(ctx, synthesize=synth)) == 0
    assert ctx.voice_notes == [lane.OGG] and synth.words == ["good night"]


@pytest.mark.parametrize("synth", [FakeSynth(None), FakeSynth(boom=RuntimeError("tts down"))])
def test_finish_when_the_speech_cannot_be_made_makes_nothing_and_warns(synth, caplog):
    ctx = _ctx()
    ctx.voice_note_words.append("good night")

    with caplog.at_level(logging.WARNING, logger="core.agent.voice_note_final"):
        made = _run(vnf.finish(ctx, synthesize=synth))

    assert made == 0 and ctx.voice_notes == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_finish_reaches_the_real_seam_through_the_fakes(monkeypatch):
    """No synthesizer handed in: the speech facade's, through the lane fakes."""
    tts, ffmpeg = lane.install(monkeypatch)
    ctx = _ctx()
    ctx.voice_note_words.append("the deploy finished")

    assert _run(vnf.finish(ctx)) == 1

    assert ctx.voice_notes == [lane.OGG]
    assert [c["text"] for c in tts.calls] == ["the deploy finished"]
    assert "libopus" in ffmpeg.commands[0]


def test_the_brake_sees_every_replacement_as_the_same_answer():
    first = ITEM_34.format(words="a")
    assert (vnf.brake_answer("voice_note", ITEM_35.format(words="worded 2"))
            == vnf.brake_answer("voice_note", ITEM_35.format(words="worded 3")))
    assert vnf.brake_answer("voice_note", first) != vnf.brake_answer(
        "voice_note", ITEM_35.format(words="a"))
    # Any other tool's answer is compared as it is.
    assert vnf.brake_answer("web_search", 'found "x"') == 'found "x"'
    assert vnf.brake_answer("web_search", 'found "y"') != vnf.brake_answer("web_search",
                                                                            'found "x"')


# ── through the spine ─────────────────────────────────────────────────────

class LookUpTool:
    """A stand-in work tool: one more round between the note and the text."""

    name = "look_up"
    description = "Look something up."
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    requires_approval = False
    owner_only = True

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        self.timeline.append("look_up")
        return "the drawing was 4 8 23"


class Timed(Model):
    """The harness's model, with each call on the shared timeline."""

    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self.timeline = timeline

    def next(self) -> Any:
        self.timeline.append("model")
        return super().next()


def _registry(*extra: Any) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in hh.build_her_hands_tools():
        if tool.name in ("voice_note", "voice_replies"):
            reg.register(tool)
    for tool in extra:
        reg.register(tool)
    return reg


def _note(call: dict[str, Any]) -> str:
    return str(call.get("trailing_system_note") or "")


def _tool_names(call: dict[str, Any]) -> list[str]:
    return [t["name"] for t in (call.get("tools") or [])]


async def _results(h: DmHarness) -> dict[str, str]:
    return {b.tool_use_id: b.content for m in await h.transcript() if m.role == "tool"
            for b in m.content if isinstance(b, ToolResultBlock)}


def _harness(monkeypatch, tmp_path, timeline: list[str], *, switch_on: bool = False,
             tts: Any = None) -> tuple[DmHarness, Any]:
    lane.voice_notes_on(monkeypatch)
    fake_tts, _ = lane.install(monkeypatch, tts=tts or lane.FakeTTS(timeline=timeline))
    h = DmHarness(monkeypatch, tmp_path, switch_on=switch_on)
    h.registry = _registry(LookUpTool(timeline))
    h.model = Timed(timeline)
    return h, fake_tts


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_the_note_is_made_once_at_the_end_from_the_tools_words(monkeypatch, tmp_path):
    timeline: list[str] = []
    h, tts = _harness(monkeypatch, tmp_path, timeline)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_calls(ToolUseBlock(id="l1", name="look_up", input={"q": "the drawing"}))
    h.model_answers("Here they are: `4 8 23`")

    result = await h.dispatch(h.turn("send me a voice message with the numbers"))

    assert timeline == ["model", "model", "look_up", "model", "tts"]
    assert [c["text"] for c in tts.calls] == ["the numbers are 4 8 23"]
    assert result.voice_notes == (lane.OGG,)
    assert result.response_text == "Here they are: `4 8 23`"
    results = await _results(h)
    assert results["v1"] == ITEM_34.format(words="the numbers are 4 8 23")


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_two_calls_make_one_note_with_the_seconds_words(monkeypatch, tmp_path):
    timeline: list[str] = []
    h, tts = _harness(monkeypatch, tmp_path, timeline)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 15"}))
    h.model_calls(ToolUseBlock(id="l1", name="look_up", input={"q": "check"}))
    h.model_calls(ToolUseBlock(id="v2", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_answers("")

    result = await h.dispatch(h.turn("voice message with the numbers please"))

    assert [c["text"] for c in tts.calls] == ["the numbers are 4 8 23"]
    assert result.voice_notes == (lane.OGG,)
    assert timeline[-1] == "tts"
    results = await _results(h)
    assert results["v2"] == ITEM_35.format(words="the numbers are 4 8 23")


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_every_later_round_shows_what_the_note_will_say_and_only_then(
        monkeypatch, tmp_path):
    timeline: list[str] = []
    h, _ = _harness(monkeypatch, tmp_path, timeline)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 15"}))
    h.model_calls(ToolUseBlock(id="l1", name="look_up", input={"q": "check"}))
    h.model_calls(ToolUseBlock(id="v2", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_answers("done")

    await h.dispatch(h.turn("voice message with the numbers please"))

    first_line = ITEM_36.format(words="the numbers are 4 8 15")
    second_line = ITEM_36.format(words="the numbers are 4 8 23")
    notes = [_note(c) for c in h.calls]
    assert len(notes) == 4
    assert "Your voice message will say" not in notes[0]       # nothing pending yet
    for note in notes[1:3]:
        assert note.count(first_line) == 1 and note.endswith(first_line)
    assert notes[3].count(second_line) == 1 and first_line not in notes[3]
    assert notes[3].endswith(second_line)


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_run_without_voice_note_has_no_pending_line(monkeypatch, tmp_path):
    timeline: list[str] = []
    h, tts = _harness(monkeypatch, tmp_path, timeline)
    h.model_calls(ToolUseBlock(id="l1", name="look_up", input={"q": "the drawing"}))
    h.model_answers("4 8 23")

    result = await h.dispatch(h.turn("what were the numbers?"))

    assert all("Your voice message will say" not in _note(c) for c in h.calls)
    assert tts.calls == [] and result.voice_notes == ()


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_fw3_a_quiet_choice_beside_pending_words_still_makes_the_note(
        monkeypatch, tmp_path):
    """Ruling FW-3: the note is a delivery, like a file; her quiet choice
    beside it stands, the note still goes, with no text, and her record says
    she answered aloud (second read F8)."""
    timeline: list[str] = []
    h, tts = _harness(monkeypatch, tmp_path, timeline, switch_on=True)
    h.model_chooses_quiet("they asked for a voice message and no reply", also=[
        ToolUseBlock(id="v1", name="voice_note", input={"text": "good night, Robin"})])

    result = await h.dispatch(h.turn("send me a voice note saying good night, no reply"))

    assert result.quiet is True and result.response_text == ""
    assert result.voice_notes == (lane.OGG,) and result.responded is True
    assert [c["text"] for c in tts.calls] == ["good night, Robin"]
    assert timeline == ["model", "tts"]                         # made after the choice
    msgs = await h.transcript()
    said = [b.text for m in msgs if m.role == "assistant" for b in m.content
            if isinstance(b, TextBlock)]
    assert said == ["good night, Robin"]                        # _spoken_words
    assert h.quiet_record() == []
    results = await _results(h)
    assert "voice message" in results["q1"] and "Nothing was sent" not in results["q1"]


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_fw3_the_quiet_choice_a_step_after_the_words_is_spoken_too(monkeypatch, tmp_path):
    timeline: list[str] = []
    h, tts = _harness(monkeypatch, tmp_path, timeline, switch_on=True)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note", input={"text": "sleep well"}))
    h.model_chooses_quiet("they wanted only the voice message")

    result = await h.dispatch(h.turn("voice note please, sleep well, no reply"))

    assert result.quiet is True and result.voice_notes == (lane.OGG,)
    assert [c["text"] for c in tts.calls] == ["sleep well"]
    results = await _results(h)
    assert "Nothing was sent" not in results["q2"]


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_when_the_speech_fails_at_the_end_the_text_still_goes_with_a_warning(
        monkeypatch, tmp_path, caplog):
    timeline: list[str] = []
    h, _ = _harness(monkeypatch, tmp_path, timeline, tts=lane.FakeTTS(None, timeline=timeline))
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_answers("The numbers are 4 8 23.")

    with caplog.at_level(logging.WARNING, logger="core.agent.voice_note_final"):
        result = await h.dispatch(h.turn("voice message with the numbers"))

    assert result.voice_notes == ()
    assert result.response_text == "The numbers are 4 8 23."
    assert result.responded is True
    assert any(r.levelno == logging.WARNING and r.name == "core.agent.voice_note_final"
               for r in caplog.records)


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_f3_with_voice_replies_on_here_the_tool_is_not_offered(monkeypatch, tmp_path):
    from core import voice_replies as vr

    timeline: list[str] = []
    h, tts = _harness(monkeypatch, tmp_path, timeline)
    vr.set_on(f"discord:{DM_CHANNEL}", True)
    h.model_answers("sunny all day")

    await h.dispatch(h.turn("what's the weather"))

    assert "voice_note" not in _tool_names(h.calls[0])
    assert "voice_replies" in _tool_names(h.calls[0])
    assert all("Your voice message will say" not in _note(c) for c in h.calls)


class VoiceNoteFiveTimes(Model):
    """voice_note, reworded, five times in a row, then a text reply — the
    10-01 loop's shape (DEFECT §56), cut short."""

    def __init__(self) -> None:
        super().__init__()
        self.plan = [("tool", ToolUseBlock(id=f"v{i}", name="voice_note",
                                           input={"text": f"the answer, worded {i}"}), [])
                     for i in range(1, 6)] + [("text", "done")]


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_defect_56_a_run_of_five_calls_makes_one_note(monkeypatch, tmp_path):
    from bots.common.voice_note import plan_voice_reply

    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=False)
    h.registry = _registry()
    h.model = VoiceNoteFiveTimes()

    result = await h.dispatch(h.turn("send me a voice message with the answer"))
    plan = await plan_voice_reply("discord", DM_CHANNEL, result, owner_id=OWNER_APP_ID)

    assert len(h.calls) <= A_FEW
    assert len(tts.calls) == 1 and result.voice_notes == (lane.OGG,)
    assert plan is not None and plan.notes == (lane.OGG,)
    # The note says the last words that ran.
    results = await _results(h)
    ran = [k for k, v in results.items() if v.startswith(("Voice message noted", "Replaced"))]
    last = int(ran[-1][1:])
    assert tts.calls[0]["text"] == f"the answer, worded {last}"


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_m1_the_braked_step_shows_the_words_without_asking_for_another_call(
        monkeypatch, tmp_path):
    """M1 / W3 (Fable's whole-branch review): the brake's reply step offers
    no voice_note, so its note shows what the message will say (36's first
    sentence) and never "Call voice_note again"; every round before it shows
    item 36 whole."""
    from core.agent.runner import BRAKE_NOTE

    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=False)
    h.registry = _registry()
    h.model = VoiceNoteFiveTimes()

    await h.dispatch(h.turn("send me a voice message with the answer"))

    brake_head = BRAKE_NOTE.split("{", 1)[0]
    braked = [c for c in h.calls if brake_head in _note(c)]
    before = [c for c in h.calls[1:] if brake_head not in _note(c)]
    assert braked and before
    for call in braked:
        note = _note(call)
        assert "Call voice_note again" not in note
        assert re.search(r'^\(Your voice message will say: "the answer, worded \d"\.\)$', note,
                         re.MULTILINE), note
    for call in before:
        assert "Call voice_note again if what you've found changes it.)" in _note(call)


def test_a_reply_still_carries_at_most_one_voice_message():
    from core import voice_replies as vr

    assert vr.MAX_VOICE_NOTES_PER_REPLY == 1


@_UTCNOW_DEBT
@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["discord", "telegram"])
async def test_both_doors_read_the_runs_voice_notes_as_before(monkeypatch, tmp_path, platform):
    from bots.common.voice_note import VoiceReply, plan_voice_reply

    timeline: list[str] = []
    h, _ = _harness(monkeypatch, tmp_path, timeline)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note", input={"text": "here's the link"}))
    h.model_answers("https://example.com/a")

    result = await h.dispatch(h.turn("send me the link, out loud"))
    plan = await plan_voice_reply(platform, DM_CHANNEL, result, owner_id=OWNER_APP_ID)

    assert plan == VoiceReply(text="https://example.com/a", notes=(lane.OGG,))


# ── fix round 1: never a lost message ─────────────────────────────────────
# The owner's rule: when the speech can't be made at the end and the reply
# carries no text, the words she chose go as her text reply — beside a quiet
# choice too (she chose to say them). A stopped or paused run sends nothing.

def _record_rows(monkeypatch) -> list[str]:
    """The assistant rows dispatch writes to the conversation history."""
    rows: list[str] = []

    async def persist(*, turn, response_text, **kw):
        rows.append(response_text)

    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", persist)
    return rows


async def _said(h: DmHarness) -> list[str]:
    return [b.text for m in await h.transcript() if m.role == "assistant" for b in m.content
            if isinstance(b, TextBlock)]


def _failing(monkeypatch, tmp_path, *, switch_on: bool = False) -> tuple[DmHarness, Any]:
    timeline: list[str] = []
    return _harness(monkeypatch, tmp_path, timeline, switch_on=switch_on,
                    tts=lane.FakeTTS(None, timeline=timeline))


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_failed_note_with_no_text_sends_the_words_as_text(monkeypatch, tmp_path, caplog):
    h, tts = _failing(monkeypatch, tmp_path)
    rows = _record_rows(monkeypatch)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_answers("")

    with caplog.at_level(logging.WARNING, logger="core.agent.runner"):
        result = await h.says("send me a voice message with the numbers")

    assert len(tts.calls) == 1                                  # it was tried
    assert result.response_text == "the numbers are 4 8 23"
    assert result.voice_notes == () and result.responded is True
    assert h.sent == ["the numbers are 4 8 23"]                 # it reached the door
    assert rows == ["the numbers are 4 8 23"]                   # the assistant row
    assert (await _said(h)) == ["the numbers are 4 8 23"]       # her transcript
    warned = [r for r in caplog.records
              if r.name == "core.agent.runner" and r.levelno == logging.WARNING
              and "voice message" in r.getMessage()]
    assert len(warned) == 1
    assert "4 8 23" not in warned[0].getMessage()               # counts only


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_failed_note_beside_text_leaves_the_text_alone(monkeypatch, tmp_path):
    h, _ = _failing(monkeypatch, tmp_path)
    rows = _record_rows(monkeypatch)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_answers("Here they are: `4 8 23`")

    result = await h.says("send me a voice message with the numbers")

    assert result.response_text == "Here they are: `4 8 23`"
    assert result.voice_notes == ()
    assert h.sent == ["Here they are: `4 8 23`"]
    assert rows == ["Here they are: `4 8 23`"]
    assert (await _said(h)) == ["Here they are: `4 8 23`"]      # nothing appended


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_two_calls_then_a_failed_note_send_the_last_words(monkeypatch, tmp_path):
    h, _ = _failing(monkeypatch, tmp_path)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 15"}))
    h.model_calls(ToolUseBlock(id="v2", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_answers("")

    result = await h.says("voice message with the numbers please")

    assert result.response_text == "the numbers are 4 8 23"
    assert h.sent == ["the numbers are 4 8 23"]


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_note_that_was_made_adds_no_text(monkeypatch, tmp_path):
    from bots.common.voice_note import VoiceReply, plan_voice_reply

    timeline: list[str] = []
    h, _ = _harness(monkeypatch, tmp_path, timeline)
    rows = _record_rows(monkeypatch)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note", input={"text": "good morning"}))
    h.model_answers("")

    result = await h.dispatch(h.turn("say good morning to me"))
    plan = await plan_voice_reply("discord", DM_CHANNEL, result, owner_id=OWNER_APP_ID)

    assert result.response_text == "" and result.voice_notes == (lane.OGG,)
    assert plan == VoiceReply(text="", notes=(lane.OGG,))
    assert rows == [] and (await _said(h)) == []


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_quiet_choice_then_a_failed_note_sends_the_words_as_text(monkeypatch, tmp_path):
    h, _ = _failing(monkeypatch, tmp_path, switch_on=True)
    rows = _record_rows(monkeypatch)
    h.model_chooses_quiet("they asked for a voice message and no reply", also=[
        ToolUseBlock(id="v1", name="voice_note", input={"text": "good night, Robin"})])

    result = await h.says("send me a voice note saying good night, no reply")

    assert result.quiet is False and result.quiet_reason == ""
    assert result.terminated_by == "natural"
    assert result.response_text == "good night, Robin" and result.responded is True
    assert h.sent == ["good night, Robin"]
    assert h.quiet_record() == []                               # no quiet row
    assert rows == ["good night, Robin"]
    assert (await _said(h)) == ["good night, Robin"]


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_long_words_go_whole_not_the_quote(monkeypatch, tmp_path):
    words = " ".join(f"word{i}" for i in range(80))              # well over 240 characters
    assert len(words) > 240
    h, _ = _failing(monkeypatch, tmp_path)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note", input={"text": words}))
    h.model_answers("")

    result = await h.says("send me the long voice message")

    assert result.response_text == words
    assert h.sent == [words]


class StopTool:
    """A stand-in work tool that asks the run to stop (step 27's "forget it")."""

    name = "stop_here"
    description = "Stand-in."
    input_schema = {"type": "object", "properties": {}}
    requires_approval = False
    owner_only = True

    def __init__(self, flag: dict) -> None:
        self.flag = flag

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        self.flag["stop"] = True
        return "ok"


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_stopped_run_adds_no_text(monkeypatch, tmp_path):
    from core.agent import duplex, steering

    h, _ = _failing(monkeypatch, tmp_path)
    flag = {"stop": False}
    h.registry = _registry(StopTool(flag))
    monkeypatch.setattr(duplex, "duplex_enabled", lambda: True)
    monkeypatch.setattr(steering, "stop_requested", lambda key: flag["stop"])
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_calls(ToolUseBlock(id="s1", name="stop_here", input={}))
    h.model_answers("never asked")

    result = await h.dispatch(h.turn("voice message with the numbers"))

    assert result.terminated_by == "stopped"
    assert result.response_text == "" and result.voice_notes == ()
    assert (await _said(h)) == []


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_no_voice_note_and_no_text_is_still_silence(monkeypatch, tmp_path):
    h, tts = _failing(monkeypatch, tmp_path)
    rows = _record_rows(monkeypatch)
    h.model_answers("")

    result = await h.says("hmm")

    assert result.responded is False and result.response_text == ""
    assert h.sent == [] and rows == [] and tts.calls == []


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_the_words_reach_the_door_even_after_text_was_streamed(monkeypatch, tmp_path):
    """After a quiet choice, text streamed in an earlier round was already
    handed to the door's capture, and the doors send the reply's beats (the
    Telegram door sends a single beat's own text, bots/telegram/reply.py),
    not response_text: the words go through the same channel, so a beat
    carries them."""
    import core.agent.llm_client as llm_mod

    h, _ = _failing(monkeypatch, tmp_path, switch_on=True)
    inner = llm_mod.call
    # Over the chunker's 80-character floor, ending a paragraph: emitted.
    streamed = ("On it. Here comes the voice message you asked for, with tonight's "
                "numbers read out slowly.\n\n")
    assert len(streamed) >= 80

    async def streaming(**kw):
        response = await inner(**kw)
        if len(h.calls) == 1 and kw.get("on_chunk") is not None:
            await kw["on_chunk"](streamed)
        return response

    monkeypatch.setattr(llm_mod, "call", streaming)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "the numbers are 4 8 23"}))
    h.model_chooses_quiet("they wanted only the voice message")

    result = await h.dispatch(h.turn("voice note please, the numbers, no reply"))

    assert result.quiet is False
    assert result.response_text == "the numbers are 4 8 23"
    assert result.cadenced_reply is not None
    beats = [b.text for b in result.cadenced_reply.beats]
    assert any("the numbers are 4 8 23" in b for b in beats), beats


def test_will_speak_reads_a_made_or_a_waiting_note():
    ctx = _ctx()
    assert vnf.will_speak(ctx) is False
    ctx.voice_note_words.append("good night")
    assert vnf.will_speak(ctx) is True
    ctx.voice_note_words.clear()
    ctx.voice_notes.append(lane.OGG)
    assert vnf.will_speak(ctx) is True


class GatedTTS(lane.FakeTTS):
    """The speech waits for the test's word, so a cancel can land mid-way."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False
        self.cut = False

    async def synthesize_speech(self, **kw):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cut = True
            raise
        self.finished = True
        return await super().synthesize_speech(**kw)


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_cancel_during_the_speech_never_cuts_it_and_still_ends_the_run(
        monkeypatch, tmp_path):
    tts = GatedTTS()
    timeline: list[str] = []
    h, _ = _harness(monkeypatch, tmp_path, timeline, tts=tts)
    h.model_calls(ToolUseBlock(id="v1", name="voice_note", input={"text": "good night"}))
    h.model_answers("")

    task = asyncio.create_task(h.dispatch(h.turn("say good night")))
    await asyncio.wait_for(tts.started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task                                              # the cancel still proceeds
    tts.release.set()
    for _ in range(200):
        if tts.finished:
            break
        await asyncio.sleep(0.01)
    assert tts.finished is True and tts.cut is False            # the speech was not cut
