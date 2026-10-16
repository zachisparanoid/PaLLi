"""Telegram voice notes, heard (telegram door, task 4).

Task 2 only MARKED a voice note: intake set ``metadata.is_voice`` and the
placeholder content, and the turn reached the model as "[VOICE_MESSAGE]" —
honest, but deaf. This is the other half: the bytes come down through the
same authenticated download every other Telegram file uses, go through the
PROVIDER-AGNOSTIC speech facade (``voice.speech.voice_speech_service``
.transcribe_attachment_bytes — ffmpeg to 16 kHz mono WAV, then whichever
STT is configured), and the transcript becomes the turn's content.

The 2026-06-11 law runs underneath every test here: a voice-only message
must NEVER produce an empty user turn. That record poisoned a whole agent
session and 400'd every call after it. No transcript — no provider, the
lane off, the download failed, audio over the cap, the service raising —
is the honest marker, never nothing.

Nothing here imports ``telegram``, touches the network, or runs ffmpeg: the
bot is a fake whose ``get_file`` hands back bytes, and the speech service is
a fake object with the facade's one method.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.turn.types import Turn, TurnMetadata

OWNER_TELEGRAM_ID = 777
STRANGER_ID = 4242
APP_OWNER_ID = 1
BOT_ID = 999
BOT_USERNAME = "palli_test_bot"
CHAT = 424242

OGG = b"OggS-fake-voice-bytes"


# ── fakes ────────────────────────────────────────────────────────────────
class FakeFile:
    def __init__(self, data: bytes, *, boom: bool = False):
        self._data = data
        self._boom = boom

    async def download_as_bytearray(self):
        if self._boom:
            raise RuntimeError("telegram hiccup")
        return bytearray(self._data)


class FakeBot:
    """Records get_file calls; never touches the network."""

    def __init__(self, files: dict | None = None):
        self.files = dict(files or {})
        self.asked: list[str] = []
        self.messages: list[dict] = []
        self.actions: list[dict] = []

    async def get_file(self, file_id):
        self.asked.append(str(file_id))
        try:
            return self.files[str(file_id)]
        except KeyError:
            raise RuntimeError(f"no such file {file_id}")

    async def send_message(self, *, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text})

    async def send_chat_action(self, *, chat_id, action, **kw):
        self.actions.append({"chat_id": chat_id, "action": action})


class FakeService:
    """Stands in for voice.speech.voice_speech_service — the ONE method the
    voice-in path uses, with the facade's exact keyword shape."""

    def __init__(self, transcript: str | None = None, *, boom: Exception | None = None):
        self.transcript = transcript
        self.boom = boom
        self.calls: list[dict] = []

    async def transcribe_attachment_bytes(self, *, audio_bytes, filename="voice.ogg"):
        self.calls.append({"audio_bytes": bytes(audio_bytes), "filename": filename})
        if self.boom is not None:
            raise self.boom
        return self.transcript


def _voice_turn(content=None, *, file_id="voice-1", filename="voice.ogg",
                mime="audio/ogg", size=len(OGG), bucket="voice",
                is_voice=True, extra_files=()):
    from bots.common.voice_note import VOICE_PLACEHOLDER_CONTENT

    files = [{"bucket": bucket, "file_id": file_id, "file_unique_id": "u-voice-1",
              "filename": filename, "mime": mime, "size": size}]
    files.extend(extra_files)
    return Turn(
        surface="telegram_text",
        owner_id=APP_OWNER_ID,
        content=VOICE_PLACEHOLDER_CONTENT if content is None else content,
        images=[],
        metadata=TurnMetadata(
            channel_id=CHAT,
            is_dm=True,
            is_voice=is_voice,
            extra={"telegram_files": files},
        ),
    )


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_INBOX_DIR", str(tmp_path / "inbox"))
    return tmp_path / "inbox"


# ── the transcript becomes the turn ──────────────────────────────────────
@pytest.mark.asyncio
async def test_the_transcript_becomes_the_turns_content(inbox):
    from bots.telegram import intake

    bot = FakeBot({"voice-1": FakeFile(OGG)})
    service = FakeService("can you check the server")

    out = await intake.resolve_voice_turn(_voice_turn(), bot, service=service)

    assert out.content == "can you check the server"
    assert out.metadata.is_voice is True
    assert bot.asked == ["voice-1"]                  # ONE download, the shared one
    assert service.calls[0]["audio_bytes"] == OGG
    assert service.calls[0]["filename"].endswith(".ogg")


@pytest.mark.asyncio
async def test_no_transcript_is_the_honest_marker_never_an_empty_turn(inbox):
    """The 2026-06-11 outage rule. An empty user row is what poisoned the
    session; the marker reads as an event, so she can say she couldn't make
    the audio out."""
    from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT
    from bots.telegram import intake

    bot = FakeBot({"voice-1": FakeFile(OGG)})

    out = await intake.resolve_voice_turn(_voice_turn(), bot, service=FakeService(None))

    assert out.content == TRANSCRIPTION_UNAVAILABLE_CONTENT
    assert out.content.strip()


@pytest.mark.asyncio
async def test_a_service_that_raises_is_the_marker_too(inbox):
    from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT
    from bots.telegram import intake

    bot = FakeBot({"voice-1": FakeFile(OGG)})
    service = FakeService(boom=RuntimeError("stt is down"))

    out = await intake.resolve_voice_turn(_voice_turn(), bot, service=service)

    assert out.content == TRANSCRIPTION_UNAVAILABLE_CONTENT


@pytest.mark.asyncio
async def test_a_caption_is_kept_and_the_transcript_follows_it(inbox):
    """His typed words are never clobbered — the Discord shape, exactly."""
    from bots.telegram import intake

    bot = FakeBot({"voice-1": FakeFile(OGG)})
    service = FakeService("the deploy failed twice")

    out = await intake.resolve_voice_turn(
        _voice_turn("context for the audio:"), bot, service=service)

    assert out.content == (
        "context for the audio:\n\n[voice message transcript] the deploy failed twice")


@pytest.mark.asyncio
async def test_a_caption_survives_a_failed_transcription(inbox):
    from bots.telegram import intake

    bot = FakeBot({"voice-1": FakeFile(OGG)})

    out = await intake.resolve_voice_turn(
        _voice_turn("typed caption"), bot, service=FakeService(None))

    assert out.content == "typed caption"


@pytest.mark.asyncio
async def test_an_audio_file_routes_with_its_own_mime(inbox):
    """``message.audio`` is not a voice note — it is a file he sent that
    happens to be audio (mp3, m4a). The facade decides what to do with the
    container from the name it is handed, so the mime has to reach it."""
    from bots.telegram import intake

    bot = FakeBot({"song-1": FakeFile(b"ID3-fake-mp3")})
    service = FakeService("this is the voicemail they left")

    out = await intake.resolve_voice_turn(
        _voice_turn(file_id="song-1", filename="voicemail", mime="audio/mpeg",
                    size=12),
        bot, service=service)

    assert out.content == "this is the voicemail they left"
    assert service.calls[0]["filename"].endswith(".mp3")


@pytest.mark.asyncio
async def test_a_download_that_fails_is_the_marker(inbox):
    from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT
    from bots.telegram import intake

    bot = FakeBot({"voice-1": FakeFile(b"", boom=True)})
    service = FakeService("unreachable")

    out = await intake.resolve_voice_turn(_voice_turn(), bot, service=service)

    assert out.content == TRANSCRIPTION_UNAVAILABLE_CONTENT
    assert service.calls == []                        # nothing to transcribe


@pytest.mark.asyncio
async def test_audio_over_the_cap_is_never_even_fetched(inbox):
    """Telegram's own bot download wall is 20 MB; asking for more gets an
    error, not a file. And she says WHICH failure it was: audio that was
    never fetched is not audio she could not make out."""
    from bots.common.voice_note import TRANSCRIPTION_TOO_LARGE_CONTENT
    from bots.telegram import intake
    from core.turn.inbox import INBOX_MAX_BYTES

    assert intake.TELEGRAM_VOICE_MAX_BYTES == min(20 * 1024 * 1024,
                                                  INBOX_MAX_BYTES)

    bot = FakeBot({"voice-1": FakeFile(OGG)})
    service = FakeService("never reached")

    out = await intake.resolve_voice_turn(
        _voice_turn(size=intake.TELEGRAM_VOICE_MAX_BYTES + 1), bot,
        service=service)

    assert bot.asked == []
    assert service.calls == []
    assert out.content == TRANSCRIPTION_TOO_LARGE_CONTENT


@pytest.mark.asyncio
async def test_audio_that_arrives_over_the_cap_is_refused_and_discarded(
        inbox, monkeypatch):
    """``file_size`` is Telegram's word for it, not a measurement. A file
    that lied gets refused on arrival — and the copy she just told him she
    could not take does not stay in the inbox."""
    from bots.common.voice_note import TRANSCRIPTION_TOO_LARGE_CONTENT
    from bots.telegram import intake

    monkeypatch.setattr(intake, "TELEGRAM_VOICE_MAX_BYTES", 8)
    bot = FakeBot({"voice-1": FakeFile(b"much longer than eight bytes")})
    service = FakeService("never reached")

    out = await intake.resolve_voice_turn(_voice_turn(size=None), bot,
                                          service=service)

    assert bot.asked == ["voice-1"]                 # it lied; we found out
    assert service.calls == []
    assert out.content == TRANSCRIPTION_TOO_LARGE_CONTENT
    assert list((inbox / "telegram").glob("*")) == []


def test_the_voice_cap_never_exceeds_the_inbox_cap(monkeypatch):
    """The voice bytes land in the inbox like every other attachment, so a
    voice cap above the inbox's wall would be a promise made in one place
    and broken in another (review round 1, minor 5)."""
    import importlib

    import core.turn.inbox as inbox_module
    from bots.telegram import intake as intake_module

    monkeypatch.setenv("PALLI_INBOX_MAX_BYTES", str(4 * 1024 * 1024))
    try:
        importlib.reload(inbox_module)
        importlib.reload(intake_module)
        assert inbox_module.INBOX_MAX_BYTES == 4 * 1024 * 1024
        assert intake_module.TELEGRAM_VOICE_MAX_BYTES == 4 * 1024 * 1024
        assert intake_module.TELEGRAM_MAX_FILE_BYTES == 4 * 1024 * 1024
    finally:
        monkeypatch.undo()
        importlib.reload(inbox_module)
        importlib.reload(intake_module)

    assert intake_module.TELEGRAM_VOICE_MAX_BYTES == min(
        20 * 1024 * 1024, inbox_module.INBOX_MAX_BYTES)


@pytest.mark.asyncio
async def test_a_turn_with_no_voice_note_is_handed_straight_back(inbox):
    from bots.telegram import intake

    turn = _voice_turn("just words", bucket="file", filename="notes.txt",
                       mime="text/plain", is_voice=False)
    bot = FakeBot({})

    out = await intake.resolve_voice_turn(turn, bot, service=FakeService("nope"))

    assert out is turn
    assert bot.asked == []


@pytest.mark.asyncio
async def test_materialize_leaves_the_voice_note_to_the_voice_path(inbox):
    """One owner for the voice file: the voice path. Materialization would
    otherwise write its own 'could not be fetched' line onto a turn whose
    honest line is the marker — two sentences about one failure."""
    from bots.common.voice_note import VOICE_PLACEHOLDER_CONTENT
    from bots.telegram import intake

    bot = FakeBot({"voice-1": FakeFile(OGG)})

    out = await intake.materialize_attachments(_voice_turn(), bot)

    assert bot.asked == []
    assert out.content == VOICE_PLACEHOLDER_CONTENT
    assert out.metadata.file_attachments == ()
    assert out.metadata.text_attachments == ()


@pytest.mark.asyncio
async def test_a_photo_beside_the_voice_note_still_materializes(inbox):
    """Skipping the voice bucket must not skip anything else on the turn."""
    from bots.telegram import intake

    turn = _voice_turn(
        "look at this",
        extra_files=[{"bucket": "image", "file_id": "photo-1",
                      "file_unique_id": "u-p", "filename": "photo.jpg",
                      "mime": "image/jpeg", "size": 4}],
    )
    bot = FakeBot({"photo-1": FakeFile(b"\xff\xd8\xff\xd9")})

    out = await intake.materialize_attachments(turn, bot)

    assert bot.asked == ["photo-1"]
    assert len(out.images) == 1
    assert out.images[0].local_path.startswith("file://")


# ── the shared engine: bots/common/voice_note.py ─────────────────────────
@pytest.mark.asyncio
async def test_transcribe_note_uses_the_configured_speech_service(monkeypatch):
    """Production passes no service: the default is the singleton facade,
    imported lazily so a chat client never drags the voice stack in."""
    import voice.speech as speech
    from bots.common.voice_note import transcribe_note

    service = FakeService("through the facade")
    monkeypatch.setattr(speech, "voice_speech_service", service)

    out = await transcribe_note(OGG, mime="audio/ogg", filename="voice.ogg")

    assert out == "through the facade"
    assert service.calls == [{"audio_bytes": OGG, "filename": "voice.ogg"}]


@pytest.mark.asyncio
async def test_transcribe_note_is_none_when_the_stt_lane_is_off():
    """``transcribe_file`` returns None when boot turned STT off; the facade
    passes that through, and so does this."""
    from bots.common.voice_note import transcribe_note

    assert await transcribe_note(OGG, mime="audio/ogg",
                                 service=FakeService(None)) is None


@pytest.mark.asyncio
async def test_transcribe_note_never_sends_empty_audio():
    from bots.common.voice_note import transcribe_note

    service = FakeService("unreachable")
    assert await transcribe_note(b"", mime="audio/ogg", service=service) is None
    assert service.calls == []


@pytest.mark.asyncio
async def test_transcribe_note_strips_and_empties_to_none():
    from bots.common.voice_note import transcribe_note

    assert await transcribe_note(OGG, mime="audio/ogg",
                                 service=FakeService("   ")) is None
    assert await transcribe_note(OGG, mime="audio/ogg",
                                 service=FakeService(" hey  ")) == "hey"


def test_resolve_voice_content_is_the_one_resolution_both_doors_use():
    from bots.common.voice_note import (
        TRANSCRIPTION_UNAVAILABLE_CONTENT,
        VOICE_PLACEHOLDER_CONTENT,
        resolve_voice_content,
    )

    assert resolve_voice_content("hey", VOICE_PLACEHOLDER_CONTENT) == "hey"
    assert resolve_voice_content("hey", "") == "hey"
    assert resolve_voice_content("hey", "caption") == (
        "caption\n\n[voice message transcript] hey")
    assert resolve_voice_content(None, VOICE_PLACEHOLDER_CONTENT) == (
        TRANSCRIPTION_UNAVAILABLE_CONTENT)
    assert resolve_voice_content(None, "") == TRANSCRIPTION_UNAVAILABLE_CONTENT
    assert resolve_voice_content(None, "caption") == "caption"


def test_the_marker_names_which_failure_it_was():
    """The default keeps Discord's two-argument calls — and their strings —
    exactly as they were."""
    from bots.common.voice_note import (
        TRANSCRIPTION_TOO_LARGE_CONTENT,
        TRANSCRIPTION_UNAVAILABLE_CONTENT,
        VOICE_PLACEHOLDER_CONTENT,
        resolve_voice_content,
    )

    assert TRANSCRIPTION_TOO_LARGE_CONTENT == (
        "[voice message received — too large to transcribe]")
    assert resolve_voice_content(
        None, VOICE_PLACEHOLDER_CONTENT,
        marker=TRANSCRIPTION_TOO_LARGE_CONTENT) == TRANSCRIPTION_TOO_LARGE_CONTENT
    # A caption still wins over either marker, and a transcript ignores both.
    assert resolve_voice_content(
        None, "caption", marker=TRANSCRIPTION_TOO_LARGE_CONTENT) == "caption"
    assert resolve_voice_content(
        "heard it", VOICE_PLACEHOLDER_CONTENT,
        marker=TRANSCRIPTION_TOO_LARGE_CONTENT) == "heard it"
    assert resolve_voice_content(None, VOICE_PLACEHOLDER_CONTENT) == (
        TRANSCRIPTION_UNAVAILABLE_CONTENT)


# ── Discord's path, unchanged ────────────────────────────────────────────
def _discord_voice_message(content: str = ""):
    att = MagicMock()
    att.content_type = "audio/ogg"
    att.read = AsyncMock(return_value=b"OggS-fake-bytes")
    msg = MagicMock()
    msg.content = content
    msg.attachments = [att]
    return msg


def _fake_stt(transcript):
    stt = MagicMock()
    stt.transcribe_audio = AsyncMock(return_value=transcript)
    return stt


@pytest.mark.asyncio
async def test_discord_voice_resolution_still_produces_the_same_strings():
    """The pin on the lift: the resolution moved into bots/common, so the
    three strings Discord has produced since 2026-06-11 are asserted here
    against the values themselves, not against the helper that makes them."""
    from bots.discord.voice_attachment import (
        VOICE_PLACEHOLDER_CONTENT,
        resolve_voice_turn_content,
    )

    assert await resolve_voice_turn_content(
        _discord_voice_message(), current_content=VOICE_PLACEHOLDER_CONTENT,
        stt=_fake_stt("can you check the server"),
    ) == "can you check the server"

    assert await resolve_voice_turn_content(
        _discord_voice_message(), current_content=VOICE_PLACEHOLDER_CONTENT,
        stt=_fake_stt(None),
    ) == "[voice message received — transcription unavailable]"

    assert await resolve_voice_turn_content(
        _discord_voice_message(), current_content="context for the audio:",
        stt=_fake_stt("the deploy failed twice"),
    ) == "context for the audio:\n\n[voice message transcript] the deploy failed twice"


# ── the door itself ──────────────────────────────────────────────────────
class FakeUser:
    def __init__(self, id, *, username=None, full_name=None, is_bot=False):
        self.id = id
        self.username = username
        self.full_name = full_name
        self.is_bot = is_bot


class FakeChat:
    def __init__(self, id=CHAT, type="private"):
        self.id = id
        self.type = type
        self.title = None


class FakeVoice:
    def __init__(self, *, file_id="voice-1", file_unique_id="u-voice-1",
                 mime_type="audio/ogg", file_size=len(OGG), duration=3):
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.mime_type = mime_type
        self.file_size = file_size
        self.duration = duration


class FakeAudio:
    """``message.audio`` — an audio FILE he sent, not a note he recorded.
    Telegram gives it a file_name and, usually, a mime_type."""

    def __init__(self, *, file_id="song-1", file_unique_id="u-song-1",
                 mime_type="audio/mpeg", file_name="voicemail.mp3",
                 file_size=4096, duration=42):
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.mime_type = mime_type
        self.file_name = file_name
        self.file_size = file_size
        self.duration = duration


class FakeMessage:
    def __init__(self, *, voice=None, audio=None, caption=None,
                 author_id=OWNER_TELEGRAM_ID, username="dana_example"):
        self.chat = FakeChat()
        self.from_user = FakeUser(author_id, username=username, full_name="Dana")
        self.text = None
        self.caption = caption
        self.entities = []
        self.caption_entities = []
        self.reply_to_message = None
        self.document = None
        self.photo = []
        self.voice = voice
        self.audio = audio
        self.message_id = 17


class FakeUpdate:
    def __init__(self, message):
        self.message = message
        self.edited_message = None
        self.channel_post = None
        self.effective_message = message


class FakeApplication:
    def __init__(self, bot):
        self.handlers: list = []
        self.bot = bot

    def add_handler(self, handler):
        self.handlers.append(handler)


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


def _result(text="on it"):
    from core.turn.types import TurnResult

    return TurnResult(responded=True, response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0)


@contextmanager
def _no_model():
    """The fragment recorder, held. Dispatch and the reply are stubbed with
    it: the burst coalescer schedules a fire as soon as a message is noted,
    and a test must not be one timer away from a real model call."""
    with patch("bots.telegram.client.record_user_fragment",
               new=AsyncMock()) as rec, \
         patch("bots.telegram.client.dispatch_turn", new=AsyncMock()), \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()):
        yield rec


@pytest.mark.asyncio
async def test_the_recorded_fragment_carries_his_words_not_the_placeholder(
        inbox, monkeypatch):
    """The whole point of resolving BEFORE the record: the fragment written
    to his transcript on arrival must be what he SAID. Discord awaits its
    resolution in the same window for the same reason."""
    import voice.speech as speech

    monkeypatch.setattr(speech, "voice_speech_service",
                        FakeService("can you check the server"))
    bot = FakeBot({"voice-1": FakeFile(OGG)})
    client = _client(bot)
    update = FakeUpdate(FakeMessage(voice=FakeVoice()))

    with _no_model() as rec:
        await client.on_message(update)

    recorded = rec.await_args.args[0]
    assert recorded.content == "can you check the server"
    assert recorded.metadata.is_voice is True
    assert client._burst_fragments[CHAT][0].content == "can you check the server"


@pytest.mark.asyncio
async def test_a_voice_note_she_cannot_hear_still_reaches_her_honestly(
        inbox, monkeypatch):
    import voice.speech as speech
    from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT

    monkeypatch.setattr(speech, "voice_speech_service", FakeService(None))
    bot = FakeBot({"voice-1": FakeFile(OGG)})
    client = _client(bot)

    with _no_model() as rec:
        await client.on_message(FakeUpdate(FakeMessage(voice=FakeVoice())))

    assert rec.await_args.args[0].content == TRANSCRIPTION_UNAVAILABLE_CONTENT


@pytest.mark.asyncio
async def test_his_caption_on_the_voice_note_reaches_her_too(inbox, monkeypatch):
    import voice.speech as speech

    monkeypatch.setattr(speech, "voice_speech_service",
                        FakeService("the deploy failed twice"))
    bot = FakeBot({"voice-1": FakeFile(OGG)})
    client = _client(bot)
    update = FakeUpdate(FakeMessage(voice=FakeVoice(), caption="context:"))

    with _no_model() as rec:
        await client.on_message(update)

    assert rec.await_args.args[0].content == (
        "context:\n\n[voice message transcript] the deploy failed twice")


@pytest.mark.asyncio
async def test_a_plain_text_message_never_touches_the_voice_path(inbox, monkeypatch):
    import voice.speech as speech

    service = FakeService("unreachable")
    monkeypatch.setattr(speech, "voice_speech_service", service)
    bot = FakeBot({})
    client = _client(bot)
    message = FakeMessage()
    message.text = "what's the plan"

    with _no_model() as rec:
        await client.on_message(FakeUpdate(message))

    assert rec.await_args.args[0].content == "what's the plan"
    assert bot.asked == []
    assert service.calls == []


# ── message.audio, through intake itself ─────────────────────────────────
def _build(message):
    from bots.telegram import intake

    return intake.build_turn(
        FakeUpdate(message),
        bot_username=BOT_USERNAME,
        bot_user_id=BOT_ID,
        owner_ref={"app_user_id": APP_OWNER_ID,
                   "telegram_user_id": OWNER_TELEGRAM_ID},
    )


def test_an_audio_file_he_sends_lands_in_the_voice_bucket():
    """``message.audio`` is the other half of the row — an mp3 he forwards
    is heard like a note he recorded. Nothing exercised this branch before
    (review round 1, important 2): deleting it left every test green."""
    from bots.common.voice_note import VOICE_PLACEHOLDER_CONTENT

    result = _build(FakeMessage(audio=FakeAudio()))

    assert result is not None
    turn = result.turn
    assert turn.metadata.is_voice is True
    assert turn.content == VOICE_PLACEHOLDER_CONTENT
    entry, = turn.metadata.extra["telegram_files"]
    assert entry["bucket"] == "voice"
    assert entry["mime"] == "audio/mpeg"
    assert entry["filename"] == "voicemail.mp3"
    assert entry["file_id"] == "song-1"
    assert entry["file_unique_id"] == "u-song-1"
    assert entry["size"] == 4096


def test_an_audio_file_with_no_mime_and_no_name_still_says_what_it_is():
    """Telegram may send neither. The defaults are the row's own."""
    result = _build(FakeMessage(
        audio=FakeAudio(mime_type=None, file_name=None)))

    entry, = result.turn.metadata.extra["telegram_files"]
    assert entry["bucket"] == "voice"
    assert entry["mime"] == "audio/mpeg"
    assert entry["filename"] == "audio"


def test_a_voice_note_and_an_audio_file_are_both_collected():
    result = _build(FakeMessage(voice=FakeVoice(), audio=FakeAudio()))

    buckets = [e["bucket"] for e in result.turn.metadata.extra["telegram_files"]]
    assert buckets == ["voice", "voice"]


# ── a stranger's voice note, heard (the owner, 10-12) ───────────────────
# Until 10-12 her guest lane spent no STT on a stranger's audio and the
# turn carried the honest marker. The owner, 10-12: guests' messages are
# "normal messages, minus permissions" — so a guest's voice note is heard
# like his, in the guest's own background task (a slow download never holds
# the door), BEFORE the room read, with their name tag around the words.

def _stranger_note(caption=None):
    return FakeUpdate(FakeMessage(voice=FakeVoice(), caption=caption,
                                  author_id=STRANGER_ID, username="stranger"))


async def _stranger_turn(client, update):
    with patch("bots.telegram.client.dispatch_turn",
               new=AsyncMock(return_value=_result())) as disp, \
         patch("bots.telegram.client.send_turn_reply", new=AsyncMock()), \
         patch("bots.telegram.client.record_user_fragment",
               new=AsyncMock()) as rec:
        await client.on_message(update)
        await _drain_guests(client)
    rec.assert_not_awaited()              # never his history
    return disp.await_args.args[0]


@pytest.mark.asyncio
async def test_a_strangers_voice_note_is_heard_inside_their_name_tag(
        inbox, monkeypatch):
    import voice.speech as speech
    from bots.common.voice_note import VOICE_PLACEHOLDER_CONTENT
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    service = FakeService("palli, can you hear me?")
    monkeypatch.setattr(speech, "voice_speech_service", service)
    bot = FakeBot({"voice-1": FakeFile(OGG)})

    turn = await _stranger_turn(_client(bot), _stranger_note())

    # Moved deliberately (Fable's checkpoint, 8; 10-13): the tag is the name
    # the group shows (full_name "Dana"), not the username "stranger".
    assert turn.content == "[Dana]: palli, can you hear me?"
    assert VOICE_PLACEHOLDER_CONTENT not in turn.content
    assert turn.metadata.is_voice is True
    assert turn.metadata.origin == "guest"
    assert [c["audio_bytes"] for c in service.calls] == [OGG]
    assert bot.asked == ["voice-1"]


@pytest.mark.asyncio
async def test_the_room_read_gets_a_strangers_words_not_the_token(inbox, monkeypatch):
    """The order that caused 10-12 17:20 on the other door: the room read
    must judge the guest's WORDS, so the note is heard before it."""
    import voice.speech as speech
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    monkeypatch.setattr(speech, "voice_speech_service", FakeService("palli, you there?"))
    client = _client(FakeBot({"voice-1": FakeFile(OGG)}))
    client._room_pass.guest_message = AsyncMock()

    await client.on_message(_stranger_note())
    await _drain_guests(client)

    read = client._room_pass.guest_message.await_args.args[0]
    assert read.content == "[Dana]: palli, you there?"


class _SlowService:
    """The facade's one method, counting how many hearings run at once."""

    def __init__(self):
        self.now = 0
        self.most = 0
        self.calls = 0

    async def transcribe_attachment_bytes(self, *, audio_bytes, filename="voice.ogg"):
        self.calls += 1
        self.now += 1
        self.most = max(self.most, self.now)
        await asyncio.sleep(0.02)
        self.now -= 1
        return "hi"


@pytest.mark.asyncio
async def test_strangers_voice_notes_are_heard_a_few_at_a_time(inbox, monkeypatch):
    import bots.common.voice_note as vn
    import voice.speech as speech
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    monkeypatch.setattr(vn, "GUEST_HEARINGS_AT_ONCE", 1)
    service = _SlowService()
    monkeypatch.setattr(speech, "voice_speech_service", service)
    client = _client(FakeBot({"voice-1": FakeFile(OGG)}))
    client._room_pass.guest_message = AsyncMock()

    for _ in range(3):
        await client.on_message(_stranger_note())
    await _drain_guests(client)

    assert service.calls == 3
    assert service.most == 1


@pytest.mark.asyncio
async def test_a_strangers_audio_is_not_kept_once_it_is_heard(inbox, monkeypatch):
    """"I don't hold files for guests" (final review, I4): the words are on
    the turn, nothing reads a guest's audio after, so the copy goes."""
    import voice.speech as speech
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    monkeypatch.setattr(speech, "voice_speech_service", FakeService("hi"))

    await _stranger_turn(_client(FakeBot({"voice-1": FakeFile(OGG)})), _stranger_note())

    assert [p for p in inbox.rglob("*") if p.is_file()] == []


@pytest.mark.asyncio
async def test_a_strangers_note_she_cannot_hear_is_the_honest_marker(inbox, monkeypatch):
    import voice.speech as speech
    from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    monkeypatch.setattr(speech, "voice_speech_service", FakeService(None))

    turn = await _stranger_turn(_client(FakeBot({"voice-1": FakeFile(OGG)})),
                                _stranger_note())

    assert turn.content == f"[Dana]: {TRANSCRIPTION_UNAVAILABLE_CONTENT}"


@pytest.mark.asyncio
async def test_a_strangers_caption_stays_and_the_transcript_follows_it(inbox, monkeypatch):
    import voice.speech as speech
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    monkeypatch.setattr(speech, "voice_speech_service", FakeService("it's about saturday"))

    turn = await _stranger_turn(_client(FakeBot({"voice-1": FakeFile(OGG)})),
                                _stranger_note(caption="listen to this"))

    assert turn.content == (
        "[Dana]: listen to this\n\n[voice message transcript] it's about saturday")


# ── the voice stack itself failing to load ───────────────────────────────
@pytest.mark.asyncio
async def test_a_voice_stack_that_will_not_import_is_the_marker(inbox, monkeypatch):
    """The lazy import is the first load of the voice stack on the event
    loop, on the first voice note of the process. If it raises, the message
    must still arrive — marked (review round 1, minor 4)."""
    import sys
    from types import ModuleType

    from bots.common.voice_note import TRANSCRIPTION_UNAVAILABLE_CONTENT
    from bots.telegram import intake

    monkeypatch.setitem(sys.modules, "voice.speech", ModuleType("voice.speech"))
    bot = FakeBot({"voice-1": FakeFile(OGG)})

    out = await intake.resolve_voice_turn(_voice_turn(), bot)

    assert out.content == TRANSCRIPTION_UNAVAILABLE_CONTENT
