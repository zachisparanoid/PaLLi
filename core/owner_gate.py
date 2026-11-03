"""The owner gate (item 7a, 2026-08-19): who she is talking to.

Until today ``bots/discord/intake.py`` stamped every Discord message — and
``voice/runtime.py`` every spoken utterance — with the OWNER's id, so a
stranger in his guild or voice channel was him: his tools (shell, files,
reminders), his memory, his stakes in the prompt. Owner's call (08-19):
only his Discord identity is the owner; anyone else is a GUEST who gets
plain replies — no tools, none of his private bands, nothing written into
his memory.

Pure helpers; the surfaces (Discord text, voice) classify the author and
build the turn accordingly; dispatch + the context adapter honor
``origin == "guest"`` / ``is_owner == False``.

Policy on an UNRESOLVED owner ref (no ``LOCAL_OWNER_DISCORD_USER_ID``, or a
DB hiccup): "unknown" is a GUEST — fail closed (week 2 task 3, gap §5.1.5).
Until 2026-09-10 it took the legacy owner path, so on a fresh clone with the
owner id never set, the first stranger to type in her channel WAS him: his
shell, his files, his memory. A person nobody can prove is him is a guest;
the cost is that during an outage his own turn is a plain reply, and boot
says the id is missing out loud (the punch list) so it is never a mystery.
Surfaces ask ``is_owner_verdict`` — one question, one answer, both doors.
"""
from __future__ import annotations

import re
from typing import Any, Optional

GUEST_ORIGIN = "guest"
# The tag guest_content puts on a guest's line. The room file keeps no author
# per row, so this is how a reader of the file knows another speaker's words
# (Batch 8, final fix wave — ruling FW-17). Any display name, brackets
# included ("[CEO] Sam", "Sam [he/him]" — ruling FW-17a): the name is never
# on more than one line (guest_content collapses its whitespace), and a
# false positive only reorders an owner payload harmlessly.
_GUEST_LINE = re.compile(r"^\[[^\n]+?\]: ")


def classify_author(author_id: Any, owner_discord_id: Optional[Any]) -> str:
    """'owner' | 'guest' | 'unknown'."""
    if owner_discord_id is None:
        return "unknown"
    try:
        owner = int(owner_discord_id)
    except (TypeError, ValueError):
        return "unknown"
    try:
        author = int(author_id)
    except (TypeError, ValueError):
        return "guest"
    return "owner" if author == owner else "guest"


def is_owner_verdict(verdict: str) -> bool:
    """True ONLY for "owner". "unknown" is a guest — fail closed.

    The one place the rule lives. A surface that asked ``== "guest"`` let
    "unknown" fall through to the owner path; asking this instead makes the
    owner path something a caller must be proved into, never defaulted into.
    """
    return str(verdict or "") == "owner"


def is_guest_turn(turn: Any) -> bool:
    md = getattr(turn, "metadata", None)
    return bool(md is not None and str(getattr(md, "origin", "") or "") == GUEST_ORIGIN)


def guest_content(display_name: Any, text: str) -> str:
    """The guest's words, tagged with who said them — the room is shared,
    and her channel transcript should stay legible about who is speaking."""
    name = " ".join(str(display_name or "").split()) or "guest"
    return f"[{name}]: {text}"


def is_guest_line(text: Any) -> bool:
    """Does this transcript line carry guest_content's tag — a guest's words
    in a shared room, not the owner's?"""
    return bool(_GUEST_LINE.match(str(text or "")))
