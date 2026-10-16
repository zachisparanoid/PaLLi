"""Voice messages, properly (Batch 8, task 10 — B8-5; rulings T10-1..T10-9).

Two tools beside ``speak_line`` in her hands, both owner-only, both behind
one setting, ``voice.voice_notes`` (schema default off; the suite pins it
off, so every existing test is a switch-off pin):

- ``voice_note(text)`` — her words, through the speech facade, as the
  OGG/Opus bytes a voice message carries, handed to the door on the turn's
  ``ToolContext.voice_notes``. It goes out WITH her reply, so the result
  never says "sent". Since R15 (Group V) the tool only notes the words (the
  last call wins) and the runner makes the note when the reply is finished
  (``core.agent.voice_note_final``; tests/agent/test_voice_note_final.py);
- ``voice_replies(on|off)`` — a switch she sets for this conversation when
  asked, kept in a small file so it survives a restart, keyed by
  ``core.session_keys.session_key_for``; Telegram's ``/voice`` flips the same
  switch (tests/bots/test_telegram_voice_out.py).

While the switch is on for a conversation, her reply call there carries ONE
status line (owner O2 — INTERIM, the owner revisits it after the batches);
with it off the payload is byte-identical. Nothing here reaches a real TTS:
the facade and ffmpeg are fakes at their own modules. Stand-in names only.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.agent.builtin_tools.her_hands as hh
from core.agent.session import ToolUseBlock
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext, ToolRegistry
from tests.agent import voice_lane_fakes as lane
from tests.agent.she_decides_harness import (
    DM_CHANNEL,
    OWNER_APP_ID,
    ROOM_CHANNEL,
    DmHarness,
)

REPO = Path(__file__).resolve().parents[2]
SIX = ["join_voice_channel", "leave_voice", "speak_line", "list_members",
       "send_message", "set_presence"]
# speak_line's description at the batch's base (626e7f4), byte for byte —
# with one word since 2026-10-13: the text fallback goes to the PRIVATE chat
# they used last (the owner: her own messages to him never go to a group).
SPEAK_LINE_TODAY = (
    "Say one line out loud in the voice channel you're currently in. Greet "
    "them when they walk in, answer aloud, think out loud — your voice, your "
    "judgment. With no live voice session to take it, the line goes to them "
    "as text on the private chat they used last instead, and the result tells "
    "you so — unless that chat is this very conversation: then nothing is "
    "sent, and you say it in your reply."
)

_UTCNOW_DEBT = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


def _ctx(surface: str = "discord_text", channel_id: int = 201, *,
         owner: bool = True, owner_id: int = OWNER_APP_ID) -> ToolContext:
    return ToolContext(turn_owner_id=owner_id, is_owner=owner, surface=surface,
                       channel_id=channel_id)


def _tools() -> dict:
    return {t.name: t for t in hh.build_her_hands_tools()}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def voice_on(monkeypatch):
    lane.voice_notes_on(monkeypatch)


# ── the setting ───────────────────────────────────────────────────────────

def test_one_setting_and_it_ships_off():
    from core.settings_schema import PalliSettings, VoiceConfig
    import yaml

    assert VoiceConfig.model_fields["voice_notes"].default is False
    example = yaml.safe_load((REPO / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    assert PalliSettings.model_validate(example).voice.voice_notes is False


def test_the_suite_holds_it_off_and_the_real_accessor_fails_closed(monkeypatch):
    from core import voice_replies as vr
    import core.settings_boot as sb

    assert vr.voice_notes_enabled() is False            # the suite's pin
    real = vr._voice_notes_enabled_impl

    class _Boom:
        def snapshot(self):
            raise RuntimeError("unbooted")

    monkeypatch.setattr(sb, "settings_service", _Boom())
    assert real() is False                              # an error reads as off
    on = SimpleNamespace(voice=SimpleNamespace(voice_notes=True))
    monkeypatch.setattr(sb, "settings_service", SimpleNamespace(snapshot=lambda: on))
    assert real() is True
    monkeypatch.setattr(sb, "settings_service", SimpleNamespace(snapshot=lambda: None))
    assert real() is False


# ── registration ──────────────────────────────────────────────────────────

def test_with_the_setting_off_her_hands_are_todays_six_and_speak_line_is_unchanged():
    tools = _tools()
    assert list(tools) == SIX
    assert tools["speak_line"].description == SPEAK_LINE_TODAY
    assert hh.SpeakLineTool.description == SPEAK_LINE_TODAY


def test_with_the_setting_on_both_voice_tools_join_her_hands(voice_on):
    tools = _tools()
    assert list(tools) == [*SIX, "voice_note", "voice_replies"]
    for name in ("voice_note", "voice_replies"):
        assert tools[name].owner_only is True
        assert tools[name].requires_approval is False
        assert tuple(tools[name].surfaces) == ("discord", "telegram", "mobile")     # T10-7; the phone app 10-13


def test_the_default_registry_carries_them_only_with_the_setting_on(monkeypatch):
    import core.agent.builtin_tools as bt

    monkeypatch.setattr(bt, "_her_hands_enabled", lambda: True)
    assert not {"voice_note", "voice_replies"} & set(bt.build_default_registry().all_names())
    lane.voice_notes_on(monkeypatch)
    assert {"voice_note", "voice_replies"} <= set(bt.build_default_registry().all_names())


def test_no_voice_tool_is_offered_on_the_dashboard_or_to_a_guest(voice_on):
    reg = ToolRegistry()
    for tool in _tools().values():
        reg.register(tool)

    def offered(ctx):
        return {s["name"] for s in reg.schemas_for(ctx)}

    assert {"voice_note", "voice_replies"} <= offered(_ctx("discord_text"))
    assert {"voice_note", "voice_replies"} <= offered(_ctx("telegram_text"))
    assert not {"voice_note", "voice_replies"} & offered(_ctx("dashboard"))
    assert not {"voice_note", "voice_replies"} & offered(_ctx("discord_text", owner=False))


# ── voice_note ────────────────────────────────────────────────────────────

def test_voice_note_notes_her_words_and_the_finished_reply_makes_the_opus_bytes(
        voice_on, monkeypatch):
    from core.agent import voice_note_final

    tts, ffmpeg = lane.install(monkeypatch)
    ctx = _ctx()

    result = _run(_tools()["voice_note"].execute({"text": "the deploy finished"}, ctx))

    # R15, Group V: nothing is spoken yet; the words wait for the end.
    assert ctx.voice_notes == [] and tts.calls == []
    assert ctx.voice_note_words == ["the deploy finished"]
    assert not isinstance(result, ToolFailure)
    # It goes out WITH her reply, so the result never claims it was sent
    # (the 5.10 lesson).
    assert "when you finish" in result
    assert "sent" not in result.lower()

    assert _run(voice_note_final.finish(ctx)) == 1
    assert ctx.voice_notes == [lane.OGG]
    assert tts.calls[0]["text"] == "the deploy finished"
    assert "libopus" in ffmpeg.commands[0]


def test_voice_note_with_nothing_to_say_makes_nothing(voice_on, monkeypatch):
    tts, _ = lane.install(monkeypatch)
    ctx = _ctx()

    result = _run(_tools()["voice_note"].execute({"text": "   "}, ctx))

    assert ctx.voice_notes == []
    assert tts.calls == []
    assert result == "Nothing to say."


def test_voice_note_that_cannot_be_made_at_the_end_queues_nothing(voice_on, monkeypatch):
    """R15, Group V: the speech is made when the reply is finished, so a
    failure there costs the note, never the text (logged; see
    tests/agent/test_voice_note_final.py)."""
    from core.agent import voice_note_final

    lane.install(monkeypatch, tts=lane.FakeTTS(None))
    ctx = _ctx()

    result = _run(_tools()["voice_note"].execute({"text": "into the void"}, ctx))

    assert not isinstance(result, ToolFailure)
    assert _run(voice_note_final.finish(ctx)) == 0
    assert ctx.voice_notes == []


@pytest.mark.parametrize("surface", ["reminder_fire", "", "discord_voice", "dashboard"])
def test_voice_note_off_a_chat_door_is_a_failure_and_speaks_nothing(voice_on, monkeypatch, surface):
    """Blank and unplatformed surfaces pass the registry's surface gate; only
    a Discord or Telegram text chat has a door that sends ctx.voice_notes."""
    tts, _ = lane.install(monkeypatch)
    ctx = _ctx(surface)

    result = _run(_tools()["voice_note"].execute({"text": "hello"}, ctx))

    assert isinstance(result, ToolFailure)
    assert ctx.voice_notes == []
    assert tts.calls == []


def test_synthesis_lives_in_voice_and_the_door_module_re_exports_it():
    """T10-1: core never imports bots — the synthesis moved to voice/."""
    from bots.common import voice_note as door_side
    from voice import voice_note_out

    assert door_side.synthesize_voice_note is voice_note_out.synthesize_voice_note
    assert door_side.VOICE_NOTE_BITRATE == voice_note_out.VOICE_NOTE_BITRATE


def test_no_voice_name_is_written_in_the_code():
    """T10-8: she speaks through the facade's configured voice."""
    for rel in ("core/agent/builtin_tools/her_hands.py", "voice/voice_note_out.py",
                "core/voice_replies.py", "bots/common/voice_note.py",
                "bots/discord/voice_reply.py"):
        text = (REPO / rel).read_text(encoding="utf-8").lower()
        for name in ("olivia", "sarah"):
            assert name not in text, (rel, name)


# ── voice_replies ─────────────────────────────────────────────────────────

def test_voice_replies_on_sets_this_conversations_switch_only(voice_on):
    from core import voice_replies as vr

    result = _run(_tools()["voice_replies"].execute({"state": "on"}, _ctx("discord_text", 201)))

    assert vr.is_on("discord:201") is True
    assert vr.is_on("discord:202") is False
    assert vr.is_on("telegram:201") is False
    assert "on" in result and not isinstance(result, ToolFailure)


def test_the_key_is_the_conversations_session_key(voice_on):
    from core import voice_replies as vr
    from core.session_keys import session_key_for

    _run(_tools()["voice_replies"].execute({"state": "on"}, _ctx("telegram_text", -1004242)))

    assert vr.is_on(session_key_for("telegram_text", -1004242, OWNER_APP_ID)) is True
    assert vr.key_for("telegram_text", -1004242, OWNER_APP_ID) == "telegram:-1004242"


def test_voice_replies_off_clears_it_and_status_reports_it(voice_on):
    from core import voice_replies as vr

    tool = _tools()["voice_replies"]
    _run(tool.execute({"state": "on"}, _ctx()))
    assert "on" in _run(tool.execute({"state": "status"}, _ctx()))

    _run(tool.execute({"state": "off"}, _ctx()))

    assert vr.is_on("discord:201") is False
    assert "off" in _run(tool.execute({"state": "status"}, _ctx()))


def test_voice_replies_with_an_unknown_word_changes_nothing(voice_on):
    from core import voice_replies as vr

    result = _run(_tools()["voice_replies"].execute({"state": "loud"}, _ctx()))

    assert isinstance(result, ToolFailure)
    assert vr.is_on("discord:201") is False


def test_voice_replies_off_a_chat_door_is_a_failure(voice_on):
    from core import voice_replies as vr

    result = _run(_tools()["voice_replies"].execute({"state": "on"}, _ctx("reminder_fire", 0)))

    assert isinstance(result, ToolFailure)
    assert not vr.VoiceRepliesStore().conversations_on()


def test_the_switch_survives_a_restart(voice_on):
    """B8-5 overrides "it resets when I restart": a fresh store — and the
    door's flag lookup with its in-memory flags gone — reads it back."""
    from bots.common import steering
    from core import voice_replies as vr

    _run(_tools()["voice_replies"].execute({"state": "on"}, _ctx("telegram_text", 424242)))
    steering._SESSION_FLAGS.clear()                  # what a restart forgets

    assert vr.VoiceRepliesStore().is_on("telegram:424242") is True
    assert steering.get_session_flag("telegram:424242", "voice_replies") is True
    on_disk = json.loads(vr.store_path().read_text(encoding="utf-8"))
    assert on_disk == {"telegram:424242": True}


def test_an_unreadable_store_reads_as_off_and_is_never_overwritten(voice_on):
    from core import voice_replies as vr

    path = vr.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    assert vr.is_on("discord:201") is False
    result = _run(_tools()["voice_replies"].execute({"state": "on"}, _ctx()))

    assert isinstance(result, ToolFailure)
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_the_store_never_touches_her_live_file():
    """G3: the suite redirects the store, and git ignores the live file."""
    from core import voice_replies as vr

    # (Never ``from tests.conftest import …``: a second import of the conftest
    # re-runs its redirects with a fresh tmp root.)
    assert str(vr.store_path()).startswith(os.environ["PALLI_TEST_DATA_ROOT"])
    ignored = subprocess.run(["git", "check-ignore", "-q", "data/voice_replies.json"],
                             cwd=REPO, check=False)
    assert ignored.returncode == 0


# ── the status line (owner O2 — INTERIM) ─────────────────────────────────

@pytest.fixture
def harness(monkeypatch, tmp_path) -> DmHarness:
    return DmHarness(monkeypatch, tmp_path, switch_on=False)


def _note(call) -> str:
    return str(call.get("trailing_system_note") or "")


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_the_status_line_rides_her_reply_call_while_the_switch_is_on(harness, monkeypatch):
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    lane.her_hands_on(monkeypatch)
    vr.set_on(f"discord:{DM_CHANNEL}", True)
    harness.model_answers("on it")

    await harness.dispatch(harness.turn("what's the weather"))

    assert _note(harness.calls[0]).count(vr.STATUS_LINE) == 1
    assert "voice_note" in vr.STATUS_LINE
    for word in ("link", "code", "number"):
        assert word in vr.STATUS_LINE


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_the_status_line_is_there_on_telegram_too(harness, monkeypatch):
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    lane.her_hands_on(monkeypatch)
    vr.set_on("telegram:424242", True)
    harness.model_answers("on it")

    await harness.dispatch(harness.turn("hi", surface="telegram_text", channel=424242))

    assert vr.STATUS_LINE in _note(harness.calls[0])


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_guests_reply_call_is_told_their_reply_will_be_spoken(harness, monkeypatch):
    """A guest's reply is spoken where the switch is on (the owner, 10-12:
    guests' messages are "normal messages, minus permissions"), so her reply
    call to them says so — without the sentence about voice_note: a guest's
    turn has no tools."""
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    lane.her_hands_on(monkeypatch)
    vr.set_on(f"discord:{ROOM_CHANNEL}", True)
    harness.model_answers("hey Sam")

    await harness.dispatch(harness.turn("hello", who="Sam", channel=ROOM_CHANNEL, is_dm=False))

    assert harness.calls
    note = _note(harness.calls[0])
    assert note.count(vr.GUEST_STATUS_LINE) == 1
    assert vr.STATUS_LINE not in note
    assert "voice_note" not in vr.GUEST_STATUS_LINE
    for word in ("spoken", "link", "code", "number"):
        assert word in vr.GUEST_STATUS_LINE


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_no_status_line_where_the_switch_is_off(harness, monkeypatch):
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    lane.her_hands_on(monkeypatch)
    vr.set_on("discord:999", True)                    # another conversation's switch
    harness.model_answers("on it")

    await harness.dispatch(harness.turn("hi"))

    assert vr.STATUS_LINE not in _note(harness.calls[0])


def test_the_status_line_is_for_a_persons_own_message_on_a_chat_door(monkeypatch):
    """The one seam, directly: the owner's own message gets his line, a
    guest's message gets theirs (10-12), and the door and the origin close
    it for everything else."""
    from core import voice_replies as vr
    from core.turn.types import Turn, TurnMetadata

    lane.voice_notes_on(monkeypatch)
    lane.her_hands_on(monkeypatch)
    vr.set_on("discord:201", True)

    def turn(surface="discord_text", origin="user"):
        return Turn(surface=surface, owner_id=OWNER_APP_ID, content="hi", images=[],
                    metadata=TurnMetadata(channel_id=201, is_dm=True, origin=origin),
                    received_at=datetime(2026, 10, 1, 9, 0, 0))

    assert vr.status_line_for(turn(), owner=True) == vr.STATUS_LINE
    assert vr.status_line_for(turn(), owner=False) == ""
    # A guest's turn is a guest's, however the caller names it.
    assert vr.status_line_for(turn(origin="guest"), owner=False) == vr.GUEST_STATUS_LINE
    assert vr.status_line_for(turn(origin="guest"), owner=True) == vr.GUEST_STATUS_LINE
    assert vr.status_line_for(turn("reminder_fire"), owner=True) == ""
    assert vr.status_line_for(turn("dashboard"), owner=True) == ""
    assert vr.status_line_for(turn("dashboard", origin="guest"), owner=False) == ""
    monkeypatch.setattr(vr, "voice_notes_enabled", lambda: False)
    assert vr.status_line_for(turn(), owner=True) == ""
    assert vr.status_line_for(turn(origin="guest"), owner=False) == ""


def test_the_owners_status_line_is_unchanged_word_for_word():
    """The guest line was cut from the owner's; his own stays byte-identical
    (his reply call's prompt is not touched by the guests' change)."""
    from core import voice_replies as vr

    assert vr.STATUS_LINE == (
        "[Voice replies are on here: just write your reply — it will be spoken and "
        "go out as a voice message. Anything they'd need to read goes out as a short "
        "text line beside it on its own: a link, a code block, or a number you put "
        "in backticks. You don't need voice_note while this is on.]"
    )


def _guest_turn(channel=201):
    from core.turn.types import Turn, TurnMetadata

    return Turn(surface="discord_text", owner_id=4242, content="[Sam]: hi", images=[],
                metadata=TurnMetadata(channel_id=channel, is_dm=False, origin="guest"),
                received_at=datetime(2026, 10, 1, 9, 0, 0))


def test_a_guests_line_needs_no_hands_because_it_names_no_tool(monkeypatch):
    """The owner's line waits for her hands because it points at voice_note
    (FW-16). The guest's names no tool, and the door speaks the reply either
    way, so the switch alone decides it."""
    import core.agent.builtin_tools as bt
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    vr.set_on("discord:201", True)
    monkeypatch.setattr(bt, "_her_hands_enabled", lambda: False)
    assert vr.status_line_for(_guest_turn(), owner=False) == vr.GUEST_STATUS_LINE
    vr.set_on("discord:201", False)
    assert vr.status_line_for(_guest_turn(), owner=False) == ""


def test_on_a_host_with_no_voice_a_guests_line_promises_nothing(monkeypatch):
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    vr.set_on("discord:201", True)
    lane.no_voice_on_this_host(monkeypatch)
    assert vr.status_line_for(_guest_turn(), owner=False) == ""


def test_a_guests_quick_read_is_told_the_reply_is_spoken_too(monkeypatch):
    """The read note goes where the status line goes (DEFECT §56), so a
    guest's quick read never says "text is fine" against the switch."""
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    vr.set_on("discord:201", True)
    assert vr.read_note_for(_guest_turn()) == vr.READ_NOTE
    vr.set_on("discord:201", False)
    assert vr.read_note_for(_guest_turn()) == ""


def _owner_turn(channel=201):
    from core.turn.types import Turn, TurnMetadata

    return Turn(surface="discord_text", owner_id=OWNER_APP_ID, content="hi", images=[],
                metadata=TurnMetadata(channel_id=channel, is_dm=True, origin="user"),
                received_at=datetime(2026, 10, 1, 9, 0, 0))


def test_the_status_line_needs_her_hands_because_it_points_at_voice_note(monkeypatch):
    """Final fix wave (ruling FW-16): the line tells her to call voice_note,
    which is on her belt only with features.her_hands — without her hands
    there is no line, even with the switch on (/voice can flip it)."""
    import core.agent.builtin_tools as bt
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    vr.set_on("discord:201", True)
    monkeypatch.setattr(bt, "_her_hands_enabled", lambda: False)
    assert vr.status_line_for(_owner_turn(), owner=True) == ""
    monkeypatch.setattr(bt, "_her_hands_enabled", lambda: True)
    assert vr.status_line_for(_owner_turn(), owner=True) == vr.STATUS_LINE


def test_on_a_host_with_no_voice_the_status_line_promises_nothing(monkeypatch):
    """Final fix wave (ruling FW-15): where boot found no TTS the door sends
    her reply as text, so no line says it will be spoken."""
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    lane.her_hands_on(monkeypatch)
    vr.set_on("discord:201", True)
    lane.no_voice_on_this_host(monkeypatch)
    assert vr.status_line_for(_owner_turn(), owner=True) == ""


def test_on_a_host_with_no_voice_the_switch_says_her_replies_stay_text(voice_on, monkeypatch):
    """Final fix wave (ruling FW-15): the switch still flips (it works the
    moment a voice is configured), and what she reads back says her replies
    go as text until then — /voice's own no-voice answer."""
    from core import voice_replies as vr

    lane.no_voice_on_this_host(monkeypatch)
    tool = _tools()["voice_replies"]
    ctx = _ctx(channel_id=202)

    on = _run(tool.execute({"state": "on"}, ctx))
    status = _run(tool.execute({"state": "status"}, ctx))

    assert vr.is_on("discord:202") is True
    for said in (on, status):
        assert not isinstance(said, ToolFailure)
        assert "no voice is configured on this host" in said
        assert "as text" in said and "go as voice messages" not in said


def test_telegrams_voice_command_and_her_tools_read_one_no_voice_answer(monkeypatch):
    from bots.telegram import commands
    from core import voice_replies as vr

    assert vr.no_voice_on_this_host() is False and commands._no_voice_on_this_host() is False
    lane.no_voice_on_this_host(monkeypatch)
    assert vr.no_voice_on_this_host() is True and commands._no_voice_on_this_host() is True


async def _capture(monkeypatch, tmp_path, *, setting_on: bool) -> list:
    from tests.agent.she_decides_golden import _call
    from core import voice_replies as vr

    with monkeypatch.context() as mp:
        h = DmHarness(mp, tmp_path, switch_on=False)
        mp.setattr(vr, "voice_notes_enabled", lambda: setting_on)
        if setting_on:
            vr.set_on("discord:999", True)            # on elsewhere, off here
        h.model_answers("sure")
        await h.dispatch(h.turn("what's up"))
        return json.loads(json.dumps([_call(c) for c in h.calls], default=str))


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_with_the_switch_off_here_the_payload_is_byte_identical(monkeypatch, tmp_path):
    off = await _capture(monkeypatch, tmp_path / "off", setting_on=False)
    on_elsewhere = await _capture(monkeypatch, tmp_path / "on", setting_on=True)

    assert off and off == on_elsewhere


# ── through the spine: the result carries her voice notes ────────────────

def _voice_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for name, tool in _tools().items():
        if name in ("voice_note", "voice_replies"):
            reg.register(tool)
    return reg


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_voice_note_rides_the_result_even_with_no_words(harness, monkeypatch):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    harness.registry = _voice_registry()
    harness.model_calls(ToolUseBlock(id="v1", name="voice_note", input={"text": "good morning"}))
    harness.model_answers("")

    result = await harness.dispatch(harness.turn("say good morning to me"))

    assert result.voice_notes == (lane.OGG,)
    assert result.responded is True                     # T10-3: a voice note counts
    assert result.response_text == ""


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_voice_note_made_beside_a_quiet_choice_still_goes_out(monkeypatch, tmp_path):
    """Final fix wave (ruling FW-3, the review's M1, confirmed by the owner
    10-04): a voice note she made is a delivery, like a file — when her quiet
    choice stands (everything beside it went through, T2-2a), the note still
    goes, with no text. T10-5 drops only her reply's spoken form, never a note
    she made.

    Second read F8 (10-04): and her record says so. The spoken words are her
    reply in her transcript, no "quiet" row goes into her record of quiet
    choices, and the quiet call's result never says "Nothing was sent"."""
    from core.agent.session import TextBlock, ToolResultBlock

    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = _voice_registry()
    h.model_chooses_quiet("they asked for a voice message and no reply", also=[
        ToolUseBlock(id="v1", name="voice_note", input={"text": "good night"})])

    result = await h.dispatch(h.turn("send me a voice note saying good night, no reply"))

    assert result.quiet is True                         # no text goes out (T10-5)
    assert result.voice_notes == (lane.OGG,)
    assert result.responded is True                     # a voice note is a delivery (T10-3)
    assert result.response_text == ""
    msgs = await h.transcript()
    said = [b.text for m in msgs if m.role == "assistant" for b in m.content
            if isinstance(b, TextBlock)]
    assert said == ["good night"]
    assert msgs[-1].role == "assistant"
    assert h.quiet_record() == []
    results = {b.tool_use_id: b.content for m in msgs if m.role == "tool" for b in m.content
               if isinstance(b, ToolResultBlock)}
    assert "Nothing was sent" not in results["q1"]
    assert "voice message" in results["q1"]


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_voice_note_from_an_earlier_step_then_quiet_is_recorded_as_spoken(
        monkeypatch, tmp_path):
    """Second read F8: the note was made a step before her quiet choice (the
    choice on its own). It still goes out (FW-3), and the record is the same:
    her words in the transcript, no quiet row, no "Nothing was sent"."""
    from core.agent.session import TextBlock, ToolResultBlock

    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = _voice_registry()
    h.model_calls(ToolUseBlock(id="v1", name="voice_note", input={"text": "sleep well"}))
    h.model_chooses_quiet("they wanted only the voice message")

    result = await h.dispatch(h.turn("voice note please, sleep well, no reply"))

    assert result.quiet is True and result.voice_notes == (lane.OGG,)
    msgs = await h.transcript()
    said = [b.text for m in msgs if m.role == "assistant" for b in m.content
            if isinstance(b, TextBlock)]
    assert said == ["sleep well"]
    assert h.quiet_record() == []
    results = {b.tool_use_id: b.content for m in msgs if m.role == "tool" for b in m.content
               if isinstance(b, ToolResultBlock)}
    assert "Nothing was sent" not in results["q2"]


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_a_quiet_choice_with_no_voice_note_is_recorded_as_before(monkeypatch, tmp_path):
    from core.agent.session import TextBlock

    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=True)
    h.registry = _voice_registry()
    h.model_chooses_quiet("they asked me not to reply")

    result = await h.dispatch(h.turn("no need to reply"))

    assert result.quiet is True and result.voice_notes == ()
    msgs = await h.transcript()
    assert not any(isinstance(b, TextBlock) for m in msgs if m.role == "assistant"
                   for b in m.content)
    assert [r["reason"] for r in h.quiet_record()] == ["they asked me not to reply"]


@pytest.mark.asyncio
async def test_a_sidecar_with_only_a_voice_note_still_delivers(tmp_path):
    """The third gate (beside the two doors'): a sidecar's result with a voice
    note and no words is delivered, not dropped."""
    from core.agent import sidecar, task_slots as ts
    from core.turn.types import Turn, TurnMetadata, TurnResult

    ts.reset_for_tests()
    sidecar.reset_for_tests()
    delivered = []

    async def dispatch(turn):
        return TurnResult(responded=True, response_text="", decision=None,
                          reflection_applied=False, delegation_tier_used=0,
                          voice_notes=(lane.OGG,))

    async def deliver(result):
        delivered.append(result.voice_notes)

    async def on_done(reply, **kw):
        pass

    turn = Turn(surface="discord_text", owner_id=1, content="say hi", images=[],
                metadata=TurnMetadata(channel_id=7, is_dm=True, user_side_prerecorded=True),
                received_at=datetime(2026, 10, 1, 9, 0, 0))
    handle = sidecar.run_sidecar(parent_key="discord:7", turn=turn, kind="talk",
                                 dispatch=dispatch, deliver=deliver, sessions_root=tmp_path,
                                 on_done=on_done)
    await handle

    assert delivered == [(lane.OGG,)]


# ── plain words, and the participant framing (G9) ────────────────────────

def test_her_new_strings_use_plain_words_and_name_the_owner_they(voice_on):
    import re

    from core import voice_replies as vr

    family = re.compile(r"\b(holding|held|carrying|carried|carry|sitting with|landed|land|"
                        r"the shape of|circling|genuinely)\b", re.IGNORECASE)
    pronouns = re.compile(r"\b(he|him|his|himself)\b", re.IGNORECASE)
    tools = _tools()
    texts = [vr.STATUS_LINE, tools["voice_note"].description,
             tools["voice_replies"].description, tools["speak_line"].description]
    for text in texts:
        assert not family.search(text), text
        assert not pronouns.search(text), text
        assert "a person" not in text.lower(), text


# ── the 10-01 loop (DEFECT §56): one per reply, a clear finish ────────────
# 2026-10-01 20:03 — voice replies on, she made a voice note, could not tell
# how to finish, and made 51 more, each answered by the same "Voice message
# made" line. The door sent all 51.

def test_a_second_voice_note_in_one_reply_replaces_the_words_and_says_finish(
        voice_on, monkeypatch):
    """R15, Group V: the last call wins (item 31) — still one note, made at
    the end, from the words she last chose."""
    from core.agent import voice_note_final

    tts, _ = lane.install(monkeypatch)
    ctx = _ctx()
    tool = _tools()["voice_note"]

    first = _run(tool.execute({"text": "here's what I meant"}, ctx))
    second = _run(tool.execute({"text": "here's what I meant, put another way"}, ctx))
    _run(voice_note_final.finish(ctx))

    assert ctx.voice_notes == [lane.OGG]                # one, never two
    assert [c["text"] for c in tts.calls] == ["here's what I meant, put another way"]
    assert not isinstance(second, ToolFailure)          # guidance, not a failure
    assert second.startswith("Replaced")
    assert "finish now" in second.lower()
    assert "sent" not in second.lower()                 # the 5.10 lesson
    assert first != second


def test_the_result_says_what_she_said_and_how_to_finish(voice_on, monkeypatch):
    lane.install(monkeypatch)
    ctx = _ctx()

    result = _run(_tools()["voice_note"].execute({"text": "the deploy finished at noon"}, ctx))

    assert "the deploy finished at noon" in result      # progress she can see
    assert "finish now" in result.lower()
    assert "sent" not in result.lower()


def test_a_long_voice_note_is_quoted_short_in_the_result(voice_on, monkeypatch):
    lane.install(monkeypatch)
    words = "word " * 200
    result = _run(_tools()["voice_note"].execute({"text": words}, _ctx()))
    assert len(result) < 600


def test_with_voice_replies_on_here_voice_note_makes_nothing_and_points_at_her_reply(
        voice_on, monkeypatch):
    from core import voice_replies as vr

    tts, _ = lane.install(monkeypatch)
    ctx = _ctx()
    vr.set_on("discord:201", True)

    result = _run(_tools()["voice_note"].execute({"text": "hello there"}, ctx))

    assert ctx.voice_notes == []
    assert tts.calls == []
    assert not isinstance(result, ToolFailure)
    assert "just write your reply" in result.lower()
    assert "sent" not in result.lower()


def test_voice_replies_on_in_another_chat_does_not_stop_a_voice_note_here(voice_on, monkeypatch):
    from core import voice_replies as vr

    lane.install(monkeypatch)
    ctx = _ctx(channel_id=201)
    vr.set_on("discord:999", True)

    _run(_tools()["voice_note"].execute({"text": "hello there"}, ctx))

    assert ctx.voice_note_words == ["hello there"]      # noted; made at the end (V)


def test_the_status_line_says_she_needs_no_tool_while_voice_replies_are_on():
    from core import voice_replies as vr

    line = vr.STATUS_LINE.lower()
    assert "just write your reply" in line
    assert "don't need voice_note" in line
    assert "backticks" in line                          # how a number gets seen


def test_her_private_read_is_told_when_voice_replies_are_on(monkeypatch):
    """Her quick read said "No TTS requested, so text is fine" while the
    switch was on (10-01 19:57): it is told now, on the owner's chat turns."""
    from core import voice_replies as vr

    lane.voice_notes_on(monkeypatch)
    lane.her_hands_on(monkeypatch)
    assert vr.read_note_for(_owner_turn()) == ""
    vr.set_on("discord:201", True)
    note = vr.read_note_for(_owner_turn())
    assert "voice replies are on" in note.lower()
    assert vr.read_note_for(_owner_turn(channel=999)) == ""


def test_recall_hands_the_private_read_the_voice_note():
    """The seam: recall's private-read input carries read_note_for's line."""
    src = (REPO / "core" / "turn" / "recall.py").read_text(encoding="utf-8")
    assert "read_note_for(" in src
