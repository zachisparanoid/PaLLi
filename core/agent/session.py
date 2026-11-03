from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
import threading
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import replace, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.agent.quiet_choice import STAY_QUIET  # no import back: quiet_choice is a leaf
from core.agent.speakers import normalize_speaker  # a leaf too: imports nothing from core


logger = logging.getLogger(__name__)


def _sanitize_session_key(key: str) -> str:
    """Make a session_key safe for use as a filename on all platforms.
    Replaces any char outside [A-Za-z0-9_.-] with _ (covers Windows :, /, \\, *, ?, etc.).
    """
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", key)


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class AgentMessage:
    role: Literal["user", "assistant", "tool"]
    content: list[ContentBlock]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # DeepSeek (and other thinking models) emit a separate ``reasoning_content``
    # alongside the visible answer. When the model makes a tool call, that
    # reasoning MUST be passed back on the follow-up request or the API 400s
    # ("reasoning_content in the thinking mode must be passed back"). We keep it
    # here so the same-turn tool round-trip can replay it. INTENTIONALLY NOT
    # persisted (see _msg_to_jsonl): it's turn-scoped — once a turn's tool loop
    # resolves, the reasoning is dead weight and shouldn't bloat future prompts
    # or replay stale across user turns (matches the reasoner contract).
    reasoning: str = ""
    # Fix plan 5.10 (Batch 4 task 5): how much of this reply the door failed
    # to deliver. "" (absent) = delivered; "none" = nothing reached them;
    # "partial" = the start did, the rest did not. Set after the fact by
    # Session.mark_undelivered and stamped into the payload by
    # mark_undelivered_for_llm. Unlike reasoning it IS persisted, because it
    # has to survive to her next turn. It is written only when set, so every
    # ordinary line stays byte-identical.
    delivery: str = ""
    # Step 2 (2026-10-08): who sent this line, from the account at intake —
    # never from its text (core/agent/speakers.py). Persisted, written only
    # when set, so an ordinary line stays byte-identical; a bad value on disk
    # reads as None and never costs the row.
    speaker: dict[str, Any] | None = None


def _block_to_dict(b: ContentBlock) -> dict[str, Any]:
    if isinstance(b, TextBlock):
        return {"type": "text", "text": b.text}
    if isinstance(b, ToolUseBlock):
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if isinstance(b, ToolResultBlock):
        return {"type": "tool_result", "tool_use_id": b.tool_use_id, "content": b.content, "is_error": b.is_error}
    raise TypeError(f"unknown block type: {type(b).__name__}")


def _block_from_dict(d: dict[str, Any]) -> ContentBlock:
    t = d.get("type")
    if t == "text":
        return TextBlock(text=d["text"])
    if t == "tool_use":
        return ToolUseBlock(id=d["id"], name=d["name"], input=d.get("input", {}))
    if t == "tool_result":
        return ToolResultBlock(
            tool_use_id=d["tool_use_id"],
            content=d.get("content", ""),
            is_error=bool(d.get("is_error", False)),
        )
    raise ValueError(f"unknown block type: {t!r}")


def _msg_to_jsonl(m: AgentMessage) -> str:
    d: dict[str, Any] = {
        "role": m.role,
        "content": [_block_to_dict(b) for b in m.content],
        "timestamp": m.timestamp.isoformat(),
    }
    if m.delivery:
        # Only when set. An ordinary line must not grow a key: the history
        # prefix is what the provider caches (Batch 2).
        d["delivery"] = m.delivery
    if m.speaker:
        d["speaker"] = m.speaker
    return json.dumps(d)


def _msg_from_dict(d: dict[str, Any]) -> AgentMessage:
    return AgentMessage(
        role=d["role"],
        content=[_block_from_dict(b) for b in d.get("content", [])],
        timestamp=datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else datetime.now(timezone.utc),
        delivery=_delivery_state(d.get("delivery")),
        speaker=normalize_speaker(d.get("speaker")),
    )


_DELIVERY_STATES = ("none", "partial")


def _delivery_state(value: Any) -> str:
    """Only a known failure state survives a load. Anything else reads as
    delivered, which is what an absent key has always meant."""
    v = str(value or "").strip().lower()
    return v if v in _DELIVERY_STATES else ""


def _is_tool_result_message(m: AgentMessage) -> bool:
    """True if the message carries a tool result (role 'tool' or any
    ``ToolResultBlock``). A window must never START on one: a tool_result is
    valid only in a payload that also holds its matching tool_use, so an
    orphaned leading tool_result is a provider 400.
    """
    if m.role == "tool":
        return True
    return any(isinstance(b, ToolResultBlock) for b in m.content)


def _redact_message(msg: AgentMessage) -> AgentMessage:
    """Secrets redaction (item 7c, 2026-08-19): every text block and tool
    result passes through his denylist before the transcript keeps it —
    on disk AND in the in-memory list the payload is built from. Identity
    when the list is empty."""
    try:
        from core.secrets_redaction import redact
    except Exception:
        return msg
    changed = False
    blocks: list[ContentBlock] = []
    for b in msg.content:
        if isinstance(b, TextBlock):
            t = redact(b.text)
            if t != b.text:
                b = replace(b, text=t); changed = True
        elif isinstance(b, ToolResultBlock):
            c = redact(b.content)
            if c != b.content:
                b = replace(b, content=c); changed = True
        blocks.append(b)
    return replace(msg, content=blocks) if changed else msg


# DEFECT_RECORD §17 (2026-09-01): the list that landed on scratch paper.
# Under duplex, text written in an iteration that also calls a tool is a
# WORKING NOTE — never delivered. Her transcript keeps it verbatim (custody),
# so on the next turn she read her own note as something he had seen ("the
# budget list I sent"). The payload she reads now stamps such text, so she
# can never be wrong about what reached him. Payload-only, pure, idempotent;
# identity when there is nothing to mark. Applied BEFORE diet_for_llm, which
# strips the tool blocks that identify a working note.
WORKING_NOTE_MARK = "[system: working note — never delivered; they have not seen this] "

# Fix plan 5.10 (Batch 4 task 5): the working note's sibling, for a reply the
# door tried to send and could not. The transcript is written before the send,
# so a failed send left her reading the reply as said. The door now flags the
# stored message (AgentMessage.delivery), and her payload carries this stamp
# on it. Same law as the working note: payload-only, the record keeps her
# words, and it is stripped at the boundary if she writes it herself. A reply
# whose start landed and whose rest died gets the partial stamp instead: she
# must not repeat what he read, or build on what he never saw (R9d).
UNDELIVERED_MARK = "[system: not delivered — they have not seen this] "
PARTIAL_DELIVERY_MARK = "[system: only partly delivered — they saw the start, not the rest] "
_DELIVERY_MARKS = {"none": UNDELIVERED_MARK, "partial": PARTIAL_DELIVERY_MARK}

# DEFECT_RECORD §20 (2026-09-02): the stamp she learned to write. The §17
# stamp read as her own prose, and within a day she copied it into a real
# reply that reached his phone verbatim. The stamp is now unmistakably the
# system's (bracketed, labelled), and anything the model writes that looks
# like one — this form, the legacy prose form, doubled, indented — is
# stripped at the model boundary by strip_working_note_mark: before her
# record, before the chunker, before Discord. Idempotent; identity on
# clean text. The not-delivered stamps (5.10), full and partial, are removed
# here too: she copies the stamps she is shown. So are the two forms of the
# ``system`` row persist_delivery_failure writes into ``conversations``
# ("[not delivered to them: …]", "[only partly delivered to them: …]"): the
# recent-lines blocks show her that row as a system note.
# The time label every line carries in the payload (core/agent/message_times.py,
# 2026-10-08) is one more thing she is shown and may copy: "[Oct 8 22:35]",
# "[Mar 3 2025 15:00]". Same label as message_times.STAMP_RE. It has its own
# pattern below: in a turn, only a time she was shown goes (Fable's checkpoint, 10).
_STAMP_RE = re.compile(
    r"^\s*(?:\[system:\s*working note[^\]]*\]|\[system:\s*not delivered[^\]]*\]"
    r"|\[system:\s*only partly delivered[^\]]*\]|\(working note[^)]*\)"
    r"|\[(?:not|only partly) delivered to [^\]:]*:[^\]]*\]"
    # the owner's label in a shared room (core/agent/speakers.py) — hers to
    # read, never to write at the start of a reply
    r"|\[[^\]\n]{0,80}\u2605owner\]:)\s*",
    re.IGNORECASE,
)


# Fable's checkpoint review, 6 (2026-10-13): she copies a guest's label too
# ("[Sam]: sure"). Hers to read, never to write at the start of a reply — but
# a bracket is often her own ("[note]: ", "[1]"), so a leading label goes only
# when it names someone her payload showed her on this turn. The runner sets
# what that was as it builds each call; None outside a turn (no guest label
# is touched there).
# The same for a time label (Fable's checkpoint, 10; accepted 10-08, closed 10-13): a
# real "[Oct 8 22:35]" at the start of a reply went too. In a turn, a time label
# goes only when it is one she was shown there — a line's time, or the turn's
# own (the minute her "Current Time" names, the one she'd stamp her reply with).
# Shown times rather than a stricter shape: a copy is the payload's exact shape,
# so no shape can tell it from a real one; what she was shown can.
@dataclass(frozen=True)
class ShownThisTurn:
    speakers: frozenset[str] = frozenset()      # the names on the lines' labels
    times: frozenset[str] = frozenset()         # the time labels, "[Oct 8 22:35]"


shown_this_turn: ContextVar[ShownThisTurn | None] = ContextVar("palli_shown_this_turn", default=None)
_SPEAKER_LABEL_RE = re.compile(r"^\s*\[([^\]\n]{1,80})\]:\s*")
_TIME_LABEL_RE = re.compile(
    r"^\s*(\[(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}(?: \d{4})? \d{2}:\d{2}\])\s*",
    re.IGNORECASE,
)


def _copied_label(text: str, shown: ShownThisTurn | None) -> re.Match[str] | None:
    m = _SPEAKER_LABEL_RE.match(text) if shown is not None else None
    return m if m and m.group(1) in shown.speakers else None


def _copied_time(text: str, shown: ShownThisTurn | None) -> re.Match[str] | None:
    m = _TIME_LABEL_RE.match(text)
    return m if m and (shown is None or m.group(1) in shown.times) else None


def strip_working_note_mark(text: str) -> str:
    if not text:
        return text
    out = _RECEIPT_LINE_RE.sub("", text)   # a receipt is the system's; an imitated one never reaches them
    shown = shown_this_turn.get()
    while True:
        m = _STAMP_RE.match(out) or _copied_time(out, shown) or _copied_label(out, shown)
        if not m:
            return out
        out = out[m.end():]


def mark_working_notes_for_llm(messages: list[AgentMessage]) -> list[AgentMessage]:
    if not messages:
        return messages
    out: list[AgentMessage] = []
    changed = False
    for m in messages:
        if m.role != "assistant":
            out.append(m)
            continue
        has_tool = any(isinstance(b, ToolUseBlock) for b in m.content)
        texts = [b for b in m.content if isinstance(b, TextBlock)]
        if not has_tool or not texts:
            out.append(m)
            continue
        new_content = []
        touched = False
        for b in m.content:
            if isinstance(b, TextBlock) and b.text.strip() and not b.text.startswith(WORKING_NOTE_MARK):
                new_content.append(replace(b, text=WORKING_NOTE_MARK + b.text))
                touched = True
            else:
                new_content.append(b)
        if touched:
            changed = True
            out.append(replace(m, content=new_content))
        else:
            out.append(m)
    return out if changed else messages


def mark_undelivered_for_llm(messages: list[AgentMessage]) -> list[AgentMessage]:
    """Stamp every reply the door could not deliver (fix plan 5.10): the full
    mark when nothing reached them, the partial mark when only the start did.

    This is the sibling of mark_working_notes_for_llm, with the same contract:
    pure (the session's own messages are never mutated, so the stamp lives in
    the payload copy only), idempotent, and identity when nothing is flagged.
    Unlike working notes, it is not gated on duplex, because any send can fail.
    """
    if not messages:
        return messages
    out: list[AgentMessage] = []
    changed = False
    for m in messages:
        mark = _DELIVERY_MARKS.get(m.delivery) if m.role == "assistant" else None
        if mark is None:
            out.append(m)
            continue
        new_content = []
        touched = False
        for b in m.content:
            if (isinstance(b, TextBlock) and b.text.strip()
                    and not b.text.startswith((UNDELIVERED_MARK, PARTIAL_DELIVERY_MARK))):
                new_content.append(replace(b, text=mark + b.text))
                touched = True
            else:
                new_content.append(b)
        if touched:
            changed = True
            out.append(replace(m, content=new_content))
        else:
            out.append(m)
    return out if changed else messages


def _write_atomically(path: Path, text: str) -> None:
    """Write beside, then replace (lan_presence.save_book's pattern), so a
    crash mid-rewrite can never leave a truncated transcript. Synchronous on
    purpose: callers rely on no await between their read and this write.

    The replacement keeps the transcript's permissions (R9g). A temp file is
    born with the umask default, and os.replace would carry that over, so the
    owner's 0660 transcript would become readable by the work account. The
    mode is set while the temp file is still empty, before a byte of the
    transcript is in it. A new file keeps today's behaviour.
    """
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = None
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if mode is not None:
            os.fchmod(f.fileno(), mode)
        f.write(text)
    os.replace(tmp, path)


def _same_words(text: str) -> str:
    """Compare replies with whitespace ignored. The door sends the reply as
    chunks or beats, and her record keeps it as one text, so the spacing can
    differ while the words do not."""
    return " ".join(str(text or "").split())


# Tool receipts (2026-09-14): what the diet leaves behind for a stripped tool
# round. One bounded line per call, the system's voice (the mark says so and
# says they did not see it — §20's rule), so her history carries the machine's
# word and not only her account of it. "Breaking Bad finished clean"
# (2026-09-11) was false when said and became the only Breaking Bad fact she
# had, because the disproving output lived in the previous turn and the diet
# had thrown it away; the grounding check (DEFECT §28) sees only this run.
RECEIPT_MARK = "[tool receipt — not seen by them]"
RECEIPTS_PER_MESSAGE = 8
RECEIPT_INPUT_CHARS = 100
RECEIPT_RESULT_CHARS = 200
_RECEIPT_LINE_RE = re.compile(r"[ \t]*\[tool receipt[^\]]*\][^\n]*\n?", re.IGNORECASE)


def _squash_receipt(text: object, limit: int) -> str:
    t = " ".join(str(text if text is not None else "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _receipt_text(name: str, tool_input: object, result: str | None, is_error: bool) -> str:
    try:
        raw_in = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
    except Exception:
        raw_in = str(tool_input)
    if result is None:
        tail = "(no result recorded)"
    else:
        tail = ("FAILED: " if is_error else "") + _squash_receipt(result, RECEIPT_RESULT_CHARS)
    return f"{RECEIPT_MARK} {name}({_squash_receipt(raw_in, RECEIPT_INPUT_CHARS)}) → {tail}"


# Who the payload is for (Batch 8, task 1 — B8-1, ruling R2). A shared room
# keeps one conversation of what was said in it, by anyone; how much of HER
# WORK in the room a reader sees depends on who is reading:
#   "owner"        — everything, exactly as before this batch;
#   "collaborator" — someone the owner named: the same past turns as the
#                    owner (receipts and stamped working notes are the work
#                    she did in this room);
#   "stranger"     — everyone else: only what was said out loud.
# Anything else is read as "stranger": an unknown reader is never handed
# the work.
DIET_AUDIENCES = ("owner", "collaborator", "stranger")

# Second read F1 + F4 (2026-10-04): the tools whose input or result is
# owner-only for everyone (OWNER_DECISIONS B8-1: the house, her private pages;
# T2b-5b: the room note's words are the owner's). A collaborator gets each of
# these calls as a name-only receipt ("→ (owner-only)"): she still knows she
# did something there, and none of its words reach a guest's payload.
# send_message never sends to this conversation (it refuses it), so every
# send it recorded went somewhere else — the owner's DM, another channel —
# and that text is not this room's. speak_line's line goes to a voice
# session or, as text, to the private chat the owner used last, never to this room
# (see SPOKEN_TOOL_LINES below). A stranger gets no receipt at all, as before.
# search_what_we_said (R15, T8) reaches every chat: what it found may be the
# owner's DM or another room's lines, never this room's to share.
OWNER_PRIVATE_RECEIPT_TOOLS = frozenset({
    "room_note", "look_at_the_house", "introspect", "where_did_that_come_from",
    "note_to_self", "send_message", "speak_line", "search_what_we_said",
})
OWNER_ONLY_RECEIPT_TAIL = "→ (owner-only)"


def _is_working_note(text: str) -> bool:
    """A stamped working note, under a not-delivered stamp or not (the 5.10
    stamp is laid over the §17 one when both apply)."""
    for mark in (UNDELIVERED_MARK, PARTIAL_DELIVERY_MARK):
        if text.startswith(mark):
            text = text[len(mark):]
            break
    return text.startswith(WORKING_NOTE_MARK)


# Ruling R2a (Batch 8, task 1): what she SAID ALOUD through a tool was said
# out loud, so a stranger keeps it. These are the hands whose input goes out
# as-is to the turn's own room (core/agent/builtin_tools/duplex_hands.py,
# both through duplex.speak_mid_task → the arbiter's release leg): the tool,
# the input field that carries the words, and the start of the result the
# tool returns once the words went out. An empty or over-long input returns
# other words and a failed send is an error result, so only a recorded,
# non-error result that starts with the mark counts as said. The other hands
# put no line in this room: send_message refuses this conversation, and
# speak_line speaks in a voice session or goes to another chat.
# Final fix wave (ruling FW-4, amends R2b): said in THIS room means sent as
# text here. A chime spoken into the owner's voice session ("Said (voice)")
# was never said in the text room, so only "Said (text)" counts. ask_him's
# result never names where its question went, so it can't be told and is
# left out (fail closed): a stranger does not get it.
# Pinned against the real tools by tests/agent/test_room_audiences.py.
SPOKEN_TOOL_LINES: dict[str, tuple[str, str]] = {
    "chime_in": ("text", "Said (text)"),
}


def _spoken_line(tu: ToolUseBlock, got: tuple[str, bool] | None) -> str:
    """The words a speaking hand delivered to the room, or "" when it is
    not one or its words did not go out (no result yet, an error, a refusal)."""
    spec = SPOKEN_TOOL_LINES.get(tu.name)
    if spec is None or got is None:
        return ""
    field_name, delivered_mark = spec
    result, is_error = got
    if is_error or not result.startswith(delivered_mark):
        return ""
    raw = tu.input.get(field_name) if isinstance(tu.input, dict) else None
    return " ".join(str(raw or "").split())


def diet_for_llm(
    messages: list[AgentMessage], *, keep_from: int, audience: str = "owner",
) -> list[AgentMessage]:
    """Past turns' tool round-trips leave the LLM payload (2026-08-18).

    Measured 08-18 00:44 on the live channel: the 120-message window was
    105,616 chars — 79% tool results and calls, 15% her prior text, 6% his
    words. She answered "whatcha been up to" from forty thousand tokens,
    most of it shell output; the transcript is for CONVERSATION, and
    long-term memory lives in recall/reflect, not here.

    Messages at index >= ``keep_from`` (this run: its user message and every
    round-trip it appends) are kept verbatim — the model must replay its
    own in-flight tool results. Before that: ``tool`` messages are dropped,
    assistant messages keep only their text blocks (a tool-only assistant
    message goes entirely), user messages are untouched. Payload-only, pure
    w.r.t. its input, identity when there is nothing to strip; runs BEFORE
    window_for_llm so the window holds conversation, and before
    normalize_for_llm, which then finds nothing to repair.

    ``audience`` (Batch 8, task 1 — see DIET_AUDIENCES): "owner" and
    "collaborator" get the past turns above, receipts included. A
    "stranger" (and any unknown audience) gets only what was said out loud:
    no receipt is built, and a tool-calling assistant message keeps only
    text that is NOT a stamped working note — under duplex that text was
    never sent; with duplex off it was streamed to the room and stays — plus,
    as plain text, the lines her speaking hands delivered to the room as
    text (SPOKEN_TOOL_LINES, rulings R2a and FW-4). A message left with
    nothing said goes
    whole. Receipts are never stored, so this keys on the
    blocks, never on RECEIPT_MARK. From ``keep_from`` on, every audience
    gets the run's own messages verbatim.

    Her quiet choices (``stay_quiet`` calls and their results — her reasons)
    are her owner-only record (Batch 8, ruling T2-4a): no receipt is built
    for one except in the owner's payload. A stranger never gets receipts;
    a collaborator gets every other receipt, as before — except that the
    owner-private tools (OWNER_PRIVATE_RECEIPT_TOOLS: the room note, the
    house, her private pages, a send to another conversation) are receipts
    by name only (second read F1 + F4, 2026-10-04).
    """
    if not messages:
        return messages
    keep_from = max(0, min(int(keep_from), len(messages)))
    stranger = audience not in ("owner", "collaborator")
    # The results of the stripped rounds, by tool_use id, so each receipt can
    # carry what actually came back (None = no result was ever recorded).
    results: dict[str, tuple[str, bool]] = {}
    for m in messages[:keep_from]:
        if m.role == "tool":
            for b in m.content:
                if isinstance(b, ToolResultBlock):
                    results[b.tool_use_id] = (str(b.content or ""), bool(b.is_error))
    out: list[AgentMessage] = []
    changed = False
    for i, m in enumerate(messages):
        if i >= keep_from:
            out.append(m)
            continue
        if m.role == "tool":
            changed = True
            continue
        if m.role == "assistant":
            texts = [b for b in m.content if isinstance(b, TextBlock)]
            if len(texts) == len(m.content):
                out.append(m)
                continue
            changed = True
            if stranger:
                # In block order: her text that reached the room (streamed
                # live, not a stamped working note) and the lines her
                # speaking hands delivered (R2a), as plain text. Every other
                # tool input, every result and every working note stays out.
                said: list[TextBlock] = []
                for b in m.content:
                    if isinstance(b, TextBlock):
                        if b.text.strip() and not _is_working_note(b.text):
                            said.append(b)
                    elif isinstance(b, ToolUseBlock):
                        line = _spoken_line(b, results.get(b.id))
                        if line:
                            said.append(TextBlock(text=line))
                if said:
                    out.append(replace(m, content=said))
                continue
            calls = [b for b in m.content if isinstance(b, ToolUseBlock)
                     if audience == "owner" or b.name != STAY_QUIET]
            receipts: list[TextBlock] = []
            shown = calls[-RECEIPTS_PER_MESSAGE:] if len(calls) > RECEIPTS_PER_MESSAGE else calls
            for tu in shown:
                if audience != "owner" and tu.name in OWNER_PRIVATE_RECEIPT_TOOLS:
                    # Second read F1 + F4: the call, by name only.
                    receipts.append(TextBlock(
                        text=f"{RECEIPT_MARK} {tu.name} {OWNER_ONLY_RECEIPT_TAIL}"))
                    continue
                got = results.get(tu.id)
                receipts.append(TextBlock(text=_receipt_text(
                    tu.name, tu.input, got[0] if got else None, got[1] if got else False)))
            if len(calls) > RECEIPTS_PER_MESSAGE:
                extra = len(calls) - RECEIPTS_PER_MESSAGE
                receipts[-1] = TextBlock(text=receipts[-1].text + f" (+{extra} more call{'s' if extra != 1 else ''} before these)")
            kept = texts + receipts
            if kept:
                out.append(replace(m, content=kept))
            continue
        out.append(m)
    return out if changed else messages


def window_for_llm(messages: list[AgentMessage], max_messages: int, *, step: int = 1) -> list[AgentMessage]:
    """Bounded view of session history for the LLM payload.

    Sessions are append-only archives — a busy channel grows without limit
    (2026-06-11: a live channel reached 471 messages / ~85k prompt tokens,
    all replayed on every turn). This returns the most recent
    ``max_messages``, snapped forward so the window STARTS on a ``user``
    message: an OpenAI-style payload whose first message is a dangling
    assistant reply — or a tool result whose matching tool_use was cut off —
    is rejected by the provider. Tool results always directly follow their
    assistant tool_call in session order, so starting on a user message
    guarantees no tool round-trip is ever split.

    ``step > 1`` floors the start to a multiple of ``step``, so the window
    keeps between ``max_messages`` and ``max_messages + step - 1`` messages
    (before the snap); ``step <= 1`` is the plain one-message slide.

    ``max_messages <= 0`` disables windowing. Payload-only: the session list
    itself is never mutated and the on-disk archive keeps everything.
    """
    if max_messages <= 0 or len(messages) <= max_messages:
        return messages
    start = len(messages) - max_messages
    if step > 1:
        # Move the window in steps so consecutive turns share a byte-identical
        # history prefix — the provider caches that prefix (Batch 2, 2026-09-27).
        start -= start % step
    snapped = start
    while snapped < len(messages) and messages[snapped].role != "user":
        snapped += 1
    if snapped < len(messages):
        return messages[snapped:]
    # No user message inside the naive window — e.g. the tail is one long
    # tool-call chain. The old fallback returned a plain messages[start:] slice,
    # which can BEGIN on a tool result whose matching tool_use was cut off — the
    # provider 400s on that orphan. Walk BACKWARD instead to the nearest user
    # message before the window and anchor there: a few extra tokens beat a
    # dropped turn.
    back = start - 1
    while back >= 0 and messages[back].role != "user":
        back -= 1
    if back >= 0:
        return messages[back:]
    # Degenerate: no user message anywhere in the session. Drop any leading
    # tool-result messages so the payload can't start mid-round-trip.
    trimmed = start
    while trimmed < len(messages) and _is_tool_result_message(messages[trimmed]):
        trimmed += 1
    return messages[trimmed:] if trimmed < len(messages) else messages[start:]


# --- Transcript grammar law (2026-07-26) -----------------------------------
# Spec: docs/superpowers/specs/2026-07-26-transcript-validity-boundary-design.md
# Applied to the windowed history at the runner's single payload-assembly
# point. Providers reject a payload WHOLE when tool calls and results are
# mispaired (the 2026-07-25 poison pill), so repairs here are the last line
# of defense for every cause at once: barge-in cancels, process death,
# corruption-truncated files, and the runner's own synthetic injections.

_INTERRUPTED_RESULT_TEXT = "[interrupted: no result was recorded for this tool call]"
_SYSTEM_OBSERVATION_PREFIX = "[system observation] "


def _message_is_empty(m: AgentMessage) -> bool:
    for b in m.content:
        if isinstance(b, (ToolUseBlock, ToolResultBlock)):
            return False
        if isinstance(b, TextBlock) and b.text:
            return False
    return True


def _dedupe_tool_uses(m: AgentMessage) -> AgentMessage:
    """R4: drop empty-id and duplicate-id tool_use blocks (first id wins).
    An unanswerable call cannot be paired, and duplicate ids make pairing
    ambiguous — both are provider 400s."""
    if m.role != "assistant":
        return m
    seen: set[str] = set()
    kept: list[ContentBlock] = []
    for b in m.content:
        if isinstance(b, ToolUseBlock):
            if not b.id or b.id in seen:
                continue
            seen.add(b.id)
        kept.append(b)
    if len(kept) == len(m.content):
        return m
    return AgentMessage(role=m.role, content=kept,
                        timestamp=m.timestamp, reasoning=m.reasoning, speaker=m.speaker)


def _emit_downgrading_orphans(
    m: AgentMessage, out: list[AgentMessage], counts: dict[str, int],
) -> None:
    """R3: a tool_result no assistant call claims becomes user-visible text.
    Keeps the signal (e.g. the loop-detector's CRITICAL message) without the
    unpairable tool_call_id — the openai_compat twin of the downgrade the
    anthropic converter already does (llm_client.py:301-338)."""
    orphans = [b for b in m.content if isinstance(b, ToolResultBlock)]
    if not orphans:
        out.append(m)
        return
    kept = [b for b in m.content if not isinstance(b, ToolResultBlock)]
    if kept:
        out.append(AgentMessage(role=m.role, content=kept,
                                timestamp=m.timestamp, reasoning=m.reasoning, speaker=m.speaker))
    for b in orphans:
        counts["orphans_downgraded"] += 1
        out.append(AgentMessage(
            role="user",
            content=[TextBlock(text=f"{_SYSTEM_OBSERVATION_PREFIX}{b.content}")],
            timestamp=m.timestamp,
        ))


def normalize_for_llm(
    messages: list[AgentMessage], *, log_context: str = "",
) -> list[AgentMessage]:
    """Repair a message list so it cannot violate provider transcript grammar.

    Pure w.r.t. its input (never mutated), payload-only (never touches disk),
    idempotent, total, and — the no-regression guarantee — identity on already
    -valid input. Rules R1–R4 and invariants I1–I5 are specified in
    docs/superpowers/specs/2026-07-26-transcript-validity-boundary-design.md.
    """
    counts = {"empties_dropped": 0, "call_blocks_dropped": 0,
              "results_synthesized": 0, "orphans_downgraded": 0}

    msgs: list[AgentMessage] = []
    for m in messages:
        m2 = _dedupe_tool_uses(m)                                  # R4
        if m2 is not m:
            counts["call_blocks_dropped"] += 1
        if _message_is_empty(m2):                                  # R1
            counts["empties_dropped"] += 1
            continue
        msgs.append(m2)

    out: list[AgentMessage] = []
    i, n = 0, len(msgs)
    while i < n:
        m = msgs[i]
        ids = ([b.id for b in m.content if isinstance(b, ToolUseBlock)]
               if m.role == "assistant" else [])
        if not ids:
            _emit_downgrading_orphans(m, out, counts)              # R3
            i += 1
            continue

        # R2: this call's span runs to the next assistant message (or the
        # end). Results legitimately live only here; anything of theirs
        # found later belongs to a later call by id anyway.
        j = i + 1
        while j < n and msgs[j].role != "assistant":
            j += 1
        span = msgs[i + 1:j]
        ids_set = set(ids)

        claimed: dict[str, ToolResultBlock] = {}
        for sm in span:
            for b in sm.content:
                if (isinstance(b, ToolResultBlock)
                        and b.tool_use_id in ids_set
                        and b.tool_use_id not in claimed):
                    claimed[b.tool_use_id] = b
        result_blocks: list[ContentBlock] = []
        for tid in ids:
            hit = claimed.get(tid)
            if hit is None:
                counts["results_synthesized"] += 1
                hit = ToolResultBlock(tool_use_id=tid,
                                      content=_INTERRUPTED_RESULT_TEXT,
                                      is_error=True)
            result_blocks.append(hit)

        out.append(m)
        first = span[0] if span else None
        if (first is not None and first.role == "tool"
                and list(first.content) == result_blocks):
            out.append(first)          # already exactly right: identity path
            rest = span[1:]
        else:
            out.append(AgentMessage(role="tool", content=result_blocks))
            rest = span
        for sm in rest:
            kept = [b for b in sm.content
                    if not (isinstance(b, ToolResultBlock)
                            and b.tool_use_id in ids_set)]
            if not kept:
                continue
            sm2 = (sm if len(kept) == len(sm.content)
                   else AgentMessage(role=sm.role, content=kept,
                                     timestamp=sm.timestamp,
                                     reasoning=sm.reasoning, speaker=sm.speaker))
            _emit_downgrading_orphans(sm2, out, counts)
        i = j

    if any(counts.values()):
        logger.warning(
            "normalize_for_llm[%s]: repaired payload: %s", log_context,
            " ".join(f"{k}={v}" for k, v in counts.items() if v),
        )
    return out


_INTERIM_FOLD = ("{user}\n\n[you already answered this while you worked — \"{reply}\" — "
                 "carry on with the task]")


def fold_interims_for_thinking(messages: list[AgentMessage]) -> list[AgentMessage]:
    """A drained interim never sits bare inside a tool round (2026-08-29
    20:09, DEFECT_RECORD §13).

    The boundary drain writes his mid-run words and her judged (or sidecar)
    reply as a user row + a bare assistant row — true on disk. But a
    thinking-mode provider requires reasoning_content on every assistant
    message in the CURRENT tool-calling round (everything after the last
    user message), and a judged interim never has one: when the run then
    continued with tool calls the payload was refused whole. Grammar, not
    meaning, payload-only: each text-only assistant message that sits
    after the last user-message-that-opened-the-round is folded into the
    user message it answered, as an annotation that also tells the model
    to carry on. Identity when there is nothing to fold; the input is
    never mutated; nothing here reaches disk.
    """
    if not messages:
        return messages
    # The round the provider checks starts at the LAST user message that is
    # followed by tool traffic or nothing; earlier exchanges are previous
    # rounds. Walk backward: a text-only assistant row directly after a user
    # row, with no user row after the pair that ends the round cleanly, is
    # an interim inside the round.
    out: list[AgentMessage] = list(messages)
    changed = False
    i = len(out) - 1
    # Find the last index that is a user message and check what follows it
    # for bare assistant rows; repeat for stacked interims (user, assistant,
    # user, assistant, tool...).
    while i >= 1:
        m = out[i]
        if m.role == "assistant" and not any(isinstance(b, ToolUseBlock) for b in m.content) \
                and out[i - 1].role == "user" and _round_continues_after(out, i):
            reply = " ".join(" ".join(b.text for b in m.content if isinstance(b, TextBlock)).split())
            user_text = " ".join(b.text for b in out[i - 1].content if isinstance(b, TextBlock)).strip()
            folded = AgentMessage(role="user", content=[TextBlock(text=_INTERIM_FOLD.format(user=user_text, reply=reply))],
                                  timestamp=out[i - 1].timestamp, speaker=out[i - 1].speaker)
            out[i - 1] = folded
            del out[i]
            changed = True
            i -= 1
            continue
        i -= 1
    return out if changed else messages


def _round_continues_after(messages: list[AgentMessage], i: int) -> bool:
    """True when the assistant row at ``i`` is inside the current round:
    the messages after it contain tool traffic (a tool-calling assistant
    row or a tool result) before any user row — or there is nothing after
    it at all and the run is mid-tool (a tool row precedes the pair)."""
    for m in messages[i + 1:]:
        if m.role == "user":
            return False                       # a later user row closes this round: previous round
        if m.role == "tool" or (m.role == "assistant" and any(isinstance(b, ToolUseBlock) for b in m.content)):
            return True
    # nothing but text after it (or nothing at all): inside a round only if
    # a tool round-trip precedes the pair in this same stretch
    for m in reversed(messages[:i - 1]):
        if m.role == "user":
            return False
        if m.role == "tool":
            return True
    return False


def ensure_user_tail(
    messages: list[AgentMessage], *, fallback_text: str = "", continuation: str = "",
) -> list[AgentMessage]:
    """The payload ends on HIM (2026-08-28, the thinking-mode 400).

    A turn dispatched for an exchange already on the record — the steering
    deadline fallback, the post-run sweep, a full_turn's real answer — reads
    a transcript whose last row is her interim reply. A thinking-mode
    provider refuses to continue an assistant message it has no
    reasoning_content for (nothing on disk carries reasoning), so such
    payloads 400'd every time, silently. Grammar, not meaning: when the tail
    is an assistant message, re-present his LAST words as a payload-only
    user message (his turn text when no user row exists at all). Identity
    on any payload that already ends on him or on a tool result; the input
    is never mutated; nothing here reaches disk.
    """
    if not messages or messages[-1].role != "assistant":
        return messages
    if str(continuation or "").strip():
        # Phase B (correction 4): mid-run, after a boundary wrote his side
        # words and her answer to them, the tail says "carry on" — re-
        # presenting his words would have her answer him twice.
        return list(messages) + [AgentMessage(role="user", content=[TextBlock(text=str(continuation).strip())])]
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)
    if last_user is not None:
        # His words keep their own time (2026-10-08): the payload stamps it.
        tail = AgentMessage(role="user", content=list(last_user.content),
                            timestamp=last_user.timestamp, speaker=last_user.speaker)
    else:
        text = str(fallback_text or "").strip()
        if not text:
            return messages
        tail = AgentMessage(role="user", content=[TextBlock(text=text)])
    return list(messages) + [tail]


# ── A parse kept between opens (2026-10-12) ───────────────────────────────
# A reply opens its conversation's file more than once (the room's rows,
# the run's own session), and every open parsed the whole file: 0.44 s for
# a 13,313-row channel (measured Oct 12), on the event loop, before each
# reply. Transcripts change only by appends (Session.append) or by whole
# replacement (_write_atomically; the corrupt-tail rewrite truncates). So an
# open that finds the same file, no shorter, with the end of what was
# parsed still in place, reuses the parsed rows and parses only the lines
# appended since. Anything else (another file, a shorter one, a changed
# end, a same-size file touched since, a line still being written, a line
# that will not parse) takes the full read, which keeps today's behaviour
# exactly, the truncate-and-rewrite included.
#
# Two things this rests on (Fable's review, 10-12). The rows are shared
# between opens: never edit one in place, make a new one with
# dataclasses.replace, as every transform does today
# (tests/agent/test_session_parse_reuse.py checks the payload passes).
# And only this module writes a transcript, by append or by replacement
# (scripts/scrub_memory_secrets.py replaces too). A hand edit of a live
# transcript is read whole only once its size or its end changes: restart
# PaLLi after one.
_PARSE_CACHE_FILES = 8      # the conversations in use; one entry is one file's rows
_PARSE_TAIL_BYTES = 256     # the end of what was parsed, checked before any reuse
# Smaller files parse in a few milliseconds and are not kept, so one-off
# keys (a sidecar's, a reach-out's) don't push the big channels out.
_PARSE_CACHE_MIN_BYTES = 256 * 1024


@dataclass
class _ParsedFile:
    dev: int
    ino: int
    size: int            # bytes parsed, always through the end of a line
    mtime_ns: int
    tail: bytes          # the last _PARSE_TAIL_BYTES of those bytes
    messages: list       # the rows (shared); every caller gets its own list of them


_parsed_files: "OrderedDict[str, _ParsedFile]" = OrderedDict()
_parsed_lock = threading.Lock()


def _parse_lines(text: str) -> tuple[list[AgentMessage], int | None, Exception | None]:
    """Transcript lines to rows. On a line that will not parse: the rows
    before it, its 0-based number and the error; otherwise None, None."""
    msgs: list[AgentMessage] = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(_msg_from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            return msgs, i, exc
    return msgs, None, None


def _remember_parse(path: Path, raw: bytes, msgs: list[AgentMessage],
                    st: os.stat_result) -> None:
    """Keep a whole-file parse for the next open. Not when the file is small
    or its last line has no end yet: the next open reads it whole again, and
    an older entry for the file goes (Fable's final read, 10-12: a channel
    reset to a small file left the big one's rows behind)."""
    key = str(path)
    if len(raw) < _PARSE_CACHE_MIN_BYTES or (raw and not raw.endswith(b"\n")):
        _forget_parse(path)
        return
    with _parsed_lock:
        _parsed_files[key] = _ParsedFile(
            dev=st.st_dev, ino=st.st_ino, size=len(raw), mtime_ns=st.st_mtime_ns,
            tail=raw[-_PARSE_TAIL_BYTES:], messages=list(msgs))
        _parsed_files.move_to_end(key)
        while len(_parsed_files) > _PARSE_CACHE_FILES:
            _parsed_files.popitem(last=False)


def _forget_parse(path: Path) -> None:
    with _parsed_lock:
        _parsed_files.pop(str(path), None)


def _reuse_parse(path: Path) -> list[AgentMessage] | None:
    """The rows of an earlier parse plus any lines appended since, or None
    when only a full read can say."""
    key = str(path)
    with _parsed_lock:
        entry = _parsed_files.get(key)
        if entry is None:
            return None
        start = entry.size - len(entry.tail)
        try:
            with path.open("rb") as f:
                st = os.fstat(f.fileno())
                if ((st.st_dev, st.st_ino) != (entry.dev, entry.ino)
                        or st.st_size < entry.size):
                    return None
                f.seek(start)
                data = f.read(st.st_size - start)
        except OSError:
            return None
        if data[:len(entry.tail)] != entry.tail:
            return None
        new = data[len(entry.tail):]
        if not new:
            if st.st_mtime_ns != entry.mtime_ns:
                return None          # written in place since: read it whole
            _parsed_files.move_to_end(key)
            return list(entry.messages)
        if not new.endswith(b"\n"):
            return None              # a line still being written
        try:
            appended, bad, _exc = _parse_lines(new.decode("utf-8"))
        except UnicodeDecodeError:
            return None
        if bad is not None:
            return None              # the full read truncates and rewrites
        entry.messages.extend(appended)
        entry.size += len(new)
        entry.mtime_ns = st.st_mtime_ns
        entry.tail = (entry.tail + new)[-_PARSE_TAIL_BYTES:]
        _parsed_files.move_to_end(key)
        return list(entry.messages)


class Session:
    def __init__(self, session_key: str, path: Path):
        self.session_key = session_key
        self.path = path
        self.messages: list[AgentMessage] = []
        self._lock = asyncio.Lock()

    @classmethod
    async def open_or_create(cls, session_key: str, *, root: Path) -> "Session":
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{_sanitize_session_key(session_key)}.jsonl"
        sess = cls(session_key, path)
        if path.exists():
            sess.messages = sess._load()
        return sess

    def _load(self) -> list[AgentMessage]:
        reused = _reuse_parse(self.path)
        if reused is not None:
            return reused
        try:
            with self.path.open("rb") as f:
                st = os.fstat(f.fileno())
                raw = f.read()
        except OSError as exc:
            logger.warning("session %s: read failed: %s", self.session_key, exc)
            return []
        msgs, bad, exc = _parse_lines(raw.decode("utf-8"))
        corruption_detected = bad is not None
        if corruption_detected:
            logger.warning(
                "session %s: corrupt line %d, truncating: %s",
                self.session_key, bad + 1, exc,
            )
        else:
            _remember_parse(self.path, raw, msgs, st)

        if corruption_detected:
            # Rewrite the file with only the good messages so the corrupt tail
            # doesn't keep reappearing on every load.
            try:
                rewritten = "".join(_msg_to_jsonl(m) + "\n" for m in msgs)
                self.path.write_text(rewritten, encoding="utf-8")
            except OSError as exc:
                logger.warning("session %s: rewrite-after-truncate failed: %s", self.session_key, exc)
        return msgs

    async def append(self, msg: AgentMessage) -> None:
        msg = _redact_message(msg)   # item 7c: his denylist, before memory and disk
        async with self._lock:
            self.messages.append(msg)
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(_msg_to_jsonl(msg) + "\n")
            except OSError as exc:
                logger.warning("session %s: append failed: %s", self.session_key, exc)

    async def enrich_last_user_placeholder(self, new_text: str) -> bool:
        """Upgrade the most-recent '[image attached]' or '[file attached]'
        user entry to new_text, in place on disk. Re-reads the file FRESH
        under the lock (another Session instance on this same channel —
        record_user_fragment — may have appended a barge-in message since
        this instance loaded; rewriting from the stale in-memory snapshot
        would erase it). The read-modify-write is fully synchronous (no
        await between read and write), so under asyncio's single thread it
        is atomic w.r.t. any concurrent append."""
        async with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError:
                return False
            fresh: list[AgentMessage] = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    fresh.append(_msg_from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            target = None
            for i in range(len(fresh) - 1, -1, -1):
                m = fresh[i]
                if (m.role == "user" and len(m.content) == 1
                        and isinstance(m.content[0], TextBlock)
                        and m.content[0].text.strip() in ("[image attached]", "[file attached]")):
                    target = i
                    break
            if target is None:
                return False
            fresh[target] = AgentMessage(
                role="user", content=[TextBlock(text=new_text)],
                timestamp=fresh[target].timestamp, speaker=fresh[target].speaker,
            )
            try:
                _write_atomically(self.path, "".join(_msg_to_jsonl(m) + "\n" for m in fresh))
            except OSError as exc:
                logger.warning("session %s: enrich rewrite failed: %s", self.session_key, exc)
                return False
            self.messages = fresh
            return True

    async def mark_undelivered(self, failed_text: str, *, partial: bool = False) -> bool:
        """Flag the reply the door could not deliver (fix plan 5.10, R9a/R9b/R9d/R9e).

        ``partial`` means the start of the reply reached them and the rest did
        not; otherwise nothing did. The flag is AgentMessage.delivery.

        The target is the LAST assistant message of the CURRENT exchange (after
        the last user message) whose words equal ``failed_text``. Whitespace
        is ignored, and the probe is also redacted the way append redacted
        what it stored. An older identical line was delivered in its own
        exchange and is never stamped. With no exact match this returns False
        and leaves the file untouched. That happens with a sidecar's reply,
        which is not in this transcript until the parent's drain writes it,
        and guessing would stamp a reply that DID land.

        Same serialisation as enrich_last_user_placeholder, the other
        after-the-fact edit: under the lock, re-read the file FRESH and
        rewrite it with no await between the read and the write. Under
        asyncio's single thread that is atomic w.r.t. any other Session
        instance's append on this key (the runner, record_user_fragment), and
        a line appended since this instance loaded is kept. The rewrite goes
        beside-then-replace, so a crash cannot truncate the transcript. Only
        the flag changes; her words stay verbatim.
        """
        state = "partial" if partial else "none"
        probes = {_same_words(failed_text)}
        try:
            from core.secrets_redaction import redact
            probes.add(_same_words(redact(str(failed_text or ""))))
        except Exception:
            pass
        probes.discard("")
        if not probes:
            return False
        async with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError:
                return False
            fresh: list[AgentMessage] = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    fresh.append(_msg_from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            target = None
            for i in range(len(fresh) - 1, -1, -1):
                m = fresh[i]
                if m.role == "user":
                    break           # the current exchange ends here, looking back
                if m.role == "assistant" and _same_words(
                        "".join(b.text for b in m.content if isinstance(b, TextBlock))) in probes:
                    target = i
                    break
            if target is None:
                return False
            if fresh[target].delivery != state:
                fresh[target] = replace(fresh[target], delivery=state)
                try:
                    _write_atomically(self.path, "".join(_msg_to_jsonl(m) + "\n" for m in fresh))
                except OSError as exc:
                    logger.warning("session %s: delivery-flag rewrite failed: %s", self.session_key, exc)
                    return False
            self.messages = fresh
            return True
