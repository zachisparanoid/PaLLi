"""Her words as a voice note: speech facade → OGG/Opus bytes (any door).

Moved here from ``bots/common/voice_note.py`` (Batch 8, task 10 — ruling
T10-1): her ``voice_note`` tool lives in ``core/`` and core never imports
``bots``, while core → voice is allowed. ``bots/common/voice_note.py``
re-exports every name, so the Telegram door and its tests are unchanged.

She speaks through the facade's CONFIGURED voice (``LEMONFOX_TTS_VOICE`` for
Lemonfox): no voice is named here (ruling T10-8).
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# The shape a voice note has at every door: Opus in an Ogg container, one
# channel, a speech bitrate. Telegram will not play a WAV as a voice note
# (it becomes a file attachment), and the TTS facade only makes WAVs — so
# one bounded ffmpeg run stands between her voice and the chat.
VOICE_NOTE_BITRATE = "32k"
# Short on purpose: a reply is seconds of audio, and a conversion that has
# not finished in a minute is a stuck door, not a slow one.
VOICE_NOTE_FFMPEG_TIMEOUT_SECONDS = 60.0


async def synthesize_voice_note(text: str, *, service=None,
                                ffmpeg=None) -> bytes | None:
    """Her words as the OGG/Opus bytes a voice note carries, or None.

    The mirror of ``transcribe_note`` and, like it, platform-blind: no door
    is named here, because "say this out loud" is the same job at all of
    them. ``service`` is the speech facade (default: the shared singleton,
    imported lazily so a chat client that never speaks never loads the voice
    stack) and ``ffmpeg`` the bounded runner; both are injectable for tests.

    None means "no voice note" for every reason there is — nothing to say,
    the TTS lane off, no provider, a raising service, ffmpeg failing, an
    empty file. It is never an error the caller has to handle: the CALLER
    sends its text and says nothing about the voice, because a reply that
    arrives is not a failure and an apology for a missing audio file is
    noise.
    """
    spoken = str(text or "").strip()
    if not spoken:
        return None

    try:
        engine = service
        if engine is None:
            # INSIDE the try, as in transcribe_note: this is the first import
            # of the voice stack on the event loop, and an ImportError from
            # it must cost the voice note, never the reply.
            from voice.speech import voice_speech_service
            engine = voice_speech_service
        convert = ffmpeg
        if convert is None:
            from core.ffmpeg import run_ffmpeg_checked
            convert = run_ffmpeg_checked

        audio = await engine.synthesize_speech(
            text=spoken, encoding="linear16", sample_rate=48000, container="wav",
        )
        if not audio:
            # The TTS lane is off, or the provider had nothing to say. Not an
            # error, and not worth an ffmpeg run.
            logger.info("voice_note: no speech for the reply; text only")
            return None

        # A directory of their own, removed by construction on EVERY path —
        # including a write that raises half-way (review round 1, minor 1)
        # and a conversion killed mid-run. Both files are named inside it
        # rather than as siblings in the shared temp dir, where another
        # process could have been holding the name already (minor 2).
        with tempfile.TemporaryDirectory(prefix="palli-voice-") as workdir:
            wav_path = os.path.join(workdir, "reply.wav")
            ogg_path = os.path.join(workdir, "reply.ogg")
            Path(wav_path).write_bytes(audio)
            await convert(
                [
                    "ffmpeg", "-i", wav_path,
                    "-c:a", "libopus", "-b:a", VOICE_NOTE_BITRATE, "-vbr", "on",
                    "-ac", "1",
                    ogg_path, "-y", "-loglevel", "error",
                ],
                timeout_seconds=VOICE_NOTE_FFMPEG_TIMEOUT_SECONDS,
            )
            data = Path(ogg_path).read_bytes()
            return data or None
    except Exception as exc:  # noqa: BLE001 — a door must not die of this
        logger.warning("voice_note: synthesis failed: %s", exc)
        return None
