"""Times on every line (step 2 of the release order, 2026-10-08).

The owner, 10-08: "time on all messages. theirs, hers, all of them. this is to
give her total awareness just like a real person would have over information
within conversations." Every transcript row already records when it was said
(``AgentMessage.timestamp``); until now only the text reached the model, so she
could not tell "an hour ago" from "just now". This pass renders each user and
assistant row's time in front of its first text block — ``[Oct 8 22:35]``, in
the server's local time (the zone her "Current Time" block names), the year added
only when it isn't this year.

Payload-only and pure, like its siblings in ``session.py``: the session's rows are
never mutated and nothing reaches disk. The stamp is a pure function of the stored
row, so the cached history prefix stays stable from one turn to the next. The
runner applies it LAST, to the copy that goes to the model, after every pass that
reads the start of a row's text (working notes, delivery marks, receipts).

She copies what she is shown (DEFECT §20), so an imitated stamp at the start of
her reply is removed by ``session.strip_working_note_mark`` before delivery; its
pattern and ``STAMP_RE`` describe the same label.

``companion.message_times`` (default on). The test suite holds it off for every
test that doesn't ask for it, so existing exact-payload pins stay pins.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone

from core.agent.session import AgentMessage, TextBlock

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# One label, one pattern: "[Oct 8 22:35]", or "[Mar 3 2025 15:00]" for another year.
STAMP_RE = re.compile(r"\[(?:" + "|".join(_MONTHS) + r") \d{1,2}(?: \d{4})? \d{2}:\d{2}\]")

_STAMPED_ROLES = ("user", "assistant")


def stamp_label(ts: datetime, *, now: datetime | None = None) -> str:
    """The label for one row's time. A naive time is read as UTC (rows are
    written aware, in UTC); ``now`` decides only whether the year is named."""
    aware = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    local = aware.astimezone()
    current = (now or datetime.now(timezone.utc)).astimezone()
    year = f" {local.year}" if local.year != current.year else ""
    return f"[{_MONTHS[local.month - 1]} {local.day}{year} {local:%H:%M}]"


def stamp_times_for_llm(messages: list[AgentMessage], *, now: datetime | None = None) -> list[AgentMessage]:
    """Each user and assistant row with its time in front of its first text block.

    Pure (rows are copied, never mutated), idempotent (a row whose first text
    already starts with a stamp is left alone), and identity on rows with no text
    (a tool call, a tool result)."""
    if not messages:
        return messages
    out: list[AgentMessage] = []
    for m in messages:
        if m.role not in _STAMPED_ROLES or m.timestamp is None:
            out.append(m)
            continue
        idx = next((i for i, b in enumerate(m.content)
                    if isinstance(b, TextBlock) and b.text.strip()), None)
        if idx is None or STAMP_RE.match(m.content[idx].text):
            out.append(m)
            continue
        label = stamp_label(m.timestamp, now=now)
        content = list(m.content)
        content[idx] = replace(content[idx], text=f"{label} {content[idx].text}")
        out.append(replace(m, content=content))
    return out


def times_shown(messages: list[AgentMessage]) -> frozenset[str]:
    """The time labels on rows already stamped (the runner's payload), for the
    reply filter (Fable's checkpoint review, 10)."""
    out: set[str] = set()
    for m in messages or []:
        if m.role in _STAMPED_ROLES:
            first = next((b.text for b in m.content if isinstance(b, TextBlock) and b.text.strip()), "")
            hit = STAMP_RE.match(first)
            if hit:
                out.add(hit.group(0))
    return frozenset(out)


def message_times_enabled() -> bool:
    """companion.message_times. Unbooted settings or any error read as off."""
    try:
        from core.turn.companion_flags import companion_settings

        comp = companion_settings()
        return comp is not None and bool(getattr(comp, "message_times", True))
    except Exception:
        return False
