"""A voice note, from any door: bytes in, a transcript or an honest marker out.

History (2026-06-11): a voice-only message produced a turn with EMPTY
content, which was persisted into the channel's agent session and then
400'd every subsequent call ("messages[479]: content should be a string or
a list"). The turn content must always end up non-empty: the transcript
when STT works, an honest event marker when it doesn't.

Extracted from bots/discord/voice_attachment.py (telegram door,
2026-09-14, task 1). The Discord half — find the audio attachment, download
it — stays there, because an attachment object IS Discord's. What is left
here sees only bytes and a mime type, which is what every door has.

Two engines, one resolution (task 4). ``transcribe_bytes`` is Discord's
path unchanged: bytes straight to the Deepgram prerecorded API.
``transcribe_note`` is the provider-agnostic one Telegram uses: the speech
facade transcodes with the bounded ffmpeg runner and calls whichever STT
boot configured, so a faster-whisper install hears Telegram too. What they
BOTH feed is ``resolve_voice_content`` — the single answer to "what does
she see when she cannot hear him", which is never nothing.

And the way back out (task 5): ``synthesize_voice_note`` speaks a reply
through the same facade's TTS and converts it to the OGG/Opus a voice note
has to be. Optional, per chat, decided by the door. Since Batch 8, task 10
it lives in ``voice/voice_note_out.py`` (re-exported here), and
``plan_voice_reply`` is the one answer both doors share to "does this reply
go out as voice, and with what?".
"""
from __future__ import annotations

import logging
import mimetypes
import weakref
from dataclasses import dataclass
from pathlib import Path

# The way back out moved to voice/voice_note_out.py (Batch 8, task 10 —
# ruling T10-1): her voice_note tool lives in core/, and core never imports
# bots. Every name is re-exported here, so the doors and their tests are
# unchanged.
from voice.voice_note_out import (  # noqa: F401 — re-exported
    VOICE_NOTE_BITRATE,
    VOICE_NOTE_FFMPEG_TIMEOUT_SECONDS,
    synthesize_voice_note,
)

logger = logging.getLogger(__name__)

VOICE_PLACEHOLDER_CONTENT = "[VOICE_MESSAGE]"

# What the LLM sees when a voice note arrives but no transcript could be
# produced (no STT key, download failed, silent audio). Reads as an event
# description so PaLLi can honestly tell the user she couldn't make the
# audio out — and the turn is never empty, which is what poisoned the
# session history in the 2026-06-11 outage.
TRANSCRIPTION_UNAVAILABLE_CONTENT = (
    "[voice message received — transcription unavailable]"
)

# The same shape for a different truth (task 4, review round 1, important 3):
# audio over the door's download wall was never heard because it was never
# FETCHED, and "transcription unavailable" would have her guess at a reason.
# She can say which one it was, so she should.
TRANSCRIPTION_TOO_LARGE_CONTENT = (
    "[voice message received — too large to transcribe]"
)

# A guest's audio, heard since 10-12 (the owner: guests' messages are "normal
# messages, minus permissions"), meets the same 20 MB wall on both doors. On
# Telegram it is the door's own cap on every download, his too
# (``bots/telegram/intake.TELEGRAM_VOICE_MAX_BYTES`` — the bot API's 20 MB,
# or the inbox's cap if that is smaller); on Discord, where his own audio has
# no wall, this one stands for it on a guest's. A resource floor, like the
# guest queue — never a messaging rule.
GUEST_VOICE_MAX_BYTES = 20 * 1024 * 1024

# And how many guests' voice notes are heard at once, across both doors
# (Fable's review, 10-12): hearing comes before the room read, so the guest
# queue's slot is not yet taken, and this is its floor for the paid STT and
# the downloads. A note over it waits its turn; it is never dropped. Read at
# call time, so a test can set it. One queue per event loop: both doors run
# as tasks on the launcher's one loop (core/launcher_runtime.py), so live it
# is one queue; a test's fresh loop gets a fresh one, so a hearing a finished
# test left holding a place can never block the next.
GUEST_HEARINGS_AT_ONCE = 2
_guest_hearings: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def guest_hearing_slot():
    """``async with guest_hearing_slot():`` — one of the
    ``GUEST_HEARINGS_AT_ONCE`` places a guest's voice note is heard in, on
    either door; waits in line for one, gives it back after."""
    import asyncio

    from bots.common.guest_gate import GuestGate

    loop = asyncio.get_running_loop()
    gate = _guest_hearings.get(loop)
    if gate is None:
        gate = _guest_hearings[loop] = GuestGate(
            cap=lambda: max(1, int(GUEST_HEARINGS_AT_ONCE)))
    return gate.slot()


async def transcribe_bytes(data: bytes, *, mime: str, stt=None) -> str | None:
    """Return the transcript of ``data``, or None.

    None when there is no audio, STT is unavailable, or it returns nothing —
    callers decide how to degrade. ``stt`` is injectable for tests and for a
    door that wants another engine; production defaults to the shared
    Deepgram client (which itself returns None when no API key is set).
    """
    if not data:
        return None

    engine = stt
    if engine is None:
        # Lazy: keep the voice stack out of module import (this module loads
        # with a chat client even when voice features are unused).
        from voice.deepgram_client import deepgram_client
        engine = deepgram_client

    # Discord serves some ogg voice notes as video/ogg; Deepgram's
    # containerized-format detection only knows audio/ogg.
    content_type = (str(mime or "") or "audio/ogg").split(";")[0].strip().lower()
    if not content_type:
        content_type = "audio/ogg"
    if content_type == "video/ogg":
        content_type = "audio/ogg"

    try:
        transcript = await engine.transcribe_audio(data, content_type=content_type)
    except Exception as exc:
        logger.warning("voice_note: transcription failed: %s", exc)
        return None

    transcript = (transcript or "").strip()
    return transcript or None


# What a container's mime is CALLED, and what it is named on disk. The
# speech facade decides how to handle a file from its suffix (a .wav skips
# ffmpeg entirely), and Telegram hands us a mime with, often, a filename
# that has no extension at all ("audio"). Deterministic on purpose — a
# format table is not a meaning decision (owner rule 07-16).
_AUDIO_SUFFIXES: dict[str, str] = {
    "audio/ogg": ".ogg",
    "video/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/amr": ".amr",
}


def audio_filename(filename: str | None, mime: str | None) -> str:
    """A filename the speech facade can read the container off.

    Telegram's voice notes arrive with no name at all and its audio files
    with names like "audio" — and a suffix-less temp file is a file ffmpeg
    has to guess at. The mime is the thing that is always there, so it
    names the file when the name cannot.
    """
    name = str(filename or "").strip() or "voice"
    if Path(name).suffix:
        return name
    content_type = (str(mime or "") or "").split(";")[0].strip().lower()
    suffix = _AUDIO_SUFFIXES.get(content_type)
    if suffix is None and content_type:
        suffix = mimetypes.guess_extension(content_type)
    return name + (suffix or ".ogg")


async def transcribe_note(data: bytes, *, mime: str, filename: str = "voice.ogg",
                          service=None) -> str | None:
    """Return the transcript of ``data`` through the CONFIGURED speech stack.

    The provider-agnostic path: ``VoiceSpeechService.transcribe_attachment_bytes``
    writes the bytes to a temp file, transcodes to a 16 kHz mono WAV with the
    bounded ffmpeg runner, and hands the result to whichever STT provider boot
    selected (Deepgram, faster-whisper, …). It returns None when the STT lane
    is off, which is one of the honest-marker cases and not an error.

    ``service`` is injectable for tests and for a door that wants another
    engine; production defaults to the shared singleton, imported lazily so a
    chat client that never hears a voice note never loads the voice stack.

    None means "no transcript" for every reason there is — no audio, no
    provider, a raising service, silence. The caller decides how to degrade,
    and on both doors that decision is ``resolve_voice_content``.
    """
    if not data:
        return None

    try:
        engine = service
        if engine is None:
            # INSIDE the try on purpose (review round 1, minor 4): this is
            # the first import of the voice stack on the event loop — it
            # happens on the first voice note of the process, not at boot —
            # and an ImportError from it would otherwise lose the whole
            # message instead of marking it.
            from voice.speech import voice_speech_service
            engine = voice_speech_service

        transcript = await engine.transcribe_attachment_bytes(
            audio_bytes=data, filename=audio_filename(filename, mime),
        )
    except Exception as exc:  # noqa: BLE001 — a door must not die of this
        logger.warning("voice_note: transcription failed: %s", exc)
        return None

    transcript = (transcript or "").strip()
    return transcript or None


@dataclass(frozen=True)
class VoiceReply:
    """What a door sends when a reply goes out as voice (Batch 8, task 10).

    ``text`` is the text line that goes beside the voice ("" for none) and
    ``notes`` the OGG/Opus voice messages, in order."""

    text: str
    notes: tuple[bytes, ...]


async def plan_voice_reply(platform: str, channel_id: int, result, *, owner_id: int = 0,
                           typing=None) -> VoiceReply | None:
    """Does this reply go out as voice, and with what? None means today's
    text path, unchanged (Batch 8, task 10 — B8-5; rulings T10-3..T10-6).

    One answer for both doors, in this order:

    * a quiet choice speaks nothing (T10-5); a voice note she made beside it
      still goes, with no text — a note is a delivery, like a file (final
      fix wave, ruling FW-3);
    * she made voice notes this turn (``voice_note``): they go, and her text
      reply goes beside them as the text line — "" when she wrote none;
    * with ``voice.voice_notes`` off, nothing below is read: today's door;
    * voice replies are on for this conversation (the switch she sets, and
      Telegram's ``/voice`` flips): the door speaks her whole reply, and what
      she'd need them to read — links, code, numbers in backticks — goes as
      the short text line beside it (DEFECT §56, ``split_for_voice``; owner
      O2 — INTERIM, the owner revisits it after the batches). A reply that is
      only a link goes as text. If the speech can't be made, None — the text
      goes alone, as today.

    A reply carries at most ``MAX_VOICE_NOTES_PER_REPLY`` voice messages,
    whatever the run queued (DEFECT §56: 51 from one run on 10-01).

    ``typing`` is the door's "typing…" (an async context manager factory),
    shown while the speech is made. Both lanes call this: since 10-12 a
    guest's reply reads the switch too (the switch itself stays the owner's
    to flip — guests have no tools and no ``/voice``).
    """
    from core import voice_replies

    notes = tuple(getattr(result, "voice_notes", ()) or ())
    if len(notes) > voice_replies.MAX_VOICE_NOTES_PER_REPLY:
        # DEFECT §56 (10-01 20:03: 51 voice messages from one run): a reply
        # carries one; the first is the one she meant, the rest a loop's.
        logger.warning("voice_note: %d voice messages queued in one reply; sending %d",
                       len(notes), voice_replies.MAX_VOICE_NOTES_PER_REPLY)
        notes = notes[:voice_replies.MAX_VOICE_NOTES_PER_REPLY]
    if bool(getattr(result, "quiet", False)):
        return VoiceReply(text="", notes=notes) if notes else None
    text = str(getattr(result, "response_text", "") or "")
    if notes:
        return VoiceReply(text=text, notes=notes)
    if not voice_replies.voice_notes_enabled():
        return None
    key = voice_replies.key_for(f"{platform}_text", channel_id, owner_id)
    if not voice_replies.is_on(key):
        return None
    # DEFECT §56: she just writes her reply; what they'd need to read (links,
    # code, numbers in backticks) goes as the short text line beside the voice.
    spoken, text_line = voice_replies.split_for_voice(text)
    if not voice_replies.has_words_to_speak(spoken):
        return None
    if typing is not None:
        async with typing():
            audio = await synthesize_voice_note(spoken)
    else:
        audio = await synthesize_voice_note(spoken)
    if not audio:
        logger.info("voice_note: voice replies are on in %s, but no speech; text only", key)
        return None
    return VoiceReply(text=text_line, notes=(audio,))


def resolve_voice_content(transcript: str | None, current_content: str, *,
                          marker: str = TRANSCRIPTION_UNAVAILABLE_CONTENT) -> str:
    """The final turn content for a message that carried a voice note.

    ONE resolution for every door (telegram door, 2026-09-14, task 4) — it
    was Discord's, and a second copy of it would be a second answer to
    "what does she see when she cannot hear him".

    Success: the transcript replaces the intake placeholder, or is appended
    after a typed caption (his own words are never clobbered).
    Failure: the honest marker — NEVER empty content. ``marker`` names WHICH
    honest line this failure deserves; it defaults to the unavailable one, so
    Discord's two-argument calls are unchanged.
    """
    text = (transcript or "").strip()
    base = (current_content or "").strip()
    is_placeholder_only = not base or base == VOICE_PLACEHOLDER_CONTENT

    if text:
        logger.info("voice_note: transcribed voice message (%d chars)", len(text))
        if is_placeholder_only:
            return text
        return f"{base}\n\n[voice message transcript] {text}"

    logger.warning(
        "voice_note: no transcript for voice message "
        "(STT unavailable, download failed, or empty audio)"
    )
    if is_placeholder_only:
        return marker
    return base
