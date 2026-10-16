"""Voice messages on the Discord door (Batch 8, task 10 — B8-5; T10-3..T10-9).

Discord's half of "voice messages, properly":

- a voice note she made (``voice_note``) goes out as an ``.ogg`` attachment —
  discord.py can't send the waveform kind, and an ``.ogg`` shows as an audio
  player — written to a temporary directory around the send and gone after;
  her text reply goes beside it as a text message;
- while voice replies are on for this conversation and she made no voice
  note, the door speaks her whole reply and sends no text (owner O2,
  INTERIM); if the speech can't be made, the text goes alone, as today;
- quiet speaks nothing (T10-5); a voice note she made beside a quiet choice
  still goes, with no words (final fix wave, ruling FW-3);
- a voice message that fails to send is a delivery part that did not
  arrive: her record is marked, like failed text (fix plan 5.10);
- with ``voice.voice_notes`` off, the door is today's, byte for byte, and the
  switch is never read.

Every Discord object is a fake; the speech facade and ffmpeg are fakes at
their own modules (tests/agent/voice_lane_fakes.py), so the lane between
them is the real code. Stand-in names only.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent.session import ToolUseBlock
from tests.agent import voice_lane_fakes as lane
from tests.agent.she_decides_harness import DM_CHANNEL, DmHarness

CHANNEL = 555
KEY = f"discord:{CHANNEL}"
_REAL_SLEEP = asyncio.sleep

_UTCNOW_DEBT = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


class HTTPException(Exception):
    """discord.HTTPException's shape: an HTTP status and Discord's code."""

    def __init__(self, status: int, code: int):
        super().__init__(f"{status} Missing Permissions (error code: {code})")
        self.status = status
        self.code = code


def _channel(*, files_fail: bool = False, text_fails: bool = False, timeline=None):
    """A discord.Channel-like fake that records what each send carried —
    the file's name, its bytes and where it was on disk (read at send time,
    before the temporary directory goes)."""
    channel = MagicMock()
    channel.id = CHANNEL
    channel.sent = []

    async def send(*args, **kwargs):
        files = list(kwargs.get("files") or ([kwargs["file"]] if kwargs.get("file") else []))
        content = kwargs.get("content", args[0] if args else None)
        if files and files_fail:
            raise HTTPException(403, 50013)
        if not files and text_fails:
            raise HTTPException(403, 50013)
        carried = []
        for f in files:
            path = getattr(f.fp, "name", "")
            carried.append({"name": f.filename, "data": f.fp.read(), "path": path})
        channel.sent.append({"content": content, "files": carried})
        if timeline is not None:
            timeline.append("voice" if carried else "message")

    channel.send = AsyncMock(side_effect=send)
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(
        side_effect=lambda *a: timeline.append("typing") if timeline is not None else None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    return channel


class _AsyncioWithoutSleep:
    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(seconds, *a, **kw):
        await _REAL_SLEEP(0)


@pytest.fixture
def reply(monkeypatch):
    from bots.discord import reply as r

    monkeypatch.setattr(r, "_calculate_typing_delay", lambda t: 0.0)
    monkeypatch.setattr(r, "asyncio", _AsyncioWithoutSleep())
    fired: list[dict] = []
    monkeypatch.setattr(r, "_delivery_notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    return fired


@pytest.fixture
def notices(monkeypatch):
    """What she feels about a voice message that didn't go (§15)."""
    from bots.discord import voice_reply

    fired: list[dict] = []
    monkeypatch.setattr(voice_reply, "_delivery_notice",
                        lambda kind, **f: fired.append({"kind": kind, **f}))
    return fired


@pytest.fixture
def persisted(monkeypatch):
    from bots.discord import client as client_mod

    mark = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", mark)
    return mark


def _client(monkeypatch, tmp_path, channel):
    import discord

    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    from bots.discord.client import PalliDiscordClient

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())
    client.get_channel = lambda cid: channel
    return client


def _result(text="the deploy finished", *, voice_notes=(), attachments=(), beats=(),
            quiet=False, responded=True):
    from core.turn.types import Beat, CadencedReply, TurnResult

    cadenced = None
    if beats:
        cadenced = CadencedReply(beats=tuple(Beat(text=b, delay_after_previous_seconds=0.0)
                                             for b in beats),
                                 total_text=text, attachments=tuple(attachments))
    return TurnResult(responded=responded, response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0,
                      cadenced_reply=cadenced, attachments=tuple(attachments),
                      voice_notes=tuple(voice_notes), quiet=quiet)


def _switch_on(key=KEY):
    from core import voice_replies as vr

    vr.set_on(key, True)


# ── the switch on: her whole reply, spoken ────────────────────────────────

@pytest.mark.asyncio
async def test_with_the_switch_on_her_whole_reply_goes_as_one_voice_message_and_no_text(
        reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    timeline: list[str] = []
    tts, _ = lane.install(monkeypatch, tts=lane.FakeTTS(timeline=timeline))
    channel = _channel(timeline=timeline)
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(CHANNEL, _result())

    assert [s["content"] for s in channel.sent] == [None]          # no text at all
    voice = channel.sent[0]["files"]
    assert len(voice) == 1 and voice[0]["data"] == lane.OGG
    assert voice[0]["name"].endswith(".ogg")
    assert tts.calls[0]["text"] == "the deploy finished"
    assert timeline[:2] == ["typing", "tts"]                        # "typing…" while it is made
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_cadenced_reply_is_spoken_whole_as_one_message(reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("hey. give me a second.", beats=["hey.", "give me a second."]))

    assert [s["content"] for s in channel.sent] == [None]
    assert [c["text"] for c in tts.calls] == ["hey. give me a second."]


@pytest.mark.asyncio
async def test_her_files_still_go_beside_a_spoken_reply(reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    report = tmp_path / "report.txt"
    report.write_text("numbers", encoding="utf-8")
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("here's the report", attachments=(str(report),)))

    names = [[f["name"] for f in s["files"]] for s in channel.sent]
    assert names == [["report.txt"], [channel.sent[1]["files"][0]["name"]]]
    assert channel.sent[1]["files"][0]["data"] == lane.OGG
    assert all(not s["content"] for s in channel.sent)             # still no text
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_when_the_speech_cannot_be_made_the_text_goes_alone(reply, persisted, monkeypatch,
                                                                   tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch, tts=lane.FakeTTS(None))
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(CHANNEL, _result("the log is clean"))

    assert channel.sent == [{"content": "the log is clean", "files": []}]
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_switch_belongs_to_one_conversation(reply, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on("discord:999")

    await _client(monkeypatch, tmp_path, channel)._deliver_result(CHANNEL, _result("not here"))

    assert channel.sent == [{"content": "not here", "files": []}]
    assert tts.calls == []


# ── her own voice note: the text reply is the line beside it ──────────────

@pytest.mark.asyncio
async def test_a_voice_note_she_made_goes_beside_her_text_line(reply, persisted, monkeypatch,
                                                                tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("https://example.com/a", voice_notes=(lane.OGG,)))

    assert channel.sent[0] == {"content": "https://example.com/a", "files": []}
    assert channel.sent[1]["content"] is None
    assert channel.sent[1]["files"][0]["data"] == lane.OGG
    assert tts.calls == []                         # made at tool time, never twice
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_voice_note_she_made_goes_out_with_the_switch_off_too(reply, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    channel = _channel()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("morning!", voice_notes=(lane.OGG,)))

    assert [s["content"] for s in channel.sent] == ["morning!", None]
    assert channel.sent[1]["files"][0]["data"] == lane.OGG


@pytest.mark.asyncio
async def test_a_voice_note_with_no_words_goes_alone(reply, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    channel = _channel()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("", voice_notes=(lane.OGG,)))

    assert len(channel.sent) == 1 and channel.sent[0]["content"] is None


@pytest.mark.asyncio
async def test_the_ogg_never_outlives_the_send(reply, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(CHANNEL, _result())

    path = channel.sent[0]["files"][0]["path"]
    assert path and path.endswith(".ogg")
    assert not os.path.exists(path)
    assert not os.path.exists(os.path.dirname(path))


# ── quiet is global (T10-5) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quiet_sends_no_voice_and_speaks_nothing(reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("", responded=False, quiet=True))

    assert channel.sent == []
    assert tts.calls == []


@pytest.mark.asyncio
async def test_a_file_from_before_a_quiet_choice_goes_with_no_words_and_no_voice(
        reply, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    report = tmp_path / "notes.txt"
    report.write_text("x", encoding="utf-8")
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("", attachments=(str(report),), quiet=True))

    assert [[f["name"] for f in s["files"]] for s in channel.sent] == [["notes.txt"]]
    assert tts.calls == []


@pytest.mark.asyncio
async def test_a_voice_note_made_beside_a_quiet_choice_goes_with_no_words(
        reply, persisted, monkeypatch, tmp_path):
    """Final fix wave (ruling FW-3): the note she made goes; her reply is not
    spoken, and no text goes."""
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("", voice_notes=(lane.OGG,), quiet=True))

    assert [s["content"] for s in channel.sent] == [None]
    assert [f["data"] for f in channel.sent[0]["files"]] == [lane.OGG]
    assert tts.calls == []
    persisted.assert_not_awaited()


# ── a voice message that didn't go is marked (fix plan 5.10) ─────────────

@pytest.mark.asyncio
async def test_a_voice_only_reply_that_fails_to_send_is_recorded_as_undelivered(
        reply, notices, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    channel = _channel(files_fail=True)
    _switch_on()
    client = _client(monkeypatch, tmp_path, channel)

    await client._deliver_result(CHANNEL, _result("the plan is X"))

    assert channel.sent == []                       # no text stood in for it
    persisted.assert_awaited_once_with(
        owner_id=1, platform="discord", channel_id=CHANNEL, reason="HTTPException 50013",
        failed_text="the plan is X", landed=0, total=1, sessions_root=client._sessions_root)
    assert [n["kind"] for n in notices] == ["voice_note_dropped"]


@pytest.mark.asyncio
async def test_a_text_line_that_went_with_a_voice_that_did_not_is_partly_delivered(
        reply, notices, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    channel = _channel(files_fail=True)

    client = _client(monkeypatch, tmp_path, channel)
    await client._deliver_result(CHANNEL, _result("code: 4417", voice_notes=(lane.OGG,)))

    assert [s["content"] for s in channel.sent] == ["code: 4417"]
    kw = persisted.await_args.kwargs
    assert (kw["failed_text"], kw["landed"], kw["total"]) == ("code: 4417", 1, 2)
    assert kw["reason"] == "HTTPException 50013"


@pytest.mark.asyncio
async def test_a_voice_message_that_cannot_be_written_is_counted_and_felt_never_raised(
        reply, notices, persisted, monkeypatch, tmp_path):
    """No room on disk for the temporary .ogg: the voice part did not go, her
    record says so, and the door does not die of it."""
    import tempfile

    lane.voice_notes_on(monkeypatch)
    channel = _channel()

    def _no_room(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", _no_room)
    client = _client(monkeypatch, tmp_path, channel)

    await client._deliver_result(CHANNEL, _result("code: 4417", voice_notes=(lane.OGG,)))

    assert [s["content"] for s in channel.sent] == ["code: 4417"]
    kw = persisted.await_args.kwargs
    assert (kw["landed"], kw["total"], kw["reason"]) == (1, 2, "OSError")
    assert [n["kind"] for n in notices] == ["voice_note_dropped"]


# ── the setting off: today's door ────────────────────────────────────────

@pytest.mark.asyncio
async def test_with_the_setting_off_the_switch_is_never_read_and_today_is_unchanged(
        reply, persisted, monkeypatch, tmp_path):
    from core import voice_replies as vr

    tts, _ = lane.install(monkeypatch)
    vr.store_path().parent.mkdir(parents=True, exist_ok=True)
    vr.store_path().write_text('{"discord:555": true}', encoding="utf-8")

    def _never(*a, **k):
        raise AssertionError("the switch was read with the setting off")

    monkeypatch.setattr(vr, "is_on", _never)
    monkeypatch.setattr(vr.VoiceRepliesStore, "is_on", _never)
    channel = _channel()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(CHANNEL, _result("typed back"))

    assert channel.sent == [{"content": "typed back", "files": []}]
    assert tts.calls == []
    persisted.assert_not_awaited()


# ── through the spine ────────────────────────────────────────────────────

def _voice_registry():
    import core.agent.builtin_tools.her_hands as hh
    from core.agent.tools import ToolRegistry

    reg = ToolRegistry()
    for tool in hh.build_her_hands_tools():
        if tool.name in ("voice_note", "voice_replies"):
            reg.register(tool)
    return reg


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_end_to_end_her_voice_note_and_her_text_line_reach_the_channel(
        reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=False)
    h.registry = _voice_registry()
    h.model_calls(ToolUseBlock(id="v1", name="voice_note",
                               input={"text": "here's the link you asked for"}))
    h.model_answers("https://example.com/a")
    channel = _channel()

    result = await h.dispatch(h.turn("send me the link, out loud"))
    client = _client(monkeypatch, tmp_path / "door", channel)
    await client._deliver_result(DM_CHANNEL, result)

    assert [s["content"] for s in channel.sent] == ["https://example.com/a", None]
    assert channel.sent[1]["files"][0]["data"] == lane.OGG
    assert [c["text"] for c in tts.calls] == ["here's the link you asked for"]


@_UTCNOW_DEBT
@pytest.mark.asyncio
async def test_end_to_end_she_turns_voice_replies_on_and_the_next_reply_is_spoken(
        reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.her_hands_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    h = DmHarness(monkeypatch, tmp_path, switch_on=False)
    h.registry = _voice_registry()
    h.model_calls(ToolUseBlock(id="r1", name="voice_replies", input={"state": "on"}))
    h.model_answers("okay — voice from here on")
    h.model_answers("it's sunny")
    channel = _channel()
    client = _client(monkeypatch, tmp_path / "door", channel)

    first = await h.dispatch(h.turn("send me voice messages until I say otherwise"))
    await client._deliver_result(DM_CHANNEL, first)
    second = await h.dispatch(h.turn("what's the weather"))
    await client._deliver_result(DM_CHANNEL, second)

    from core import voice_replies as vr

    assert vr.STATUS_LINE in str(h.calls[-1].get("trailing_system_note") or "")
    assert [s["content"] for s in channel.sent] == [None, None]     # both spoken, no text
    assert [c["text"] for c in tts.calls] == ["okay — voice from here on", "it's sunny"]


# ── the 10-01 loop (DEFECT §56): one voice message per reply ─────────────

@pytest.mark.asyncio
async def test_only_the_first_of_many_voice_notes_goes_out(reply, persisted, monkeypatch, tmp_path):
    """20:03 — one run made 51 voice notes and the door sent every one. A
    reply carries ONE voice message, whatever the run queued."""
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    channel = _channel()
    first, second, third = b"OggS-first", b"OggS-second", b"OggS-third"

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("", voice_notes=(first, second, third)))

    voices = [f["data"] for s in channel.sent for f in s["files"]]
    assert voices == [first]
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_the_switch_on_a_link_in_her_reply_goes_as_the_text_line(
        reply, persisted, monkeypatch, tmp_path):
    """No tool needed: she writes the link in her reply, the door says it is
    in the text and sends it as the short line beside the voice."""
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("It's OpenAI's job page: https://example.com/jobs/1."))

    assert channel.sent[0] == {"content": "https://example.com/jobs/1", "files": []}
    assert channel.sent[1]["files"][0]["data"] == lane.OGG
    assert "example.com" not in tts.calls[0]["text"]
    assert "(link in the text)" in tts.calls[0]["text"]
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_the_switch_on_numbers_in_backticks_are_spoken_and_shown(
        reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("Ours for tomorrow: `38 40 42 56 63`."))

    assert channel.sent[0]["content"] == "38 40 42 56 63"
    assert tts.calls[0]["text"] == "Ours for tomorrow: 38 40 42 56 63."


@pytest.mark.asyncio
async def test_with_the_switch_on_a_reply_that_is_only_a_link_goes_as_text(
        reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _client(monkeypatch, tmp_path, channel)._deliver_result(
        CHANNEL, _result("https://example.com/only"))

    assert [s["content"] for s in channel.sent] == ["https://example.com/only"]
    assert all(not s["files"] for s in channel.sent)
    assert tts.calls == []


# ── a guest's reply, spoken too (the owner, 10-12) ────────────────────────
# Guests' messages are "normal messages, minus permissions": the switch stays
# the owner's to flip (her tool, never a guest's — guests have no tools), but
# while it is on in a room, her reply to a guest there is spoken as his is.
# A part that does not arrive marks THEIR record (their id, this room).

GUEST_ID = 4242


def _guest_turn(text="[Sam]: hello"):
    from datetime import datetime

    from core.turn.types import Turn, TurnMetadata

    return Turn(surface="discord_text", owner_id=GUEST_ID, content=text, images=[],
                metadata=TurnMetadata(channel_id=CHANNEL, is_dm=False, origin="guest"),
                received_at=datetime(2026, 10, 12, 17, 20, 0))


async def _answer_guest(monkeypatch, tmp_path, channel, result):
    from unittest.mock import patch

    client = _client(monkeypatch, tmp_path, channel)
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=result)):
        await client._answer_guest(_guest_turn(), channel_id=CHANNEL, author_id=GUEST_ID)


@pytest.mark.asyncio
async def test_with_the_switch_on_a_guests_reply_is_spoken_too(
        reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()
    _switch_on()

    await _answer_guest(monkeypatch, tmp_path, channel, _result("hey Sam"))

    assert [s["content"] for s in channel.sent] == [None]          # spoken, no text
    assert channel.sent[0]["files"][0]["data"] == lane.OGG
    assert tts.calls[0]["text"] == "hey Sam"
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_guests_spoken_reply_that_does_not_arrive_marks_their_record(
        reply, persisted, notices, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    lane.install(monkeypatch)
    channel = _channel(files_fail=True)
    _switch_on()

    await _answer_guest(monkeypatch, tmp_path, channel, _result("hey Sam"))

    kw = persisted.await_args.kwargs
    assert kw["owner_id"] == GUEST_ID                  # theirs, never his
    assert kw["channel_id"] == CHANNEL
    assert (kw["landed"], kw["total"]) == (0, 1)


@pytest.mark.asyncio
async def test_with_the_switch_off_a_guest_gets_her_words_as_before(
        reply, persisted, monkeypatch, tmp_path):
    lane.voice_notes_on(monkeypatch)
    tts, _ = lane.install(monkeypatch)
    channel = _channel()

    await _answer_guest(monkeypatch, tmp_path, channel, _result("hey Sam"))

    assert [s["content"] for s in channel.sent] == ["hey Sam"]
    assert tts.calls == []
    persisted.assert_not_awaited()
