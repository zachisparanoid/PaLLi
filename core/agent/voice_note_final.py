"""The voice message, made last (R15, memory that reaches, Group V).

On 10-05 PaLLi sent a voice note made early in a reply, which was right, and
a text written after the checks, which was wrong: the ``voice_note`` tool
made the note the moment it was called. Now the tool only notes the words on
the turn's ``ToolContext.voice_note_words`` (one entry; the last call wins),
and the runner makes the one note when the reply is finished, from the words
PaLLi last chose (``finish``). While words wait, every later round's
trailing note shows what the note will say (``pending_line``, item 36), so
PaLLi can replace them.

The doors are unchanged: they read ``RunResult.voice_notes`` as before
(``bots/common/voice_note.py`` ``plan_voice_reply``), and a reply still
carries at most ``core.voice_replies.MAX_VOICE_NOTES_PER_REPLY`` (1).

The words are Fable's items 34-36, Draft 2, approved by the owner on 10-13
(items 34 and 35 are the tool's answers, in her_hands.py).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

TOOL_NAME = "voice_note"
# How much of the words a line PaLLi reads quotes back.
QUOTE_CHARS = 240
# Item 36: the reminder on every later round while a note is waiting.
PENDING_LINE = ('(Your voice message will say: "{words}". Call voice_note again if what '
                "you've found changes it.)")
# Item 36's first sentence alone, for a step that can't call voice_note (the
# brake's reply step): Fable's ruling W3, the whole-branch review (M1).
PENDING_LINE_NO_CALL = '(Your voice message will say: "{words}".)'


def quoted(words: str) -> str:
    """The words as a line PaLLi reads quotes them: whole up to 240
    characters, else cut with an ellipsis."""
    text = str(words or "").strip()
    if len(text) <= QUOTE_CHARS:
        return text
    return text[:QUOTE_CHARS - 3].rstrip() + "…"


def pending_words(tool_ctx: Any) -> str:
    """The words the reply's voice message will say, or "" when none wait:
    no call yet, or the note is already made."""
    if getattr(tool_ctx, "voice_notes", None):
        return ""
    words = getattr(tool_ctx, "voice_note_words", None) or []
    return str(words[-1] or "").strip() if words else ""


def will_speak(tool_ctx: Any) -> bool:
    """A voice message is made or waiting to be made this reply — what her
    quiet choice's answer says (ruling FW-3: the voice message goes)."""
    return bool(getattr(tool_ctx, "voice_notes", None)
                or getattr(tool_ctx, "voice_note_words", None))


def pending_line(tool_ctx: Any, *, can_call: bool = True) -> str:
    """Item 36 with the last words (240 characters), "" when none wait.
    ``can_call=False`` (a step that offers no voice_note): its first
    sentence alone, so the line never asks for a call the step can't make."""
    words = pending_words(tool_ctx)
    if not words:
        return ""
    return (PENDING_LINE if can_call else PENDING_LINE_NO_CALL).format(words=quoted(words))


def brake_answer(name: str, content: str) -> str:
    """What the runner's same-answer brake (second read F3) compares for
    one call. A voice_note answer quotes PaLLi's own words back, so a
    reworded call is the same answer with new words in it: compared without
    the quoted words, a run that keeps rewording instead of finishing meets
    the brake the way the 10-01 loop did (DEFECT §56). Every other tool's
    answer is compared as it is."""
    text = str(content or "")
    if str(name or "") != TOOL_NAME:
        return text
    start, end = text.find('"'), text.rfind('"')
    return text[:start] + text[end + 1:] if 0 <= start < end else text


async def _synthesize(text: str) -> bytes | None:
    """Seam: the words through the speech facade's configured voice, as
    OGG/Opus bytes. Lazy, so a run that never speaks never loads the voice
    stack."""
    from voice.voice_note_out import synthesize_voice_note
    return await synthesize_voice_note(text)


async def finish(tool_ctx: Any, *,
                 synthesize: Callable[[str], Awaitable[bytes | None]] | None = None) -> int:
    """Make the reply's one voice message from the last words, once the
    reply is finished. Appends the bytes to ``tool_ctx.voice_notes`` (the
    ``deliveries`` contract: only once they exist) and returns 1; returns 0
    when no words wait, a note is already made, or the speech can't be made
    (logged as a WARNING; the text still goes)."""
    words = pending_words(tool_ctx)
    if not words:
        return 0
    make = synthesize or _synthesize
    try:
        audio = await make(words)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - the note is lost, never the reply
        logger.warning("voice note: the speech couldn't be made at the end of the reply "
                       "(%s); no voice message goes, the text still does", type(exc).__name__)
        return 0
    if not audio:
        logger.warning("voice note: the speech couldn't be made at the end of the reply; "
                       "no voice message goes, the text still does")
        return 0
    tool_ctx.voice_notes.append(audio)
    logger.info("voice note: made at the end of the reply, from the last words "
                "(%d chars)", len(words))
    return 1
