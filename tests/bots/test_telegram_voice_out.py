"""Her reply, out loud — optional, per chat (telegram door, task 5).

Task 4 gave her ears at this door: a voice note in became a transcript.
This is the other half, and it is OFF until he asks for it. When the message
she is answering WAS a voice note and ``/voice on`` is set for THAT chat,
the words she is about to send are also spoken through the configured TTS,
converted to the OGG/Opus a Telegram voice note has to be, and sent after
the text.

Three rules run underneath every test here:

  * the voice note rides ALONGSIDE the text, never instead of it — the words
    are the reply, and they go first;
  * a failure to make the audio says NOTHING. No note, no apology: he gets
    the text reply he would have got anyway, and she does not narrate her
    own plumbing;
  * the switch is one chat's. A guest's voice note is answered in text.

Nothing here imports ``telegram``, runs ffmpeg, or reaches a TTS: the bot is
a fake that records what it was handed, the speech facade is a fake with the
facade's one method under its exact keywords, and ffmpeg is a callable that
writes the file it was asked to write. Both are patched at their own modules,
so everything BETWEEN them is the real code.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.turn.types import Turn, TurnMetadata

APP_OWNER_ID = 1
OWNER_TELEGRAM_ID = 777
STRANGER_ID = 4242
BOT_ID = 999
BOT_USERNAME = "palli_test_bot"
CHAT = 424242
OTHER_CHAT = 515151
SESSION = f"telegram:{CHAT}"
VOICE_FLAG = "voice_replies"

WAV = b"RIFF\x00\x00\x00\x00WAVEfake-pcm"
OGG = b"OggS\x00fake-opus-bytes"


# ── fakes ────────────────────────────────────────────────────────────────
class FakeTTS:
    """Stands in for voice.speech.voice_speech_service — the ONE method the
    voice-out path uses, with the facade's exact keyword shape."""

    def __init__(self, audio: bytes | None = WAV, *,
                 boom: BaseException | None = None, timeline: list | None = None):
        self.audio = audio
        self.boom = boom
        # A shared list (the bot's own call log) so a test can see WHEN the
        # synthesis happened relative to the sends — the order is the fix.
        self.timeline = timeline
        self.calls: list[dict] = []

    async def synthesize_speech(self, *, text, encoding="linear16",
                                sample_rate=48000, container=None):
        self.calls.append({"text": text, "encoding": encoding,
                           "sample_rate": sample_rate, "container": container})
        if self.timeline is not None:
            self.timeline.append("tts")
        if self.boom is not None:
            raise self.boom
        return self.audio


class FakeFfmpeg:
    """``core.ffmpeg.run_ffmpeg_checked``'s shape: it writes the file it was
    asked for, or it raises the way the real checked runner does."""

    def __init__(self, *, produces: bytes | None = OGG,
                 boom: Exception | None = None):
        self.produces = produces
        self.boom = boom
        self.commands: list[list[str]] = []
        self.timeouts: list[float | None] = []
        self.in_path: str | None = None
        self.out_path: str | None = None

    async def __call__(self, args, *, timeout_seconds=None):
        argv = [str(a) for a in args]
        self.commands.append(argv)
        self.timeouts.append(timeout_seconds)
        self.in_path = argv[argv.index("-i") + 1]
        self.out_path = next((a for a in argv if a.endswith(".ogg")), None)
        if self.boom is not None:
            raise self.boom
        if self.produces is not None and self.out_path:
            with open(self.out_path, "wb") as handle:
                handle.write(self.produces)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class RetryAfter(Exception):
    """The shape telegram.error.RetryAfter has, under its own name."""

    def __init__(self, retry_after):
        super().__init__(f"flood control: retry in {retry_after}")
        self.retry_after = retry_after


class FakeBot:
    def __init__(self, *, voice_fails_with=None, voice_fails_always=False):
        self.messages: list[dict] = []
        self.voices: list[dict] = []
        self.actions: list[dict] = []
        self.calls: list[str] = []
        self._voice_fails_with = voice_fails_with
        self._voice_fails_always = voice_fails_always

    async def send_message(self, *, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})
        self.calls.append("message")

    async def send_voice(self, *, chat_id, voice, **kw):
        if self._voice_fails_with is not None:
            exc = self._voice_fails_with
            if not self._voice_fails_always:
                self._voice_fails_with = None
            raise exc
        self.voices.append({"chat_id": chat_id, "voice": bytes(voice), **kw})
        self.calls.append("voice")

    async def send_chat_action(self, *, chat_id, action, **kw):
        self.actions.append({"chat_id": chat_id, "action": action})


class FakeApplication:
    def __init__(self, bot):
        self.handlers: list = []
        self.bot = bot

    def add_handler(self, handler):
        self.handlers.append(handler)


# ── fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def clean_flags():
    """No test inherits another test's switch — turned off the way ``/voice
    off`` turns it off, through the store's own door."""
    from bots.common.steering import set_session_flag

    def _off():
        for chat_id in (CHAT, OTHER_CHAT):
            set_session_flag(f"telegram:{chat_id}", VOICE_FLAG, False)

    _off()
    yield
    _off()


@pytest.fixture
def voice_lane(monkeypatch):
    """The two ends of the voice lane, faked at their own modules: the speech
    facade singleton and the bounded ffmpeg runner. Returns both so a test
    can say what they do and read back what they were asked."""
    import core.ffmpeg as _ffmpeg
    import voice.speech as _speech

    tts = FakeTTS()
    ffmpeg = FakeFfmpeg()

    def _install(new_tts=None, new_ffmpeg=None):
        nonlocal tts, ffmpeg
        if new_tts is not None:
            tts = new_tts
        if new_ffmpeg is not None:
            ffmpeg = new_ffmpeg
        monkeypatch.setattr(_speech, "voice_speech_service", tts)
        monkeypatch.setattr(_ffmpeg, "run_ffmpeg_checked", ffmpeg)
        return tts, ffmpeg

    _install()
    return SimpleNamespace(tts=lambda: tts, ffmpeg=lambda: ffmpeg,
                           install=_install)


@pytest.fixture
def notices(monkeypatch):
    """What she FEELS about the last mile (§15), recorded instead of fired."""
    from bots.telegram import reply

    fired: list[dict] = []
    monkeypatch.setattr(reply, "_delivery_notice",
                        lambda kind, **fields: fired.append({"kind": kind, **fields}))
    return fired


@pytest.fixture
def no_spacing(monkeypatch):
    from bots.telegram import reply

    monkeypatch.setattr(reply, "CHUNK_SPACING_SECONDS", 0.0)
    return reply


@pytest.fixture
def slept(monkeypatch):
    """Record every sleep instead of taking it."""
    from bots.telegram import reply

    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def _sleep(seconds, *a, **kw):
        waits.append(float(seconds))
        return await real_sleep(0)

    monkeypatch.setattr(reply.asyncio, "sleep", _sleep)
    return waits


# ── the synthesis itself (platform-blind) ────────────────────────────────
@pytest.mark.asyncio
async def test_her_words_come_back_as_opus_bytes():
    """The whole lane in one: the facade's WAV, one bounded ffmpeg run, the
    OGG bytes a voice note carries."""
    from bots.common.voice_note import synthesize_voice_note

    tts = FakeTTS()
    ffmpeg = FakeFfmpeg()

    out = await synthesize_voice_note("the deploy finished", service=tts,
                                      ffmpeg=ffmpeg)

    assert out == OGG
    # The facade's exact keyword shape — a WAV at 48 kHz, which is what the
    # conversion below expects to find on disk.
    assert tts.calls == [{"text": "the deploy finished", "encoding": "linear16",
                          "sample_rate": 48000, "container": "wav"}]
    command = ffmpeg.commands[0]
    assert command[0] == "ffmpeg"
    assert "libopus" in command                  # Telegram plays nothing else
    assert command[command.index("-ac") + 1] == "1"
    assert ffmpeg.timeouts == [60.0]             # bounded, never a hung door


@pytest.mark.asyncio
async def test_the_temp_files_never_outlive_the_send():
    """Not deleted afterwards — held in a directory that cannot outlive the
    call, so a raising write or a killed conversion leaves nothing either."""
    from bots.common.voice_note import synthesize_voice_note

    ffmpeg = FakeFfmpeg()
    await synthesize_voice_note("gone by the time you read this",
                                service=FakeTTS(), ffmpeg=ffmpeg)

    assert ffmpeg.in_path and ffmpeg.out_path
    assert os.path.dirname(ffmpeg.in_path) == os.path.dirname(ffmpeg.out_path)
    assert not os.path.exists(ffmpeg.in_path)
    assert not os.path.exists(ffmpeg.out_path)
    assert not os.path.exists(os.path.dirname(ffmpeg.in_path))


@pytest.mark.asyncio
async def test_the_tts_lane_off_is_no_voice_note_and_no_ffmpeg():
    """``synthesize_speech`` returns None when the TTS lane is off. That is
    not an error, and it must not cost an ffmpeg run."""
    from bots.common.voice_note import synthesize_voice_note

    ffmpeg = FakeFfmpeg()
    out = await synthesize_voice_note("into the void", service=FakeTTS(None),
                                      ffmpeg=ffmpeg)

    assert out is None
    assert ffmpeg.commands == []


@pytest.mark.asyncio
async def test_a_raising_tts_never_escapes():
    from bots.common.voice_note import synthesize_voice_note

    out = await synthesize_voice_note(
        "still a reply", service=FakeTTS(boom=RuntimeError("lemonfox is down")),
        ffmpeg=FakeFfmpeg())

    assert out is None


@pytest.mark.asyncio
async def test_ffmpeg_failing_leaves_nothing_behind():
    from bots.common.voice_note import synthesize_voice_note

    ffmpeg = FakeFfmpeg(boom=RuntimeError("ffmpeg failed (code=1)"))
    out = await synthesize_voice_note("no opus today", service=FakeTTS(),
                                      ffmpeg=ffmpeg)

    assert out is None
    assert ffmpeg.in_path and not os.path.exists(ffmpeg.in_path)
    assert not os.path.exists(os.path.dirname(ffmpeg.in_path))


@pytest.mark.asyncio
async def test_ffmpeg_producing_nothing_is_not_an_empty_voice_note():
    from bots.common.voice_note import synthesize_voice_note

    out = await synthesize_voice_note("silence", service=FakeTTS(),
                                      ffmpeg=FakeFfmpeg(produces=None))

    assert out is None


@pytest.mark.asyncio
async def test_nothing_to_say_is_never_synthesized():
    from bots.common.voice_note import synthesize_voice_note

    tts = FakeTTS()
    assert await synthesize_voice_note("   ", service=tts,
                                       ffmpeg=FakeFfmpeg()) is None
    assert await synthesize_voice_note(None, service=tts,
                                       ffmpeg=FakeFfmpeg()) is None
    assert tts.calls == []


@pytest.mark.asyncio
async def test_by_default_it_speaks_through_the_configured_facade(voice_lane):
    """No injection: the production path reaches the speech singleton and the
    bounded ffmpeg runner, which is what the door calls."""
    from bots.common.voice_note import synthesize_voice_note

    out = await synthesize_voice_note("through the facade")

    assert out == OGG
    assert voice_lane.tts().calls[0]["text"] == "through the facade"
    assert voice_lane.ffmpeg().commands


# ── the send (bots/telegram/reply.py) ────────────────────────────────────
@pytest.mark.asyncio
async def test_the_voice_note_lands_after_the_last_chunk_of_text(no_spacing, slept):
    """Alongside, never instead: the words go first, all of them, and the
    audio is the last thing to arrive."""
    reply = no_spacing
    bot = FakeBot()
    text = "a" * 4096 + "b" * 100

    await reply.send_turn_reply(bot, CHAT, text, voice_note=OGG)

    assert bot.calls == ["message", "message", "voice"]
    assert bot.voices[0] == {"chat_id": CHAT, "voice": OGG}


@pytest.mark.asyncio
async def test_flood_control_on_the_voice_note_is_honoured_once(no_spacing, slept):
    reply = no_spacing
    bot = FakeBot(voice_fails_with=RetryAfter(7))

    await reply.send_turn_reply(bot, CHAT, "here", voice_note=OGG)

    assert bot.calls == ["message", "voice"]
    assert 7.0 in slept


@pytest.mark.asyncio
async def test_a_voice_note_that_will_not_send_never_costs_him_the_text(no_spacing):
    """The words landed; only the audio didn't. Nothing raises into the
    client and the text is not re-sent or apologised for."""
    reply = no_spacing
    bot = FakeBot(voice_fails_with=RuntimeError("telegram said no"),
                  voice_fails_always=True)

    await reply.send_turn_reply(bot, CHAT, "the answer", voice_note=OGG)

    assert [m["text"] for m in bot.messages] == ["the answer"]
    assert bot.voices == []


@pytest.mark.asyncio
async def test_a_dropped_voice_note_is_felt_as_itself(no_spacing, notices):
    """§15 with the truth attached: not "a file never attached — too big for
    Discord", which is not what happened. Her own kind, her own phrase."""
    reply = no_spacing
    bot = FakeBot(voice_fails_with=RuntimeError("telegram said no"),
                  voice_fails_always=True)

    await reply.send_turn_reply(bot, CHAT, "the answer", voice_note=OGG)

    assert [n["kind"] for n in notices] == ["voice_note_dropped"]
    assert notices[0]["reason"] == "voice_send_failed"


def test_the_dropped_voice_note_has_a_lived_phrase():
    from ai.present_moment import _EVENT_PHRASES, _humanize_events

    assert "voice_note_dropped" in _EVENT_PHRASES    # a kind of her own
    rendered = _humanize_events([{"kind": "voice_note_dropped"}])

    assert "voice_note_dropped" not in rendered      # no raw enum reaches her
    assert rendered.strip() != "- voice note dropped"    # not the bare degrade
    assert "voice" in rendered.lower()
    assert "Discord" not in rendered                 # it was not Discord's wall


@pytest.mark.asyncio
async def test_a_voice_note_with_no_text_of_its_own_still_goes(no_spacing):
    """How the cadenced path sends it: the beats already went out, and the
    voice note follows them on its own."""
    reply = no_spacing
    bot = FakeBot()

    await reply.send_turn_reply(bot, CHAT, "", voice_note=OGG)

    assert bot.calls == ["voice"]


@pytest.mark.asyncio
async def test_no_voice_note_is_the_reply_task_2_already_sent(no_spacing):
    reply = no_spacing
    bot = FakeBot()

    await reply.send_turn_reply(bot, CHAT, "just words")

    assert bot.calls == ["message"]
    assert bot.voices == []


# ── the decision (bots/telegram/client.py) ───────────────────────────────
def _client(bot):
    from bots.common.guest_gate import GuestGate
    from bots.telegram.client import PalliTelegramClient

    client = PalliTelegramClient(
        application=FakeApplication(bot),
        bot_user_id=BOT_ID,
        bot_username=BOT_USERNAME,
        owner_id=APP_OWNER_ID,
        guest_gate=GuestGate(),
    )

    async def _owner_tg_id():
        return OWNER_TELEGRAM_ID

    client._owner_telegram_id = _owner_tg_id
    return client


async def _drain_guests(client):
    """Await the guest turns the door now holds in the BACKGROUND.

    Since the final wave's I2 fix the handler returns while a stranger's turn
    is still running (python-telegram-bot processes updates sequentially, so
    awaiting one there stalled every chat on the door), and a test that wants
    to see the END of one waits on the set the client holds it in.
    """
    tasks = list(getattr(client, "_guest_tasks", ()) or ())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)          # let the done-callbacks discard


def _result(text="on it", *, beats=()):
    from core.turn.types import Beat, CadencedReply, TurnResult

    cadenced = None
    if beats:
        cadenced = CadencedReply(
            beats=tuple(Beat(text=b, delay_after_previous_seconds=0.0)
                        for b in beats),
            total_text=text,
        )
    return TurnResult(responded=True, response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0,
                      cadenced_reply=cadenced)


def _voice_turn(content="can you check the server", *, owner_id=APP_OWNER_ID,
                chat_id=CHAT, is_voice=True):
    return Turn(
        surface="telegram_text",
        owner_id=owner_id,
        content=content,
        images=[],
        metadata=TurnMetadata(channel_id=chat_id, is_dm=True, is_voice=is_voice),
    )


def _voice_on(chat_id=CHAT):
    from bots.common.steering import set_session_flag

    set_session_flag(f"telegram:{chat_id}", VOICE_FLAG, True)


@pytest.mark.asyncio
async def test_a_voice_note_in_earns_one_back_when_he_asked_for_it(
        voice_lane, no_spacing):
    """End to end through the burst: his voice note is the turn, ``/voice on``
    is this chat's switch, and her reply arrives as words AND audio."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    client._burst_fragments[CHAT] = [_voice_turn()]
    client._burst_authors[CHAT] = {OWNER_TELEGRAM_ID}

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result("it's back up"))):
        await client._fire_burst(CHAT)

    assert [m["text"] for m in bot.messages] == ["it's back up"]
    assert bot.calls == ["message", "voice"]
    assert bot.voices[0]["voice"] == OGG
    assert voice_lane.tts().calls[0]["text"] == "it's back up"


@pytest.mark.asyncio
async def test_the_words_go_out_before_the_tts_is_even_asked(voice_lane, no_spacing):
    """Synthesis is a network call and an ffmpeg run — seconds, sometimes a
    minute. His words do not wait behind it: they go first, the audio
    follows, and the chat says she is still busy while it is made."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    voice_lane.install(FakeTTS(timeline=bot.calls))

    await client._deliver_result(CHAT, _result("it's back up"), is_voice=True)

    assert bot.calls == ["message", "tts", "voice"]
    assert any(a["action"] == "typing" for a in bot.actions)


@pytest.mark.asyncio
async def test_the_beats_go_out_before_the_tts_too(voice_lane, no_spacing):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    voice_lane.install(FakeTTS(timeline=bot.calls))

    await client._deliver_result(
        CHAT, _result("hey. give me a second.", beats=["hey.", "give me a second."]),
        is_voice=True)

    assert bot.calls == ["message", "message", "tts", "voice"]


@pytest.mark.asyncio
async def test_a_barge_in_during_the_synthesis_costs_only_the_voice(
        voice_lane, no_spacing):
    """The delivery runs inside the cancellable in-flight task. A cancel
    while the TTS is out must not take the reply he is already reading."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    voice_lane.install(FakeTTS(boom=asyncio.CancelledError(), timeline=bot.calls))

    with pytest.raises(asyncio.CancelledError):
        await client._deliver_result(CHAT, _result("it's back up"), is_voice=True)

    assert [m["text"] for m in bot.messages] == ["it's back up"]
    assert bot.voices == []
    assert bot.calls == ["message", "tts"]


@pytest.mark.asyncio
async def test_voice_replies_are_off_until_he_turns_them_on(voice_lane, no_spacing):
    bot = FakeBot()
    client = _client(bot)
    client._burst_fragments[CHAT] = [_voice_turn()]
    client._burst_authors[CHAT] = {OWNER_TELEGRAM_ID}

    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result("it's back up"))):
        await client._fire_burst(CHAT)

    assert bot.calls == ["message"]
    assert voice_lane.tts().calls == []          # never even asked


@pytest.mark.asyncio
async def test_a_typed_message_never_gets_a_voice_reply(voice_lane, no_spacing):
    """The switch is about answering VOICE with voice. A typed message with
    the switch on is still a typed conversation — and it is the same default
    that keeps a sidecar's result (which answers no message of his) silent."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()

    await client._deliver_result(CHAT, _result("typed back"))

    assert bot.calls == ["message"]
    assert voice_lane.tts().calls == []


@pytest.mark.asyncio
async def test_the_switch_belongs_to_one_chat(voice_lane, no_spacing):
    bot = FakeBot()
    client = _client(bot)
    _voice_on(OTHER_CHAT)

    await client._deliver_result(CHAT, _result("not here"), is_voice=True)

    assert bot.calls == ["message"]
    assert voice_lane.tts().calls == []


@pytest.mark.asyncio
async def test_when_the_speech_cannot_be_made_the_text_goes_alone(
        voice_lane, no_spacing):
    """A TTS that gives nothing back is not something he hears about: the
    reply he gets is exactly the reply he would have got anyway."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    voice_lane.install(FakeTTS(None))

    await client._deliver_result(CHAT, _result("the log is clean"), is_voice=True)

    assert [m["text"] for m in bot.messages] == ["the log is clean"]
    assert bot.voices == []
    assert "voice" not in bot.messages[0]["text"].lower()


@pytest.mark.asyncio
async def test_ffmpeg_failing_still_answers_him_in_words(voice_lane, no_spacing):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    voice_lane.install(None, FakeFfmpeg(boom=RuntimeError("ffmpeg failed (code=1)")))

    await client._deliver_result(CHAT, _result("the log is clean"), is_voice=True)

    assert [m["text"] for m in bot.messages] == ["the log is clean"]
    assert bot.voices == []


def _guest_turn(content="[stranger]: who are you?", *, is_voice=True):
    return Turn(
        surface="telegram_text",
        owner_id=STRANGER_ID,
        content=content,
        images=[],
        metadata=TurnMetadata(channel_id=CHAT, is_dm=False, is_voice=is_voice,
                              origin="guest", extra={"telegram_username": "stranger"}),
    )


async def _answer_a_guest(client, turn, reply_text):
    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result(reply_text))):
        await client._handle_guest_message(SimpleNamespace(turn=turn), CHAT)
        await _drain_guests(client)


@pytest.mark.asyncio
async def test_a_guests_voice_note_earns_one_back_where_voice_is_on(voice_lane, no_spacing):
    """``/voice`` stays the owner's switch (test_voice_is_the_owners_switch).
    While it is on in this chat, a guest's voice note earns one back as his
    does — the owner, 10-12: guests' messages are "normal messages, minus
    permissions"."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()

    await _answer_a_guest(client, _guest_turn(), "I'm PaLLi.")

    assert [m["text"] for m in bot.messages] == ["I'm PaLLi."]
    assert [v["voice"] for v in bot.voices] == [OGG]
    assert voice_lane.tts().calls[0]["text"] == "I'm PaLLi."


@pytest.mark.asyncio
async def test_a_guests_typed_message_is_answered_in_words_where_voice_answers_voice(
        voice_lane, no_spacing):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()

    await _answer_a_guest(client, _guest_turn(is_voice=False), "I'm PaLLi.")

    assert [m["text"] for m in bot.messages] == ["I'm PaLLi."]
    assert bot.voices == []
    assert voice_lane.tts().calls == []


@pytest.mark.asyncio
async def test_a_long_reply_is_spoken_as_its_first_message(voice_lane, no_spacing):
    """Her whole answer is in the text. The voice note is the first Telegram
    message of it — nobody listens to a ten-minute one."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    long_text = "a" * 4096 + "b" * 900

    await client._deliver_result(CHAT, _result(long_text), is_voice=True)

    assert [len(m["text"]) for m in bot.messages] == [4096, 900]
    assert voice_lane.tts().calls[0]["text"] == "a" * 4096
    assert bot.calls == ["message", "message", "voice"]


@pytest.mark.asyncio
async def test_a_cadenced_reply_still_gets_its_voice_after_the_last_beat(
        voice_lane, no_spacing):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()

    await client._deliver_result(
        CHAT, _result("hey. give me a second.", beats=["hey.", "give me a second."]),
        is_voice=True)

    assert bot.calls == ["message", "message", "voice"]
    assert [m["text"] for m in bot.messages] == ["hey.", "give me a second."]
    assert bot.voices[0]["voice"] == OGG


@pytest.mark.asyncio
async def test_a_reply_with_nothing_in_it_is_not_spoken(voice_lane, no_spacing):
    from core.turn.types import TurnResult

    bot = FakeBot()
    client = _client(bot)
    _voice_on()

    await client._deliver_result(
        CHAT, TurnResult(responded=False, response_text="", decision=None,
                         reflection_applied=False, delegation_tier_used=0),
        is_voice=True)

    assert bot.calls == []
    assert voice_lane.tts().calls == []


# ═════════════════════════════════════════════════════════════════════════
# Batch 8, task 10 (B8-5; rulings T10-2..T10-6): voice messages, properly.
#
# Everything above is today's door and stays green as a pin of the setting
# OFF — the suite holds ``voice.voice_notes`` off (tests/conftest.py). Below,
# the setting is ON: ``/voice`` and her ``voice_replies`` tool flip ONE
# switch per chat, kept across restarts, and while it is on her replies
# there go as voice, with no text unless she made a voice note herself
# (then her text reply is the short line beside it). The old "voice answers
# voice" lane stands down.
# ═════════════════════════════════════════════════════════════════════════

@pytest.fixture
def voice_notes_on(monkeypatch):
    from core import voice_replies

    monkeypatch.setattr(voice_replies, "voice_notes_enabled", lambda: True)


@pytest.fixture
def persisted(monkeypatch):
    from bots.telegram import client as client_mod

    mark = AsyncMock()
    monkeypatch.setattr(client_mod, "persist_delivery_failure", mark)
    return mark


def _result_with(text="on it", *, voice_notes=(), quiet=False, responded=True):
    from dataclasses import replace

    return replace(_result(text), voice_notes=tuple(voice_notes), quiet=quiet,
                   responded=responded)


@pytest.mark.asyncio
async def test_with_the_setting_on_a_typed_message_gets_a_voice_reply_and_no_text(
        voice_notes_on, voice_lane, no_spacing, persisted):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    voice_lane.install(FakeTTS(timeline=bot.calls))

    await client._deliver_result(CHAT, _result("it's back up"))

    assert bot.calls == ["tts", "voice"]
    assert bot.messages == []
    assert bot.voices[0]["voice"] == OGG
    assert voice_lane.tts().calls[0]["text"] == "it's back up"
    assert any(a["action"] == "typing" for a in bot.actions)    # while it is made
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_the_setting_on_a_long_reply_is_spoken_whole(voice_notes_on, voice_lane,
                                                                no_spacing):
    """No text goes, so the voice carries all of it — not just the first
    message's worth, as the old lane did."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    long_text = "a" * 4096 + "b" * 900

    await client._deliver_result(CHAT, _result(long_text))

    assert voice_lane.tts().calls[0]["text"] == long_text
    assert bot.calls == ["voice"]


@pytest.mark.asyncio
async def test_with_the_setting_on_her_voice_note_goes_beside_her_text_line(
        voice_notes_on, voice_lane, no_spacing, persisted):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()

    await client._deliver_result(CHAT, _result_with("code: 4417", voice_notes=(OGG,)))

    assert bot.calls == ["message", "voice"]
    assert [m["text"] for m in bot.messages] == ["code: 4417"]
    assert voice_lane.tts().calls == []                # made at tool time, never twice
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_the_setting_on_her_voice_note_goes_out_with_the_switch_off_too(
        voice_notes_on, voice_lane, no_spacing):
    bot = FakeBot()
    client = _client(bot)

    await client._deliver_result(CHAT, _result_with("morning!", voice_notes=(OGG,)))

    assert bot.calls == ["message", "voice"]


@pytest.mark.asyncio
async def test_with_the_setting_on_the_old_voice_answers_voice_lane_stands_down(
        voice_notes_on, voice_lane, no_spacing):
    """A voice note in, the switch off: text only — the answer-voice-with-
    voice rule is today's flag-off behaviour, not the new switch's."""
    bot = FakeBot()
    client = _client(bot)

    await client._deliver_result(CHAT, _result("it's back up"), is_voice=True)

    assert bot.calls == ["message"]
    assert voice_lane.tts().calls == []


@pytest.mark.asyncio
async def test_with_the_setting_on_when_the_speech_cannot_be_made_the_text_goes_alone(
        voice_notes_on, voice_lane, no_spacing, persisted):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    voice_lane.install(FakeTTS(None))

    await client._deliver_result(CHAT, _result("the log is clean"), is_voice=True)

    assert [m["text"] for m in bot.messages] == ["the log is clean"]
    assert bot.voices == []
    assert len(voice_lane.tts().calls) == 1            # one try, never a second lane's
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_the_setting_on_a_voice_only_reply_that_fails_to_send_is_marked(
        voice_notes_on, voice_lane, no_spacing, notices, persisted):
    bot = FakeBot(voice_fails_with=RuntimeError("telegram said no"), voice_fails_always=True)
    client = _client(bot)
    _voice_on()

    await client._deliver_result(CHAT, _result("the plan is X"))

    assert bot.messages == []
    persisted.assert_awaited_once_with(
        owner_id=APP_OWNER_ID, platform="telegram", channel_id=CHAT,
        reason="RuntimeError", failed_text="the plan is X", landed=0, total=1,
        sessions_root=client._sessions_root)
    assert [n["kind"] for n in notices] == ["voice_note_dropped"]


@pytest.mark.asyncio
async def test_with_the_setting_on_a_text_line_without_its_voice_is_partly_delivered(
        voice_notes_on, voice_lane, no_spacing, persisted):
    bot = FakeBot(voice_fails_with=RuntimeError("telegram said no"), voice_fails_always=True)
    client = _client(bot)

    await client._deliver_result(CHAT, _result_with("code: 4417", voice_notes=(OGG,)))

    kw = persisted.await_args.kwargs
    assert (kw["failed_text"], kw["landed"], kw["total"]) == ("code: 4417", 1, 2)


@pytest.mark.asyncio
async def test_with_the_setting_on_quiet_sends_no_voice(voice_notes_on, voice_lane, no_spacing):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()

    await client._deliver_result(CHAT, _result_with("", responded=False, quiet=True),
                                 is_voice=True)

    assert bot.calls == []
    assert voice_lane.tts().calls == []


@pytest.mark.asyncio
async def test_with_the_setting_on_a_voice_note_made_beside_a_quiet_choice_goes_alone(
        voice_notes_on, voice_lane, no_spacing, persisted):
    """Final fix wave (ruling FW-3): the note she made goes, with no words and
    no spoken reply."""
    bot = FakeBot()
    client = _client(bot)
    _voice_on()

    await client._deliver_result(CHAT, _result_with("", voice_notes=(OGG,), quiet=True),
                                 is_voice=True)

    assert bot.calls == ["voice"]
    assert bot.messages == []
    assert bot.voices[0]["voice"] == OGG
    assert voice_lane.tts().calls == []
    persisted.assert_not_awaited()


# (A guest's reply with the setting on: spoken too since 10-12 —
# test_with_the_setting_on_a_guests_reply_is_spoken_too, at the end.)


# ── /voice and her voice_replies: one switch, kept across restarts ───────

def _voice_command(*, owner=True):
    from bots.common.commands import Caller
    from bots.telegram import commands

    caller = Caller(is_owner=owner, user_id=1 if owner else STRANGER_ID,
                    platform_user_id=OWNER_TELEGRAM_ID if owner else STRANGER_ID,
                    platform="telegram")
    message = SimpleNamespace(chat=SimpleNamespace(id=CHAT, type="private"))

    async def run(word):
        return await commands._voice(None, caller, message, [word] if word else [])

    return run


@pytest.mark.asyncio
async def test_with_the_setting_on_voice_and_her_tool_flip_the_same_switch(voice_notes_on):
    from core import voice_replies as vr
    from core.agent.builtin_tools.her_hands import build_her_hands_tools
    from core.agent.tools import ToolContext

    voice = _voice_command()
    tool = {t.name: t for t in build_her_hands_tools()}["voice_replies"]
    ctx = ToolContext(turn_owner_id=APP_OWNER_ID, is_owner=True,
                      surface="telegram_text", channel_id=CHAT)

    await voice("on")
    assert vr.is_on(SESSION) is True
    assert "on" in await tool.execute({"state": "status"}, ctx)

    await tool.execute({"state": "off"}, ctx)
    assert "off" in (await voice(""))
    assert vr.is_on(SESSION) is False


@pytest.mark.asyncio
async def test_with_the_setting_on_the_switch_survives_a_restart(voice_notes_on):
    from bots.common import steering
    from core import voice_replies as vr

    await _voice_command()("on")
    steering._SESSION_FLAGS.clear()                    # what a restart forgets

    assert vr.VoiceRepliesStore().is_on(SESSION) is True
    assert steering.get_session_flag(SESSION, VOICE_FLAG) is True


@pytest.mark.asyncio
async def test_with_the_setting_on_voice_says_what_the_switch_now_does(voice_notes_on,
                                                                       monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "effective_tts_provider", "lemonfox", raising=False)

    line = await _voice_command()("on")

    assert line == (
        "Voice replies are on in this chat. While they're on, my replies here "
        "come as voice messages, with a short text line when there's something "
        "to read. The switch stays as you set it — a restart doesn't change it.")
    assert "resets when I restart" not in line


@pytest.mark.asyncio
async def test_with_the_setting_on_voice_still_says_when_no_voice_is_configured(
        voice_notes_on, monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "effective_tts_provider", "off", raising=False)

    line = await _voice_command()("on")

    assert "no voice is configured on this host" in line
    assert "resets when I restart" not in line


@pytest.mark.asyncio
async def test_with_the_setting_on_voice_is_still_the_owners_switch(voice_notes_on):
    from core import voice_replies as vr

    await _voice_command(owner=False)("on")

    assert vr.is_on(SESSION) is False


# ── a guest's reply, spoken too (the owner, 10-12) ────────────────────────
@pytest.mark.asyncio
async def test_with_the_setting_on_a_guests_reply_is_spoken_too(
        voice_notes_on, voice_lane, no_spacing, persisted):
    bot = FakeBot()
    client = _client(bot)
    _voice_on()
    voice_lane.install(FakeTTS(timeline=bot.calls))

    await _answer_a_guest(client, _guest_turn("[stranger]: hi", is_voice=False), "hey")

    assert bot.calls == ["tts", "voice"]
    assert bot.messages == []
    assert voice_lane.tts().calls[0]["text"] == "hey"
    persisted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_guests_spoken_reply_that_fails_to_send_marks_their_record(
        voice_notes_on, voice_lane, no_spacing, notices, persisted):
    bot = FakeBot(voice_fails_with=RuntimeError("telegram said no"), voice_fails_always=True)
    client = _client(bot)
    _voice_on()

    await _answer_a_guest(client, _guest_turn("[stranger]: hi", is_voice=False), "hey")

    persisted.assert_awaited_once_with(
        owner_id=STRANGER_ID, platform="telegram", channel_id=CHAT,
        reason="RuntimeError", failed_text="hey", landed=0, total=1,
        sessions_root=client._sessions_root)


@pytest.mark.asyncio
async def test_with_the_setting_on_and_the_switch_off_a_guest_gets_words(
        voice_notes_on, voice_lane, no_spacing, persisted):
    bot = FakeBot()
    client = _client(bot)

    await _answer_a_guest(client, _guest_turn("[stranger]: hi", is_voice=False), "hey")

    assert [m["text"] for m in bot.messages] == ["hey"]
    assert bot.voices == []
    persisted.assert_not_awaited()
