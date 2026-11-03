"""The owner's room notes (Batch 8, task 2b — owner O1, ruling T2b-5).

INTERIM — the owner revisits this after the batches (why the old
STANDING_ORDERS.md stopped being read, the limits of a room note, and a third
solution the owner will bring). It is built to be easy to replace: this one
store, one owner-only tool (``core/agent/builtin_tools/room_note.py``) and one
line, read in two places — the room's first read
(``bots/common/room_pass.py``) and her reply call in that conversation
(``core/agent/runner.py``) — only while ``companion.she_decides`` is on.

What it is: a standing instruction from the owner for one room ("stay quiet in
here until tomorrow"), with an end time. While it is active, her decisions in
that room see it as one line — the owner's words, when it was set, until when,
and that the only exception is a real safety reason. The words stay the
owner's (ruling T2b-5b): they are in the room's first reads and the owner's
own reply calls; a guest's reply call there sees only that the owner has a
standing note here, until when — neutral about what it asks (final fix wave,
ruling FW-8). She still decides; code
never silences her. When it runs out (a time limit is code) or the owner lifts
it, it is gone. If she answers in that room while it stands, her record of
quiet choices notes it (``spoke_during_note``).

The file is ``data/room_notes.json`` (gitignored; ``PALLI_ROOM_NOTES_PATH``
overrides it, which is how the test suite stays off the live file), keyed by
conversation key (``core.session_keys``) →
``{"words", "until", "set_at"}``, times in local ISO 8601. Writes are atomic
(a temporary file, then a rename). A file that cannot be read is no notes —
logged, and never overwritten.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
WORDS_MAX_CHARS = 300


class RoomNoteStoreError(RuntimeError):
    """The notes file exists but cannot be read, so it is not rewritten."""


def store_path() -> Path:
    raw = os.environ.get("PALLI_ROOM_NOTES_PATH")
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "room_notes.json"


def _load(*, strict: bool) -> dict[str, dict[str, Any]]:
    path = store_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        if strict:
            raise RoomNoteStoreError(f"can't read {path.name}: {exc}") from exc
        logger.warning("room_notes: could not read %s (%s)", path.name, exc)
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError as exc:
        if strict:
            raise RoomNoteStoreError(f"{path.name} is not readable notes: {exc}") from exc
        logger.warning("room_notes: %s is unreadable; reading it as no notes", path.name)
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _save(notes: dict[str, dict[str, Any]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(notes, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _until(note: dict[str, Any]) -> datetime | None:
    """The note's end as a local, naive time (a hand-edited file may carry
    an offset); None when it cannot be read."""
    try:
        until = datetime.fromisoformat(str(note.get("until") or ""))
    except ValueError:
        return None
    if until.tzinfo is not None:
        until = until.astimezone().replace(tzinfo=None)
    return until


def _is_active(note: dict[str, Any], now: datetime) -> bool:
    until = _until(note)
    return until is not None and until > now and bool(str(note.get("words") or "").strip())


def set_note(key: str, words: str, until: datetime, *, now: datetime | None = None,
             set_by: dict[str, Any] | None = None, excerpt: str = "") -> dict[str, Any]:
    """Save the owner's note for one room, replacing any earlier one there.
    ValueError for no words or an end that has already passed.

    ``set_by`` and ``excerpt`` (second read F9, 2026-10-04): who set it and
    the stretch of their own message that asked for it, kept in the note
    when given — so the note can always be traced to the owner's words."""
    now = now or datetime.now()
    if until.tzinfo is not None:
        until = until.astimezone().replace(tzinfo=None)
    text = " ".join(str(words or "").split())[:WORDS_MAX_CHARS]
    if not text:
        raise ValueError("a room note needs the owner's words")
    if until <= now:
        raise ValueError("a room note's end time has to be in the future")
    notes = {k: v for k, v in _load(strict=True).items() if _is_active(v, now)}
    note: dict[str, Any] = {"words": text, "until": until.replace(microsecond=0).isoformat(),
                            "set_at": now.replace(microsecond=0).isoformat()}
    if set_by:
        note["set_by"] = dict(set_by)
    if str(excerpt or "").strip():
        note["excerpt"] = " ".join(str(excerpt).split())[:WORDS_MAX_CHARS]
    notes[str(key)] = note
    _save(notes)
    return dict(note)


def lift(key: str, *, now: datetime | None = None) -> bool:
    """Lift the note for one room. True when there was an active one."""
    now = now or datetime.now()
    notes = _load(strict=True)
    had = str(key) in notes and _is_active(notes[str(key)], now)
    if str(key) in notes:
        notes.pop(str(key))
        _save({k: v for k, v in notes.items() if _is_active(v, now)})
    return had


def active(key: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The active note for this room, or None (none, run out, or unreadable)."""
    note = _load(strict=False).get(str(key))
    if note is None or not _is_active(note, now or datetime.now()):
        return None
    return dict(note)


def list_active(*, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Every active note, by room."""
    now = now or datetime.now()
    return {k: dict(v) for k, v in _load(strict=False).items() if _is_active(v, now)}


def _when(iso: Any) -> str:
    try:
        moment = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso or "")
    return f"{moment:%b} {moment.day}, {moment:%H:%M}"


def note_line(key: str, *, words: bool = True, now: datetime | None = None) -> str:
    """The one line her decisions in this room see while its note stands;
    "" when there is none.

    ``words`` (ruling T2b-5b — the owner's words stay the owner's): the
    note's words appear only in the room's first reads (a decision; what it
    writes goes to the owner-only record) and in the owner's own reply calls.
    A guest's reply call gets ``words=False``: only that the owner has a
    standing note here, until when, and the one exception — neutral about
    what the note asks, so a note that asks for something other than quiet
    never reads as a silence (final fix wave, ruling FW-8)."""
    note = active(key, now=now)
    if note is None:
        return ""
    if not words:
        # Reworded 10-13 on the owner's word (Fable's text): "a silence it asks
        # for" still named a silence; this says nothing of what the note asks.
        return (f"The owner has a standing note for this room until {_when(note.get('until'))}; "
                "its words are the owner's, not shown here. Whatever it asks, the one thing "
                "that comes before it is a real safety reason (someone says they are in danger).")
    return (f"The owner's note for this room (set {_when(note.get('set_at'))}, until "
            f"{_when(note.get('until'))}): \"{note.get('words')}\". It stands until then "
            "unless the owner lifts it; the only exception to a silence it asks for is a "
            "real safety reason (someone says they are in danger).")


def after_reply(*, door: str, conversation: str, who_id: Any, who_name: Any, owner: bool,
                reply_text: str, now: datetime | None = None) -> bool:
    """She answered in a room whose note stands: her record of quiet choices
    notes it (source ``spoke_during_note``, her reply's first 120 characters).
    False when no note stands there. Never raises."""
    try:
        note = active(conversation, now=now)
        if note is None or not str(reply_text or "").strip():
            return False
        from core import quiet_record

        return quiet_record.record(
            door=door, conversation=conversation, who_id=who_id, who_name=who_name,
            owner=owner, text=reply_text, source="spoke_during_note", now=now,
            reason=f"answered while the owner's room note stood (\"{note.get('words')}\")",
        )
    except Exception as exc:  # never let the record break the turn it records
        logger.warning("room_notes: could not note a reply during a room note (%s)",
                       type(exc).__name__)
        return False
