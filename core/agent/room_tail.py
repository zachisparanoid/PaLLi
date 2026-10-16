"""The room as anyone there reads it (step 2, part 4; the who-is-talking plan, A1).

One reader for every quick read of a shared room: the room pass's first read of
a guest's line, the quick vote on the owner's turns there, and the mood read.
Each line says who said it, as her reply call reads it (core.agent.speakers),
so a guest can't pass for the owner in a quick read either.

Before 2026-10-08 the quick vote and the mood read on the owner's turns read
the conversations database, which never holds a guest's words: they were blind
to everyone else in the room (10-06, she took a friend's message for the
owner's). The reader lived in bots/common/room_pass.py; it moved here so the
runner can use it too (core never imports bots/).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable, Sequence

from core.agent.message_times import stamp_label
from core.agent.session import AgentMessage, Session, TextBlock, diet_for_llm, mark_working_notes_for_llm
from core.agent.speakers import OWNER_MARK, label_names, label_speakers_for_llm, speaker_of
from core.owner_gate import is_guest_line, is_guest_turn

logger = logging.getLogger(__name__)

TAIL_MESSAGES = 16
LINE_CHARS = 400
# The room card's "recently here": how far back it looks, how many it names.
SPEAKER_ROWS = 40
SPEAKER_NAMES = 6

# The room the runner read for this turn, for the mood read in recall
# (ai.turn_read.room_conversation). Set only around recall, in a shared room
# with group chat on; None everywhere else, so every other read is today's.
current_room_tail: ContextVar[str | None] = ContextVar("palli_room_tail", default=None)
# The names this turn's message calls, in her own words (core.room_people), for
# the same read (ai.turn_read.room_names): set beside the room tail, "" elsewhere.
current_room_names: ContextVar[str] = ContextVar("palli_room_names", default="")


def _words(m: Any) -> str:
    return "".join(b.text for b in m.content if isinstance(b, TextBlock))


async def load(key: str, *, root: Any) -> list[AgentMessage] | None:
    """The room's transcript, or None when it can't be read."""
    try:
        session = await Session.open_or_create(key, root=Path(root))
    except Exception as exc:
        logger.warning("room tail: could not read %s: %s", key, exc)
        return None
    return list(session.messages)


def rows_saying(messages: Sequence[AgentMessage], texts: Iterable[str]) -> set[int]:
    """The rows of the lines being read, found by their words: for each text,
    the last user row saying exactly that (a second identical text takes the
    row before it)."""
    taken: set[int] = set()
    for text in reversed([t for t in texts if str(t).strip()]):
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if i not in taken and m.role == "user" and _words(m) == text:
                taken.add(i)
                break
    return taken


def render(messages: Sequence[AgentMessage], *, drop: Iterable[int] = (),
           line_chars: int | None = LINE_CHARS) -> str:
    """The room's last lines as anyone there reads them, without the rows in
    ``drop`` (the message(s) being read, which are shown on their own).
    ``line_chars`` bounds each line (None: whole lines, for the message
    being read itself — the first look's, R14).

    Under duplex the text beside a tool call was never sent: it is stamped as
    a working note first (as the runner does before its diet), so the stranger
    rendering leaves it out (final fix wave, ruling FW-2)."""
    from core.agent import duplex as _dx

    dropped = set(drop)
    rows = [m for i, m in enumerate(messages) if i not in dropped]
    if _dx.duplex_enabled():
        rows = mark_working_notes_for_llm(rows)
    seen = label_speakers_for_llm(diet_for_llm(rows, keep_from=len(rows), audience="stranger"))
    lines: list[str] = []
    for m in seen:
        if m.role not in ("user", "assistant") or getattr(m, "delivery", "") == "none":
            continue
        text = " ".join(_words(m).split())
        if not text:
            continue
        if line_chars is not None and len(text) > line_chars:
            text = text[: line_chars - 1].rstrip() + "…"
        when = ""
        try:
            when = stamp_label(m.timestamp) + " "     # the one time label (the plan, T)
        except Exception:
            pass
        lines.append(f"{when}{'PaLLi: ' if m.role == 'assistant' else ''}{text}")
    return "\n".join(lines[-TAIL_MESSAGES:])


def latest(turns: Sequence[Any]) -> str:
    """The message(s) being read, each saying who sent it, with the same labels
    as the room's lines. A guest's turn with no speaker record never gets the
    owner's mark: an untagged line would otherwise read as the owner's."""
    rows = []
    for turn in turns:
        text = str(getattr(turn, "content", "") or "")
        if not text.strip():
            continue
        speaker = speaker_of(turn)
        if speaker is None and is_guest_turn(turn) and not is_guest_line(text):
            speaker = {"kind": "guest"}
        rows.append(AgentMessage(role="user", content=[TextBlock(text=text)], speaker=speaker))
    return "\n".join(_words(m) for m in label_speakers_for_llm(rows))


def recent_speakers(messages: Sequence[AgentMessage]) -> list[str]:
    """Who has spoken in the room lately, newest first, each once, as each is
    labeled in the room (the owner with the owner's mark). Never a guess: her
    lines, system notes and senders the app hides name nobody.

    The owner once (2026-10-12: the card read "Robin ★owner, ★owner"): older
    rows carry the mark without a name, so every owner-marked label is the one
    owner, shown by the name the account gave when there is one."""
    names: list[str] = []
    owner_at: int | None = None
    for name in reversed(label_names(list(messages), last=SPEAKER_ROWS)):
        if not name:
            continue
        if name.endswith(OWNER_MARK):
            if owner_at is not None:
                if names[owner_at] == OWNER_MARK:
                    names[owner_at] = name
                continue
            owner_at = len(names)
        elif name in names:
            continue
        names.append(name)
        if len(names) == SPEAKER_NAMES:
            break
    return names
