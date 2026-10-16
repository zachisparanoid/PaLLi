"""Tests for bots.discord.voice_attachment — detect AND transcribe voice messages."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_extract_voice_attachment_returns_first_audio_attachment() -> None:
    from bots.discord.voice_attachment import extract_voice_attachment

    audio = MagicMock()
    audio.content_type = "audio/ogg"
    other = MagicMock()
    other.content_type = "image/png"
    msg = MagicMock()
    msg.attachments = [other, audio]
    found = extract_voice_attachment(msg)
    assert found is audio


def test_extract_voice_attachment_returns_none_when_absent() -> None:
    from bots.discord.voice_attachment import extract_voice_attachment

    other = MagicMock()
    other.content_type = "image/png"
    msg = MagicMock()
    msg.attachments = [other]
    assert extract_voice_attachment(msg) is None


def test_extract_voice_attachment_handles_missing_attachments_attr() -> None:
    """Defensive: a message-like object without an .attachments attr
    must not raise."""
    from bots.discord.voice_attachment import extract_voice_attachment

    msg = MagicMock(spec=[])  # no attachments attr at all
    assert extract_voice_attachment(msg) is None


def test_voice_placeholder_content_constant_is_loud() -> None:
    """Constant is referenced by downstream stages; if you change it,
    sweep callers too."""
    from bots.discord.voice_attachment import VOICE_PLACEHOLDER_CONTENT

    assert VOICE_PLACEHOLDER_CONTENT == "[VOICE_MESSAGE]"


def test_extract_voice_attachment_accepts_video_ogg() -> None:
    """Discord encodes some voice messages as video/ogg, not audio/ogg."""
    from bots.discord.voice_attachment import extract_voice_attachment

    voice = MagicMock()
    voice.content_type = "video/ogg"
    msg = MagicMock()
    msg.attachments = [voice]
    assert extract_voice_attachment(msg) is voice


# ---------------------------------------------------------------------------
# Transcription (2026-06-11). Until now this module only DETECTED voice
# messages — the turn went downstream with empty content, which is what
# poisoned the agent session and 400'd every subsequent turn. Now the audio
# is transcribed and the transcript becomes the turn content.
# ---------------------------------------------------------------------------


def _voice_message(*, content: str = "", audio: bytes = b"OggS-fake-bytes",
                   content_type: str = "audio/ogg"):
    att = MagicMock()
    att.content_type = content_type
    att.read = AsyncMock(return_value=audio)
    msg = MagicMock()
    msg.content = content
    msg.attachments = [att]
    return msg, att


def _fake_stt(transcript):
    stt = MagicMock()
    stt.transcribe_audio = AsyncMock(return_value=transcript)
    return stt


@pytest.mark.asyncio
async def test_transcribe_voice_message_returns_transcript() -> None:
    from bots.discord.voice_attachment import transcribe_voice_message

    msg, att = _voice_message()
    stt = _fake_stt("hey palli, you there?")
    out = await transcribe_voice_message(msg, stt=stt)
    assert out == "hey palli, you there?"
    stt.transcribe_audio.assert_awaited_once()
    args, kwargs = stt.transcribe_audio.await_args
    sent_audio = args[0] if args else kwargs.get("audio_data")
    assert sent_audio == b"OggS-fake-bytes"
    assert kwargs.get("content_type") == "audio/ogg"


@pytest.mark.asyncio
async def test_transcribe_voice_message_maps_video_ogg_to_audio_ogg() -> None:
    """Deepgram's containerized-format detection doesn't know video/ogg;
    Discord's ogg voice notes must be presented as audio/ogg."""
    from bots.discord.voice_attachment import transcribe_voice_message

    msg, att = _voice_message(content_type="video/ogg")
    stt = _fake_stt("yo")
    out = await transcribe_voice_message(msg, stt=stt)
    assert out == "yo"
    _, kwargs = stt.transcribe_audio.await_args
    assert kwargs.get("content_type") == "audio/ogg"


@pytest.mark.asyncio
async def test_transcribe_voice_message_none_when_no_audio_attachment() -> None:
    from bots.discord.voice_attachment import transcribe_voice_message

    msg = MagicMock()
    msg.attachments = []
    stt = _fake_stt("should never be called")
    assert await transcribe_voice_message(msg, stt=stt) is None
    stt.transcribe_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcribe_voice_message_none_on_download_failure() -> None:
    from bots.discord.voice_attachment import transcribe_voice_message

    msg, att = _voice_message()
    att.read = AsyncMock(side_effect=RuntimeError("discord cdn says no"))
    stt = _fake_stt("unreachable")
    assert await transcribe_voice_message(msg, stt=stt) is None
    stt.transcribe_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcribe_voice_message_none_when_stt_empty() -> None:
    from bots.discord.voice_attachment import transcribe_voice_message

    msg, _ = _voice_message()
    assert await transcribe_voice_message(msg, stt=_fake_stt(None)) is None
    msg2, _ = _voice_message()
    assert await transcribe_voice_message(msg2, stt=_fake_stt("   ")) is None


@pytest.mark.asyncio
async def test_resolve_voice_turn_content_success_replaces_placeholder() -> None:
    from bots.discord.voice_attachment import (
        VOICE_PLACEHOLDER_CONTENT,
        resolve_voice_turn_content,
    )

    msg, _ = _voice_message()
    out = await resolve_voice_turn_content(
        msg, current_content=VOICE_PLACEHOLDER_CONTENT, stt=_fake_stt("can you check the server"),
    )
    assert out == "can you check the server"


@pytest.mark.asyncio
async def test_resolve_voice_turn_content_failure_yields_honest_marker() -> None:
    """When transcription is unavailable the turn must still carry HONEST,
    non-empty content — an empty user turn is exactly what poisoned the
    session history on 2026-06-11. The marker reads as an event so PaLLi
    can tell the user it couldn't make the audio out."""
    from bots.discord.voice_attachment import (
        TRANSCRIPTION_UNAVAILABLE_CONTENT,
        VOICE_PLACEHOLDER_CONTENT,
        resolve_voice_turn_content,
    )

    msg, _ = _voice_message()
    out = await resolve_voice_turn_content(
        msg, current_content=VOICE_PLACEHOLDER_CONTENT, stt=_fake_stt(None),
    )
    assert out == TRANSCRIPTION_UNAVAILABLE_CONTENT
    assert out.strip()  # never empty


@pytest.mark.asyncio
async def test_resolve_voice_turn_content_appends_transcript_to_caption() -> None:
    """A typed caption alongside the voice note is kept; the transcript is
    appended rather than clobbering the user's words."""
    from bots.discord.voice_attachment import resolve_voice_turn_content

    msg, _ = _voice_message()
    out = await resolve_voice_turn_content(
        msg, current_content="context for the audio:", stt=_fake_stt("the deploy failed twice"),
    )
    assert out.startswith("context for the audio:")
    assert "the deploy failed twice" in out


@pytest.mark.asyncio
async def test_resolve_voice_turn_content_failure_keeps_caption() -> None:
    from bots.discord.voice_attachment import resolve_voice_turn_content

    msg, _ = _voice_message()
    out = await resolve_voice_turn_content(
        msg, current_content="typed caption", stt=_fake_stt(None),
    )
    assert out == "typed caption"


def test_intake_voice_only_message_gets_placeholder_content() -> None:
    """A voice-only message must NEVER produce an empty-content turn — that
    is the exact record that 400'd the whole channel. Intake synthesizes the
    voice placeholder just like it already does for images and files."""
    from bots.discord.intake import build_turn_from_message
    from bots.discord.voice_attachment import VOICE_PLACEHOLDER_CONTENT

    att = MagicMock()
    att.content_type = "audio/ogg"
    att.filename = "voice-message.ogg"
    msg = MagicMock()
    msg.content = ""
    msg.attachments = [att]
    msg.mentions = []
    turn = build_turn_from_message(msg, bot_user_id=99, owner_id=1)
    assert turn.metadata.is_voice is True
    assert turn.content == VOICE_PLACEHOLDER_CONTENT
