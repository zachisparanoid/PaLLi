"""Discord voice messages: detect the audio attachment and transcribe it.

History: this began as a Wave-3 detect-only stub, and the gap had real
consequences — a voice-only message produced a turn with EMPTY content,
which was persisted into the channel's agent session and then 400'd every
subsequent DeepSeek call (2026-06-11 outage: "messages[479]: content should
be a string or a list"). The turn content must always end up non-empty:
the transcript when STT works, an honest event marker when it doesn't.

Flow: intake.py marks the turn (metadata.is_voice + placeholder content);
bots.discord.client awaits resolve_voice_turn_content() before dispatch,
which downloads the attachment and runs it through the existing Deepgram
prerecorded STT (voice/deepgram_client.py — bots/ -> voice/ is an allowed
import direction per tests/architecture/test_import_boundaries.py).
"""
from __future__ import annotations

import asyncio
import logging

# The transcriber and the two markers moved to bots/common/voice_note.py
# (telegram door, 2026-09-14, task 1) — a voice note is not a Discord idea.
# Imported rather than redefined so both doors carry the SAME marker
# objects: bots/discord/intake.py imports VOICE_PLACEHOLDER_CONTENT from
# this module and the turn content it synthesizes is compared against it.
from bots.common.voice_note import (  # noqa: F401 — re-exported on purpose
    GUEST_VOICE_MAX_BYTES,
    TRANSCRIPTION_TOO_LARGE_CONTENT,
    TRANSCRIPTION_UNAVAILABLE_CONTENT,
    VOICE_PLACEHOLDER_CONTENT,
    resolve_voice_content,
    transcribe_bytes,
)

logger = logging.getLogger(__name__)

_AUDIO_PREFIXES = ("audio/", "video/ogg")


def extract_voice_attachment(message):
    """Return the first audio attachment on the message, or None.

    Tolerates message-like objects without an `.attachments` attr
    (returns None) so callers don't have to guard.
    """
    attachments = getattr(message, "attachments", None) or []
    for att in attachments:
        ct = (getattr(att, "content_type", "") or "").lower()
        if any(ct.startswith(p) for p in _AUDIO_PREFIXES):
            return att
    return None


async def transcribe_voice_message(message, *, stt=None) -> str | None:
    """Download the message's audio attachment and return its transcript.

    Returns None when there is no audio attachment, the download fails, or
    STT is unavailable/returns nothing — callers decide how to degrade.
    ``stt`` is injectable for tests; production defaults to the shared
    Deepgram client (which itself returns None when no API key is set).
    """
    att = extract_voice_attachment(message)
    if att is None:
        return None

    try:
        audio = await att.read()
    except Exception as exc:
        logger.warning("voice_attachment: audio download failed: %s", exc)
        return None
    if not audio:
        return None

    return await transcribe_bytes(
        audio, mime=getattr(att, "content_type", "") or "audio/ogg", stt=stt,
    )


async def resolve_voice_turn_content(message, *, current_content: str, stt=None,
                                     max_bytes: int | None = None) -> str:
    """Produce the final turn content for a message carrying a voice note.

    Success: the transcript replaces the intake placeholder, or is appended
    after a typed caption (the user's own words are never clobbered).
    Failure: the honest unavailable-marker — NEVER empty content.

    ``max_bytes``: a wall on the audio's size, checked against the size
    Discord reports BEFORE anything is read — the guest lane passes one
    (10-12); his own lane passes none. Audio over it is the too-large marker.

    The resolution itself moved to bots/common/voice_note.py (telegram door,
    2026-09-14, task 4) so both doors degrade the same way; the strings this
    returns are unchanged, and tests/bots/test_telegram_voice_in.py pins all
    three of them against their literal values.
    """
    if max_bytes is not None:
        att = extract_voice_attachment(message)
        size = getattr(att, "size", None)
        if isinstance(size, int) and size > max_bytes:
            logger.warning(
                "voice_attachment: %s not transcribed — %d bytes is over the "
                "%.0f MB wall", getattr(att, "filename", None) or "audio",
                size, max_bytes / (1024 * 1024))
            return resolve_voice_content(None, current_content,
                                         marker=TRANSCRIPTION_TOO_LARGE_CONTENT)
    transcript = await transcribe_voice_message(message, stt=stt)
    return resolve_voice_content(transcript, current_content)


async def hear_guest_voice(message, *, current_content: str, stt=None) -> str:
    """A guest's voice note HEARD (10-12 — the owner: guests' messages are
    "normal messages, minus permissions"): the owner's resolver, behind the
    guest wall (``GUEST_VOICE_MAX_BYTES``), in one of the guests' hearing
    places (``guest_hearing_slot`` — a few at once across both doors).

    Never raises: anything inside that breaks costs the audio, never the
    message — the honest marker, as the Telegram door does."""
    from bots.common.voice_note import guest_hearing_slot

    try:
        async with guest_hearing_slot():
            return await resolve_voice_turn_content(
                message, current_content=current_content, stt=stt,
                max_bytes=GUEST_VOICE_MAX_BYTES,
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a broken hearing costs the audio, never the message
        logger.exception("voice_attachment: a guest's voice note could not be heard")
        return resolve_voice_content(None, current_content)
