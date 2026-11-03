"""Render a turn's reply back through a Telegram chat (telegram door,
2026-09-14, task 2).

The mirror of bots/discord/reply.py against a different wall: 4,096
characters per message instead of 2,000, 50 MB per file instead of 10, and
a rate limiter that says exactly how long to wait instead of a blanket 429.

Three rules carried over from §15/§16, because they were paid for:

  * a file that will not fit is NAMED, not dropped — with where it still is,
    so he can ask again;
  * a send that fails is something she FEELS (``_delivery_notice``), because
    a delivery she cannot feel is one she goes on believing in;
  * a send never raises into the client — one failed chat must not take down
    the door. It does say whether anything LANDED, though (final review, I1):
    a reply he never got is his to lose, but a REMINDER nothing delivered
    must not be recorded as delivered, and bots/telegram/adapter.py turns
    that False into the raise ``core/turn/outbound.py`` and
    ``core/reminders/binding.py`` already know how to read.

``telegram.error.RetryAfter`` cannot be imported here (the library is not a
test dependency and is imported lazily everywhere else in this package), so
it is recognised STRUCTURALLY: a class named RetryAfter carrying a
``retry_after``. That is the whole shape the handling needs, and it is
exactly what the real exception is.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextvars import ContextVar

from bots.common.delivery_notice import delivery_notice as _delivery_notice
from bots.discord.reply import _chunk_text

logger = logging.getLogger(__name__)

# The class name of the exception the last send swallowed, so the caller that
# needs to RAISE about it can say what killed the send. A ContextVar and not
# a module global: two chats sending at once are two tasks, and each task
# gets its own copy — one chat's flood limit can never be named as another's.
_last_send_error: ContextVar[str] = ContextVar(
    "palli_telegram_last_send_error", default="")
# And how many of its parts landed (fix plan 5.10, R9d), so a reply whose
# start reached him and whose rest died is recorded as exactly that. A part is
# one message of her words. Documents and the voice note are not parts: the
# words are what her record holds (R9f).
_last_send_parts: ContextVar[tuple[int, int]] = ContextVar(
    "palli_telegram_last_send_parts", default=(0, 0))


def last_send_error() -> str:
    """What killed the most recent send in THIS task, or "" if nothing did."""
    return _last_send_error.get()


def last_send_parts() -> tuple[int, int]:
    """(landed, total) messages of her words in the most recent send in THIS
    task."""
    return _last_send_parts.get()


TELEGRAM_MAX_LEN = 4096
# Telegram's own wall for a bot uploading a file. A local Bot API server
# raises it to 2 GB, which is what the override is for.
TELEGRAM_MAX_ATTACHMENT_BYTES = int(
    os.environ.get("PALLI_TELEGRAM_MAX_ATTACHMENT_BYTES", str(50 * 1024 * 1024))
)
# A beat between chunks. Telegram delivers instantly and a three-message
# wall arriving in one frame reads as a machine emptying a buffer.
CHUNK_SPACING_SECONDS = 1.0
# Telegram's typing indicator expires after about five seconds, so a run
# that takes a minute has to keep saying it.
TYPING_INTERVAL_SECONDS = 4.0


def _is_retry_after(exc: BaseException) -> bool:
    """Telegram's flood-control exception, recognised by shape."""
    return type(exc).__name__ == "RetryAfter" and hasattr(exc, "retry_after")


async def _send_once(call) -> None:
    """Make one send. A RetryAfter is honoured ONCE, with the wait Telegram
    itself asked for — a blind retry loop against a flood limiter is how a
    bot earns a longer one. A second refusal goes to the caller."""
    try:
        await call()
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        if not _is_retry_after(exc):
            raise
        delay = float(getattr(exc, "retry_after", 0) or 0)
        logger.warning("telegram reply: flood control — waiting %.0fs, once", delay)
        await asyncio.sleep(delay)
    await call()


def _with_notes(text: str, notes: list[str]) -> str:
    if not notes:
        return text
    body = (text or "").strip()
    return (body + "\n\n" + "\n".join(notes)).strip() if body else "\n".join(notes)


def first_chunk(text: str) -> str:
    """The first Telegram message this text will become.

    What the voice lane speaks when a reply is longer than one message
    (task 5): the whole answer is in the text, and a ten-minute voice note
    is one nobody plays.
    """
    for chunk in _chunk_text(str(text or ""), max_len=TELEGRAM_MAX_LEN):
        return chunk
    return ""


def _weigh_attachments(paths: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split the paths into what can go and a note for everything that
    cannot (§15). Weighed BEFORE the send, so nothing bounces."""
    sendable: list[str] = []
    notes: list[str] = []
    cap_mb = TELEGRAM_MAX_ATTACHMENT_BYTES / (1024 * 1024)
    for raw in paths or ():
        path = str(raw or "").strip()
        if not path:
            continue
        name = os.path.basename(path)
        if not os.path.isfile(path):
            logger.warning("telegram reply: attachment path missing on disk: %s", path)
            notes.append(f"(not attached — {name} isn't on disk anymore)")
            _delivery_notice("attachment_dropped", reason="missing")
            continue
        size = os.path.getsize(path)
        if size > TELEGRAM_MAX_ATTACHMENT_BYTES:
            mb = size / (1024 * 1024)
            logger.warning("telegram reply: %s is %.1f MB, over the %.0f MB limit",
                           name, mb, cap_mb)
            notes.append(
                f"(not delivered — {name}: {mb:.1f} MB is over Telegram's "
                f"{cap_mb:.0f} MB limit; the file is still at {path})")
            _delivery_notice("attachment_dropped", reason="too_big",
                             size_mb=round(mb, 1))
            continue
        sendable.append(path)
    return sendable, notes


async def _send_documents(bot, chat_id: int, paths: list[str]) -> bool:
    """True once at least one document actually landed. The honest
    "(not delivered — …)" line that follows a failed upload is not a
    delivery of the FILE, so it does not count as one."""
    landed = False
    for path in paths:

        async def _one(p=path) -> None:
            # Re-opened per attempt on purpose: a retry with a handle the
            # failed attempt already read from would upload nothing.
            with open(p, "rb") as handle:
                await bot.send_document(chat_id=chat_id, document=handle,
                                        filename=os.path.basename(p))

        try:
            await _send_once(_one)
            landed = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # §15's other half: the send carrying the file died, so the file
            # did not go. Name it, name what killed it, say where it is.
            logger.warning("telegram reply: sending %s failed: %s", path, exc)
            _last_send_error.set(type(exc).__name__)
            _delivery_notice("attachment_dropped", reason="send_failed", count=1)
            await _send_once(lambda p=path, e=exc: bot.send_message(
                chat_id=chat_id,
                text=(f"(not delivered — {os.path.basename(p)}: "
                      f"{type(e).__name__}; the file is still at {p})")))
    return landed


async def send_turn_reply(
    bot,
    chat_id: int,
    text: str,
    *,
    attachments: tuple[str, ...] = (),
    reply_to_message_id: int | None = None,
    voice_note: bytes | None = None,
) -> bool:
    """Send a reply into a Telegram chat — chunked, spaced, honest.

    Returns True once ANYTHING landed: the first chunk of text, a document,
    or the voice note. False means the chat got nothing — which is what
    bots/telegram/adapter.py raises on, because a reminder or a reach-out
    recorded as delivered when nothing arrived is a lie she then remembers
    (final review, I1). The client's own reply callers may ignore it.

    Empty text with no attachments is a no-op. Send errors are logged and
    felt, never raised: one unreachable chat must not break the door.

    ``voice_note`` is OGG/Opus bytes to send as a voice note AFTER the words
    (task 5). Alongside the text, never instead of it: the words are the
    reply, and they arrive first. The client decides whether there is one.
    """
    # Both reset BEFORE the nothing-to-send return: a no-op send must not
    # keep an earlier send's reason (final review, minor 10).
    _last_send_parts.set((0, 0))
    _last_send_error.set("")
    if (not text or not text.strip()) and not attachments and not voice_note:
        return False
    delivered = False
    sendable, notes = _weigh_attachments(tuple(attachments or ()))
    text = _with_notes(text or "", notes)
    chunks = list(_chunk_text(text, max_len=TELEGRAM_MAX_LEN)) if text.strip() else []
    landed = 0

    try:
        for index, chunk in enumerate(chunks):
            if index:
                await asyncio.sleep(CHUNK_SPACING_SECONDS)
            kwargs = {"chat_id": chat_id, "text": chunk}
            if index == 0 and reply_to_message_id is not None:
                kwargs["reply_to_message_id"] = int(reply_to_message_id)
            await _send_once(lambda kw=kwargs: bot.send_message(**kw))
            delivered = True
            landed += 1
            if index == 0 and sendable:
                # The files ride right after the first chunk, so the words
                # that explain them arrive first.
                if await _send_documents(bot, chat_id, sendable):
                    delivered = True
                sendable = []
        if sendable:
            if await _send_documents(bot, chat_id, sendable):
                delivered = True
        if voice_note:
            try:
                await _send_once(lambda: bot.send_voice(
                    chat_id=chat_id, voice=voice_note))
                delivered = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Its own small failure, on purpose: the words are already
                # in his hands, and filing this as a failed TURN would be a
                # lie about a reply he is reading. §15 still applies — a
                # thing she tried to hand him did not land, and she feels it
                # as the thing it was (review round 1, minor 3).
                logger.warning("telegram reply: voice note send failed: %s", exc)
                _last_send_error.set(type(exc).__name__)
                _delivery_notice("voice_note_dropped", reason="voice_send_failed")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram reply: send failed: %s", exc)
        _last_send_error.set(type(exc).__name__)
        _delivery_notice("turn_failed", error_class=type(exc).__name__,
                         stage="telegram_send")
    _last_send_parts.set((landed, len(chunks)))
    return delivered


async def send_turn_reply_cadenced(bot, chat_id: int, cadenced_reply) -> bool:
    """Send a CadencedReply as a series of Telegram messages.

    Each beat is its own message, after the pause the cadence module gave
    it. Cancellation propagates — a barge-in drops the remaining beats,
    which is the point of cadence in the first place.

    Returns the same thing send_turn_reply does (fix plan 5.10): True once
    anything landed, False when the chat got nothing (the reason is in
    last_send_error()). The single-beat path already returns
    send_turn_reply's answer, and one function should not mean two things.
    Every message of every beat is a part in last_send_parts().
    """
    _last_send_error.set("")
    _last_send_parts.set((0, 0))
    if cadenced_reply is None:
        return False
    beats = getattr(cadenced_reply, "beats", None)
    if not beats:
        return False
    attachments = tuple(getattr(cadenced_reply, "attachments", ()) or ())
    if len(beats) == 1:
        return await send_turn_reply(bot, chat_id, beats[0].text, attachments=attachments)

    sendable, notes = _weigh_attachments(attachments)
    # Lay the beats out before sending, so the parts are counted up front:
    # a burst that dies part-way can say "after 2 of 5".
    planned: list[tuple[int, object, list[str]]] = []
    pending_notes = list(notes)
    for index, beat in enumerate(beats):
        text = str(getattr(beat, "text", "") or "").strip()
        if not text:
            continue
        if pending_notes:
            text = _with_notes(text, pending_notes)
            pending_notes = []
        planned.append((index, beat, list(_chunk_text(text, max_len=TELEGRAM_MAX_LEN))))
    total = sum(len(chunks) for _index, _beat, chunks in planned)
    landed = 0
    first_sent = False
    try:
        for index, beat, chunks in planned:
            delay = float(getattr(beat, "delay_after_previous_seconds", 0.0) or 0.0)
            if index and delay > 0:
                await asyncio.sleep(delay)
            for chunk_index, chunk in enumerate(chunks):
                if chunk_index or first_sent:
                    await asyncio.sleep(CHUNK_SPACING_SECONDS)
                await _send_once(lambda c=chunk: bot.send_message(
                    chat_id=chat_id, text=c))
                landed += 1
                if not first_sent and sendable:
                    await _send_documents(bot, chat_id, sendable)
                    sendable = []
                first_sent = True
    except asyncio.CancelledError:
        logger.info("telegram reply: cadenced burst cancelled mid-flight; "
                    "remaining beats dropped")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram reply: cadenced send failed: %s", exc)
        _last_send_error.set(type(exc).__name__)
        _delivery_notice("turn_failed", error_class=type(exc).__name__,
                         stage="telegram_send")
    _last_send_parts.set((landed, total))
    # Not first_sent: that is set only after the first beat's files went, so a
    # dead upload behind a beat that DID land would report nothing delivered
    # (R9g). False means nothing landed, as on the Discord door.
    return landed > 0


class typing_while:
    """Show "typing…" for as long as the block runs.

    Telegram's chat action expires after about five seconds, so this re-sends
    it on an interval rather than once. The analogue of Discord's
    ``async with channel.typing()``, and like it, purely cosmetic: a failure
    to show it never touches the reply.
    """

    def __init__(self, bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = int(chat_id)
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "typing_while":
        try:
            await self._bot.send_chat_action(chat_id=self._chat_id, action="typing")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("telegram reply: typing action failed: %s", exc)
        self._task = asyncio.create_task(self._keep_showing_it())
        return self

    async def _keep_showing_it(self) -> None:
        while True:
            await asyncio.sleep(TYPING_INTERVAL_SECONDS)
            try:
                await self._bot.send_chat_action(
                    chat_id=self._chat_id, action="typing")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("telegram reply: typing action failed: %s", exc)

    async def __aexit__(self, *exc_info) -> bool:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return False
