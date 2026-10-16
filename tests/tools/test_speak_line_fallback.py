"""Batch 4, Task 11 (fix plan 7.2): a line she can't speak is sent as text,
and the result says so.

Her session transcripts hold ten speak_line calls (08-19 to 09-12): six
"Said it.", three "The voice session couldn't take the line right now." and
one "I'm not in a voice session — join a channel first." Four in ten lines
she meant him to hear went nowhere, and each came back as a plain success
string, so nothing marked them as failures.

Controller ruling R15: on BOTH failure paths (no voice connection at all, and
a session that would not take the line) the line goes to him as text on the
chat he used last (``core/doors.py::last_used_door``), through the same
custody route ``send_message`` uses (``record_outbound``), and the result is
"not spoken — no live voice session; sent as text on <door>". No chat, or a
text send that fails, is a ToolFailure saying the line was neither spoken nor
sent. "Said it." and "Nothing to say." are unchanged.

Every Discord object here is a fake; no send leaves the process.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import core.agent.builtin_tools.her_hands as hh
import core.agent.hands_runtime as hr
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext


@pytest.fixture(autouse=True)
def _clean_holder():
    hr.register_client(None)
    yield
    hr.register_client(None)


def _ctx() -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True, surface="discord_text", channel_id=201)


def _client(*, in_voice: bool):
    voice_channel = SimpleNamespace(name="General", id=101)
    voice_client = SimpleNamespace(channel=voice_channel) if in_voice else None
    guild = SimpleNamespace(name="home", voice_client=voice_client)
    hr.register_client(SimpleNamespace(guilds=[guild]))


def _tool():
    return {t.name: t for t in hh.build_her_hands_tools()}["speak_line"]


class _Recorder:
    def __init__(self, *, result=True, error: Exception | None = None):
        self.calls: list[dict] = []
        self.result = result
        self.error = error

    async def __call__(self, **kw):
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        return self.result


def _wire(monkeypatch, *, say_ok=False, door=("discord", 1483), outbound=None):
    lookups: list[int] = []

    async def _say(channel_id, text):
        return say_ok

    async def _door(owner_id):
        lookups.append(owner_id)
        return door

    outbound = outbound or _Recorder()
    monkeypatch.setattr(hh, "_say_in_session", _say)
    monkeypatch.setattr(hh, "last_used_door", _door)
    monkeypatch.setattr(hh, "_record_outbound", outbound)
    monkeypatch.setattr(hh, "_deed", lambda *a: None)
    return outbound, lookups


def _run(text="hello out loud"):
    return asyncio.run(_tool().execute({"text": text}, _ctx()))


def test_a_session_that_will_not_take_the_line_sends_it_as_text(monkeypatch):
    _client(in_voice=True)
    outbound, lookups = _wire(monkeypatch, say_ok=False, door=("discord", 1483))

    result = _run()

    assert result == "not spoken — no live voice session; sent as text on discord:1483"
    assert lookups == [1]
    assert len(outbound.calls) == 1
    call = outbound.calls[0]
    assert call["owner_id"] == 1 and call["channel_id"] == 1483
    assert call["text"] == "hello out loud"
    assert call["platform"] == "discord" and call["surface_id"] == "1483"
    assert call["origin"] == "her_hands"


def test_no_voice_connection_at_all_sends_it_as_text_too(monkeypatch):
    _client(in_voice=False)
    outbound, _ = _wire(monkeypatch, door=("telegram", 5222))

    result = _run()

    assert result == "not spoken — no live voice session; sent as text on telegram:5222"
    assert len(outbound.calls) == 1
    assert outbound.calls[0]["platform"] == "telegram"
    assert outbound.calls[0]["channel_id"] == 5222
    assert outbound.calls[0]["surface_id"] == "5222"


def test_no_client_at_all_sends_it_as_text(monkeypatch):
    outbound, _ = _wire(monkeypatch, door=("discord", 1483))

    result = _run()

    assert result == "not spoken — no live voice session; sent as text on discord:1483"
    assert len(outbound.calls) == 1


@pytest.mark.parametrize("in_voice", [True, False])
def test_no_chat_to_send_it_to_is_a_failure_and_nothing_is_sent(monkeypatch, in_voice):
    _client(in_voice=in_voice)
    outbound, _ = _wire(monkeypatch, say_ok=False, door=None)

    result = _run()

    assert isinstance(result, ToolFailure)
    assert "neither spoken nor sent" in str(result).lower()
    assert outbound.calls == []


def test_a_text_send_that_fails_is_a_failure_that_says_so(monkeypatch):
    _client(in_voice=True)
    outbound, _ = _wire(monkeypatch, say_ok=False,
                        outbound=_Recorder(error=RuntimeError("discord said no")))

    result = _run()

    assert isinstance(result, ToolFailure)
    text = str(result).lower()
    assert "neither spoken nor sent" in text and "discord:1483" in text
    assert "discord said no" in text


def test_a_text_send_that_does_not_go_out_is_a_failure(monkeypatch):
    _client(in_voice=True)
    outbound, _ = _wire(monkeypatch, say_ok=False, outbound=_Recorder(result=False))

    result = _run()

    assert isinstance(result, ToolFailure)
    assert "neither spoken nor sent" in str(result).lower()


def test_said_it_is_unchanged_and_sends_no_text(monkeypatch):
    _client(in_voice=True)

    async def _must_not_look(owner_id):
        raise AssertionError("a spoken line needs no chat")

    outbound, _ = _wire(monkeypatch, say_ok=True)
    monkeypatch.setattr(hh, "last_used_door", _must_not_look)

    assert _run() == "Said it."
    assert outbound.calls == []


@pytest.mark.parametrize("in_voice", [True, False])
def test_nothing_to_say_is_unchanged_and_sends_nothing(monkeypatch, in_voice):
    _client(in_voice=in_voice)
    outbound, lookups = _wire(monkeypatch)

    assert _run(text="   ") == "Nothing to say."
    assert outbound.calls == [] and lookups == []


# --- fix round 1, R15a: the chat he used last is THIS conversation -----------
# The 09-11 case: he talks to her in a Discord chat and she reaches for her
# voice. record_outbound would post the line into this chat AND file it in the
# running turn's own session, between the open speak_line call and its result
# (the post-run-only helper, mid-run). He would read it twice, once as the
# send and once in her reply. So nothing is sent: she is told to say it in
# her reply, the way send_message answers "that's this conversation".


def _ctx_on(surface: str, channel_id: int) -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True, surface=surface, channel_id=channel_id)


@pytest.mark.parametrize("in_voice", [True, False])
@pytest.mark.parametrize("surface,door", [
    ("discord_text", ("discord", 1483)),
    ("telegram_text", ("telegram", 1483)),
])
def test_when_the_door_is_this_conversation_nothing_is_sent(monkeypatch, in_voice, surface, door):
    _client(in_voice=in_voice)
    outbound, _ = _wire(monkeypatch, say_ok=False, door=door)

    result = asyncio.run(_tool().execute({"text": "hello out loud"}, _ctx_on(surface, 1483)))

    assert outbound.calls == []
    text = str(result).lower()
    assert "not spoken" in text
    assert "this conversation" in text and "reply" in text
    assert "sent as text" not in text


@pytest.mark.parametrize("surface,channel_id,door", [
    ("discord_text", 201, ("discord", 1483)),     # another Discord chat
    ("telegram_text", 1483, ("discord", 1483)),   # same number, other platform
    ("discord_voice", 1483, ("discord", 1483)),   # a voice turn is not a chat turn
    ("dashboard", 0, ("telegram", 5222)),
])
def test_a_door_in_another_conversation_still_gets_the_text_once(monkeypatch, surface, channel_id, door):
    _client(in_voice=True)
    outbound, _ = _wire(monkeypatch, say_ok=False, door=door)

    result = asyncio.run(_tool().execute({"text": "hello out loud"}, _ctx_on(surface, channel_id)))

    assert result == f"not spoken — no live voice session; sent as text on {door[0]}:{door[1]}"
    assert len(outbound.calls) == 1


def test_her_strings_name_the_owner_as_they_not_he(monkeypatch):
    """The owner-pronoun rule: what she reads says "they"."""
    import re

    _client(in_voice=True)

    async def _broken(owner_id):
        raise RuntimeError("lookup down")

    _wire(monkeypatch, say_ok=False)
    monkeypatch.setattr(hh, "last_used_door", _broken)
    results = [str(_run())]
    _wire(monkeypatch, say_ok=False, door=None)
    results.append(str(_run()))
    _wire(monkeypatch, say_ok=False, door=("discord", 1483))
    results.append(str(asyncio.run(_tool().execute({"text": "x"}, _ctx_on("discord_text", 1483)))))
    results.append(_tool().description)
    for text in results:
        assert not re.search(r"\b(he|him|his)\b", text, re.IGNORECASE), text


def test_the_description_says_this_conversation_means_say_it_in_the_reply():
    """Final review, minor 7 (R15a): "the line goes to them as text on the
    chat they used last" is not true when that chat is this conversation —
    nothing is sent and she is told to say it in her reply. The description
    she reads before the call says so too."""
    description = _tool().description
    assert "this very conversation" in description
    assert "say it in your reply" in description


# ── Batch 8, task 10: no voice channel → voice_note ───────────────────────

def test_with_voice_messages_on_the_description_points_to_voice_note(monkeypatch):
    """B8-5: with ``voice.voice_notes`` on, the line she can't speak in a voice
    channel has a second home — a voice message in the chat (voice_note). The
    R15a promise stays word for word."""
    from core import voice_replies

    monkeypatch.setattr(voice_replies, "voice_notes_enabled", lambda: True)
    description = _tool().description

    assert "voice_note" in description
    assert "this very conversation" in description
    assert "say it in your reply" in description
    assert description.startswith(hh.SpeakLineTool.description)


def test_with_voice_messages_off_the_description_is_todays_byte_for_byte():
    description = _tool().description

    assert description == hh.SpeakLineTool.description
    assert "voice_note" not in description
