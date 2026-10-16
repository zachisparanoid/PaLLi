"""Voice messages on the Discord door (Batch 8, task 10 — B8-5; T10-3..T10-9).

``deliver_if_voice`` is the Discord half of ``bots/common/voice_note.py``'s
``plan_voice_reply``: when a reply goes out as voice — she made voice notes,
or voice replies are on in this conversation (owner O2, INTERIM) — this
sends it and says how many of its parts arrived, so the client can mark her
record exactly as it does for failed text (fix plan 5.10). None means the
reply is not voice and the client's text path runs, unchanged.

How a voice message goes: discord.py 2.7 can't send the waveform kind of
voice message, but an ``.ogg`` attachment shows as an audio player. The send
path in ``bots/discord/reply.py`` takes paths (and is CRLF, left alone —
ruling T10-9), so each note is written to a temporary directory around its
own send and is gone after. It is NOT routed through ``send_turn_reply``:
when a send carrying files fails, that function retries the words alone with
a note naming where the file "still is" — for a voice message that is a
temporary path already gone, and the retry would count the voice as
delivered. Here a voice message that did not go is a part that did not
arrive, felt as ``voice_note_dropped`` (the Telegram door's kind).
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from bots.common.delivery_notice import delivery_notice as _delivery_notice
from bots.common.voice_note import plan_voice_reply
from bots.discord.reply import (
    _send_failure_reason,
    last_send_error,
    last_send_parts,
    send_turn_reply,
    send_turn_reply_cadenced,
)

logger = logging.getLogger(__name__)

VOICE_FILENAME = "voice-message.ogg"


async def _send_one(channel, workdir: str, index: int, data: bytes) -> str:
    """One voice message: written into ``workdir``, sent, its handle closed.
    Returns "" when it arrived, else what killed it. Never raises for a
    failed write or send; a cancel goes through."""
    import discord

    attachment = None
    try:
        path = os.path.join(workdir, f"{index + 1}-{VOICE_FILENAME}")
        with open(path, "wb") as handle:
            handle.write(data)
        attachment = discord.File(path, filename=VOICE_FILENAME)
        await channel.send(files=[attachment])
        return ""
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — felt and counted, never raised
        logger.warning("discord reply: voice message send failed: %s", exc)
        _delivery_notice("voice_note_dropped", reason="voice_send_failed")
        return _send_failure_reason(exc)
    finally:
        if attachment is not None:
            attachment.close()


async def send_voice_notes(channel, notes: tuple[bytes, ...]) -> tuple[int, str]:
    """Send each voice message as an ``.ogg`` attachment of its own.

    Returns (how many arrived, what killed the first one that didn't — ""
    when all did). Never raises for a failed write or send — not even when
    the temporary directory itself can't be made; a cancel goes through."""
    landed, reason = 0, ""
    if not notes:
        return landed, reason
    try:
        with tempfile.TemporaryDirectory(prefix="palli-voice-out-") as workdir:
            for index, data in enumerate(notes):
                why = await _send_one(channel, workdir, index, data)
                if why:
                    reason = reason or why
                else:
                    landed += 1
    except OSError as exc:  # the temporary directory itself (or its cleanup)
        logger.warning("discord reply: no room for a voice message: %s", exc)
        if landed < len(notes):     # a failed cleanup after every send is no drop
            _delivery_notice("voice_note_dropped", reason="voice_send_failed")
            reason = reason or type(exc).__name__
    return landed, reason


async def deliver_if_voice(channel, channel_id: int, result, *,
                           owner_id: int = 0,
                           waiting_since: float | None = None) -> tuple[int, int, str] | None:
    """Send this reply as voice when it is one; None when it is not.

    Her text line first (when there is one, with any files), exactly as words
    go today, then the voice messages. Returns (landed, total, reason) over
    every part — a voice message is a delivery part like a message of words
    (ruling T10-3). ``waiting_since`` credits the text line's typing pause
    with the wait behind it, as on the text path (2026-10-12)."""
    # Lazy: the channel's typing() is touched only when speech is made, so a
    # reply that is not voice touches nothing the text path did not.
    voice = await plan_voice_reply("discord", channel_id, result, owner_id=owner_id,
                                   typing=lambda: channel.typing())
    if voice is None:
        return None
    attachments = tuple(getattr(result, "attachments", ()) or ())
    landed, total, reason = 0, 0, ""
    cadenced = getattr(result, "cadenced_reply", None)
    if (voice.text.strip() and voice.text == getattr(result, "response_text", "")
            and cadenced is not None and len(cadenced.beats) > 1):
        await send_turn_reply_cadenced(channel, cadenced, waiting_since=waiting_since)
        (landed, total), reason = last_send_parts(), last_send_error()
    elif voice.text.strip() or attachments:
        await send_turn_reply(channel, voice.text, attachments=attachments,
                              waiting_since=waiting_since)
        (landed, total), reason = last_send_parts(), last_send_error()
    voice_landed, voice_reason = await send_voice_notes(channel, voice.notes)
    return (landed + voice_landed, total + len(voice.notes), reason or voice_reason)
