"""Wave 3: render a TurnResult back through a Discord channel.

Replaces the chunking / typing / send work that bots/discord/bot.py
does inline. Take text + a discord.Channel, emit at most 2000
characters per send (Discord's hard limit), wrap each send in a
typing indicator with a length-proportional human-pace delay.
"""
from __future__ import annotations

import asyncio
import os
import logging
import random
import time
from contextvars import ContextVar
from typing import Iterable

# Her sense of the last mile (§15): structure only, never raises. Moved to
# bots/common/delivery_notice.py for the telegram door (2026-09-14, task 2)
# — every door has a last mile. Bound under its old private name, so it is
# still the module attribute this file calls and tests monkeypatch.
from bots.common.delivery_notice import delivery_notice as _delivery_notice

logger = logging.getLogger(__name__)

# Fix plan 5.10 (Batch 4 task 5): what killed the last send, and how many of
# its parts landed, so the door can say so in her record ("[not delivered to
# them: HTTPException 50013]", "[only partly delivered to them: … after 1 of
# 3 parts]"). This is the Telegram door's shape (bots/telegram/reply.py). They
# are ContextVars and not module globals: two channels sending at once are two
# tasks, and each task gets its own copy.
_last_send_error: ContextVar[str] = ContextVar(
    "palli_discord_last_send_error", default="")
_last_send_parts: ContextVar[tuple[int, int]] = ContextVar(
    "palli_discord_last_send_parts", default=(0, 0))
# Seconds of typing shown before the parts of the last send (2026-10-12):
# the door logs it with the wait the person had.
_last_send_typing: ContextVar[float] = ContextVar(
    "palli_discord_last_send_typing", default=0.0)


def last_send_error() -> str:
    """What killed the most recent send in THIS task, or "" if nothing did."""
    return _last_send_error.get()


def last_send_parts() -> tuple[int, int]:
    """(landed, total) messages of the most recent send in THIS task. A part
    is one outgoing message of her words; the files ride on the first."""
    return _last_send_parts.get()


def last_send_typing() -> float:
    """Seconds of typing shown before the most recent send in THIS task."""
    return _last_send_typing.get()


def _send_failure_reason(exc: BaseException) -> str:
    """Short and secret-free: the exception's class, plus Discord's own error
    code (else the HTTP status) when it carries one. Never the message body,
    because that can quote what was being sent."""
    name = type(exc).__name__
    for attr in ("code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool) and value:
            return f"{name} {value}"
    return name


DISCORD_MAX_LEN = 2000
# DEFECT_RECORD §15 (2026-09-01): the bounced package. A 15.4 MB MP3 hit
# Discord's cap five times (413 Payload Too Large); the 6.6 MB file taped to
# it died too; and no failure ever reached her. Weigh before sending; name a
# dropped file to him; make every send failure something she feels.
# 10 MiB = the unboosted bot limit; override for a boosted server.
DISCORD_MAX_ATTACHMENT_BYTES = int(
    os.environ.get("PALLI_DISCORD_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024))
)


# Typing-delay tuning (ported from the legacy bots/discord/bot.py
# `_calculate_typing_delay`). Mirrors a natural 60-WPM read-and-type
# pace: ~0.15s per character, ±20% jitter, clamped to [1, 8] seconds.
_DELAY_PER_CHAR_SECONDS = 0.15
_DELAY_JITTER_LOW = 0.8
_DELAY_JITTER_HIGH = 1.2
_DELAY_MIN_SECONDS = 1.0
_DELAY_MAX_SECONDS = 8.0

# Credit for the wait already behind a reply (2026-10-12, the owner: "keep a
# 1 second beat if needed"). The pace above was meant for a reply that starts
# when the person's message arrives. A reply reaches this sender after the
# settle, the pre-reads and the writing, and the full pause on top made them
# wait twice (10-10 21:42: 8.2 s of typing after 18.6 s of waiting). The time
# already spent since their last message comes off the pause, down to a beat.
_BEAT_SECONDS = 1.0


def _chunk_text(text: str, *, max_len: int = DISCORD_MAX_LEN) -> Iterable[str]:
    """Yield contiguous chunks at most max_len chars long.

    No paragraph-aware splitting in v1; Discord's 2000-char hard limit
    is the only constraint that matters.
    """
    if not text:
        return
    for i in range(0, len(text), max_len):
        yield text[i : i + max_len]


def _calculate_typing_delay(text: str) -> float:
    """Return a length-proportional typing delay (seconds) with jitter.

    Approximates 60 WPM at 5 chars/word = 12 chars/sec; the 0.15s/char
    floor is slightly slower than that on purpose so short replies still
    show a visible typing indicator. Bounded to keep long replies from
    feeling unresponsive.
    """
    base_delay = len(text or "") * _DELAY_PER_CHAR_SECONDS
    jitter = random.uniform(_DELAY_JITTER_LOW, _DELAY_JITTER_HIGH)
    return max(_DELAY_MIN_SECONDS, min(_DELAY_MAX_SECONDS, base_delay * jitter))


def _credited_delay(natural: float, credit: float) -> tuple[float, float]:
    """(pause to show now, credit left) for one piece of a reply.

    ``credit`` is how long the person has already waited. The pause shrinks
    by it down to a one-second beat; a piece whose own pause is under a beat
    keeps its own. What the pause gave up is spent from the credit, so a long
    wait covers the pieces after the first one too.
    """
    credit = max(0.0, credit)
    shown = max(min(natural, _BEAT_SECONDS), natural - credit)
    return shown, max(0.0, credit - (natural - shown))


def _waited(waiting_since: float | None) -> float:
    """Seconds since ``waiting_since`` (time.monotonic()), 0 without one."""
    if waiting_since is None:
        return 0.0
    return max(0.0, time.monotonic() - waiting_since)


async def send_turn_reply(
    channel,
    text: str,
    *,
    attachments: tuple[str, ...] = (),
    waiting_since: float | None = None,
) -> bool:
    """Send a turn's reply through a Discord channel — chunked, paced.

    Each chunk shows a typing indicator for a length-proportional delay
    before the actual send so the conversation feels human-paced rather
    than instant-bot. Empty / whitespace-only text is a no-op. Send
    errors are logged but don't propagate.

    Returns whether the reply was DELIVERED (fix plan 5.10, R9d; Telegram's
    meaning): True once its first part landed, False when nothing reached
    them (or there was nothing to send). last_send_parts() says how many of
    how many landed, and last_send_error() says what killed the rest. Her
    record already holds the reply, so the caller marks it: not delivered,
    or only partly delivered. A file that could not ride is named to him in
    the words that did land and is felt separately (attachment_dropped,
    §15), so it does not make the words undelivered.

    2026-05-06: now accepts an attachments tuple of file paths. The
    files are uploaded with the FIRST chunk so PaLLi's screenshot/
    webcam-snap/file output flows back to Discord as a real attachment,
    not a path string in the reply text.

    2026-10-12: ``waiting_since`` is when the person's last message arrived
    (time.monotonic()). The typing pause is credited with the time already
    spent since then, down to a one-second beat; None paces as before.
    """
    _last_send_error.set("")
    _last_send_parts.set((0, 0))
    _last_send_typing.set(0.0)
    if (not text or not text.strip()) and not attachments:
        return False
    discord_files, notes, file_paths = await _prepare_attachments(attachments)
    text = _with_notes(text, notes)
    chunks = list(_chunk_text(text)) if text and text.strip() else [""]
    total = sum(1 for idx, chunk in enumerate(chunks) if chunk or (idx == 0 and discord_files))
    landed = 0
    credit = _waited(waiting_since)
    shown = 0.0
    try:
        for idx, chunk in enumerate(chunks):
            async with channel.typing():
                pause, credit = _credited_delay(_calculate_typing_delay(chunk), credit)
                shown += pause
                await asyncio.sleep(pause)
                if idx == 0 and discord_files:
                    await _send_carrying_files(channel, chunk, discord_files, file_paths)
                    landed += 1
                elif chunk:
                    await channel.send(chunk)
                    landed += 1
    except Exception as exc:
        logger.warning("discord reply: send failed: %s", exc)
        _last_send_error.set(_send_failure_reason(exc))
        _delivery_notice("turn_failed", error_class=type(exc).__name__, stage="discord_send")
    _last_send_typing.set(shown)
    _last_send_parts.set((landed, total))
    return landed > 0


async def _prepare_attachments(paths: tuple[str, ...]) -> tuple[list, list[str], list[str]]:
    """Build discord.File objects from local paths — and a note for every
    file that will NOT attach (missing, too big for Discord, or unbuildable),
    worded for him. Each drop is also felt (§15: attachment_dropped).

    Returns the files, the notes, and the local path behind each file in the
    same order: a send that dies still has to be able to name what was on it
    and where it still is (week 2, task 2)."""
    if not paths:
        return [], [], []
    notes: list[str] = []
    try:
        import discord  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("discord library unavailable for attachment delivery")
        for raw in paths:
            name = os.path.basename((raw or "").strip()) or "a file"
            notes.append(f"(not attached — {name}; my attachment hand isn't available right now)")
        _delivery_notice("attachment_dropped", reason="no_discord", count=len(paths))
        return [], notes, []
    files: list = []
    file_paths: list[str] = []
    cap_mb = DISCORD_MAX_ATTACHMENT_BYTES / (1024 * 1024)
    for raw in paths:
        path = (raw or "").strip()
        if not path:
            continue
        name = os.path.basename(path)
        if not os.path.exists(path) or not os.path.isfile(path):
            logger.warning("attachment path missing on disk: %s", path)
            notes.append(f"(not attached — {name} isn't on disk anymore)")
            _delivery_notice("attachment_dropped", reason="missing")
            continue
        size = os.path.getsize(path)
        if size > DISCORD_MAX_ATTACHMENT_BYTES:
            mb = size / (1024 * 1024)
            logger.warning("attachment over Discord's cap (%.1f MB > %.0f MB): %s", mb, cap_mb, path)
            notes.append(
                f"(not attached — {name} is {mb:.1f} MB, over Discord's {cap_mb:.0f} MB limit; "
                f"it's on disk at {path})"
            )
            _delivery_notice("attachment_dropped", reason="too_big", size_mb=round(mb, 1))
            continue
        try:
            files.append(discord.File(path))
            file_paths.append(path)
        except Exception as exc:
            logger.warning("discord.File construct failed for %s: %s", path, exc)
            notes.append(f"(not attached — {name} couldn't be packaged)")
            _delivery_notice("attachment_dropped", reason="construct_failed")
    return files, notes, file_paths


async def _resolve_discord_files(paths: tuple[str, ...]) -> list:
    """Compat wrapper: files only (the notes are handled by the senders)."""
    files, _notes, _file_paths = await _prepare_attachments(paths)
    return files


def _undelivered_note(path: str, exc: Exception) -> str:
    """DEFECT §15's other half: the send that carried the file died, so the
    file did not go. Name it, name what killed it, and say where it still
    is — she must not go on believing it landed."""
    return (f"(not delivered — {os.path.basename(path)}: {type(exc).__name__}; "
            f"the file is still at {path})")


async def _send_carrying_files(channel, chunk: str, discord_files: list,
                               file_paths: list[str]) -> None:
    """Send the chunk the attachments ride on. If that send raises, the text
    died with the files; try the same chunk ONCE more WITHOUT them, carrying
    a note for every file that was on it, so the words still land and the
    truth rides with them (the §16 note mechanism, _with_notes).

    The artifact is never deleted or moved — it stays at its path, named, so
    he can ask again. A retry that raises too falls through to the caller's
    handler, which logs it and makes it something she feels."""
    try:
        await channel.send(content=chunk or None, files=discord_files)
        return
    except Exception as exc:
        # `exc` is unbound once the clause ends, so word the notes here.
        logger.warning("discord reply: the send carrying %d file(s) failed: %s",
                       len(discord_files), exc)
        # §15: a drop she cannot feel is the defect. The retry landing is not
        # the file landing, so this is felt whether or not the retry works.
        _delivery_notice("attachment_dropped", reason="send_failed",
                         count=len(file_paths))
        notes = [_undelivered_note(p, exc) for p in file_paths]
    text = _with_notes(chunk, notes)
    await channel.send(content=text or None)


def _with_notes(text: str, notes: list[str]) -> str:
    if not notes:
        return text
    body = (text or "").strip()
    return (body + "\n\n" + "\n".join(notes)).strip() if body else "\n".join(notes)


async def send_turn_reply_cadenced(channel, cadenced_reply, *,
                                   waiting_since: float | None = None) -> bool:
    """Send a CadencedReply as a series of Discord messages with pacing.

    Each beat fires as its own message with two delays:
    1. The per-beat delay assigned by the cadence module (between the
       previous beat's send and this beat's typing-indicator start).
    2. A length-proportional typing delay (existing pacing logic).

    Cancellation is propagated — if the caller cancels the task, the
    remaining beats are dropped. The "user typed mid-burst" race
    protection lives at the on_typing handler level (see client.py),
    which cancels the in-flight task when a new user message arrives.

    Empty CadencedReply or single-beat with empty text → no-op.
    Falls back to plain send_turn_reply if the cadenced_reply is
    structurally a single beat.

    2026-05-06: attachments on the cadenced reply (e.g. screenshot
    paths) attach to the FIRST beat's outgoing message.

    Returns whether the reply was delivered, with the same meaning as
    send_turn_reply (fix plan 5.10, R9d): True once its first part landed.
    Every message of every beat is a part in last_send_parts().

    2026-10-12: ``waiting_since`` credits the typing pauses with the wait
    already behind the reply, as in send_turn_reply. The pauses between
    beats are the cadence's own and stay as planned.
    """
    _last_send_error.set("")
    _last_send_parts.set((0, 0))
    _last_send_typing.set(0.0)
    if cadenced_reply is None:
        return False
    beats = getattr(cadenced_reply, "beats", None)
    if not beats:
        return False
    attachments = tuple(getattr(cadenced_reply, "attachments", ()) or ())
    if len(beats) == 1:
        # Single-beat path is identical to send_turn_reply behavior.
        return await send_turn_reply(channel, beats[0].text, attachments=attachments,
                                     waiting_since=waiting_since)

    discord_files, notes, file_paths = await _prepare_attachments(attachments)
    # Lay the beats out before sending, so the parts are counted up front:
    # a burst that dies part-way can say "after 2 of 5".
    planned: list[tuple[int, object, list[str]]] = []
    notes_pending = list(notes)
    for idx, beat in enumerate(beats):
        text = (beat.text or "").strip()
        if not text:
            continue
        if notes_pending:
            text = _with_notes(text, notes_pending)
            notes_pending = []
        planned.append((idx, beat, list(_chunk_text(text))))
    total = sum(len(chunks) for _idx, _beat, chunks in planned)
    landed = 0
    credit = _waited(waiting_since)
    shown = 0.0
    try:
        first_chunk_sent = False
        for idx, beat, chunks in planned:
            # Inter-beat delay (cadence pacing). The first beat has 0
            # delay so it fires immediately; subsequent beats wait the
            # assigned amount before showing typing-indicator.
            if idx > 0 and beat.delay_after_previous_seconds > 0:
                await asyncio.sleep(beat.delay_after_previous_seconds)
            # Typing indicator + length-proportional pause + send.
            for chunk in chunks:
                async with channel.typing():
                    # Use a shorter typing delay for cadenced beats — the
                    # inter-beat pause already conveys "thinking time."
                    typing_delay, credit = _credited_delay(
                        max(0.3, _calculate_typing_delay(chunk) * 0.5), credit)
                    shown += typing_delay
                    await asyncio.sleep(typing_delay)
                    if not first_chunk_sent and discord_files:
                        await _send_carrying_files(channel, chunk, discord_files, file_paths)
                        first_chunk_sent = True
                    else:
                        await channel.send(chunk)
                        first_chunk_sent = True
                    landed += 1
    except asyncio.CancelledError:
        logger.info(
            "discord reply: cadenced burst cancelled mid-flight (likely "
            "user sent a new message); remaining beats dropped"
        )
        raise
    except Exception as exc:
        logger.warning("discord reply: cadenced send failed: %s", exc)
        _last_send_error.set(_send_failure_reason(exc))
        _delivery_notice("turn_failed", error_class=type(exc).__name__, stage="discord_send")
    _last_send_typing.set(shown)
    _last_send_parts.set((landed, total))
    return landed > 0
