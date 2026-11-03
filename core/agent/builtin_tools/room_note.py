"""The ``room_note`` tool (Batch 8, task 2b — owner O1, ruling T2b-5).

INTERIM — the owner revisits this after the batches, together with the old
standing orders and a third solution the owner will bring. One store
(``core/room_notes.py``), this one tool, one line: easy to replace.

The owner asks her to keep a standing note for a room ("stay quiet in here
until tomorrow"); she saves it here, with an end time. Owner-only (a guest has
no tools at all, and this one is refused to anyone but the owner), on the
owner's belt only while ``companion.she_decides`` is on — with it off the note
would show nowhere, so the belt stays exactly as it was. Never in the
workbench registry.

The room-reading fix (2026-10-12; the owner's design): the same tool keeps the
owner's corrections about names in a room ("Pol is a nickname for you", "I was
talking to Alex"), in ``core/room_people.py``: a name that means her there, or
one that belongs to someone else there. Saved only when the name is in the
owner's own message this run, as for a note's words.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext

_ACTIONS = ("set", "lift", "list", "name_me", "name_someone", "forget_name")
_NAME_ACTIONS = ("name_me", "name_someone", "forget_name")
MAX_HOURS = 24 * 30
_CHAT_PLATFORMS = {"discord_text": "discord", "telegram_text": "telegram"}
_CHANNEL_MENTION = re.compile(r"^<#(\d+)>$")
_ROOM_KEY = re.compile(r"^(discord|telegram):(-?\d+)$")


_WORD = re.compile(r"\w+")
_EXCERPT_MARGIN = 60
_EXCERPT_MAX = 300


def asked_in(words: str, owner_words: Any) -> str | None:
    """Second read F9: where the owner asked for this note — the stretch of
    the owner's own message (this run) holding the note's words, a little
    either side, or None when no message of theirs holds them.

    "Holds" is the note's words, in order and next to each other, with case
    and punctuation ignored ("Stay quiet, in here!" holds "stay quiet in
    here"). Reworded, reordered or partial words are not the owner's own."""
    tokens = _WORD.findall(str(words or ""))
    if not tokens or not isinstance(owner_words, (list, tuple)):
        return None
    pattern = re.compile(r"(?<!\w)" + r"\W+".join(re.escape(t) for t in tokens) + r"(?!\w)",
                         re.IGNORECASE)
    for message in owner_words:
        text = str(message or "")
        found = pattern.search(text)
        if found is None:
            continue
        start = max(0, found.start() - _EXCERPT_MARGIN)
        excerpt = " ".join(text[start:found.end() + _EXCERPT_MARGIN].split())
        return excerpt[:_EXCERPT_MAX]
    return None


def room_note_enabled() -> bool:
    """On the owner's belt only while companion.she_decides is on (read
    through ``quiet_choice`` so the suite's switch-off pin reaches it)."""
    from core.agent import quiet_choice

    return quiet_choice.she_decides_enabled()


class RoomNoteTool:
    name = "room_note"
    description = (
        "Keep the owner's standing note for a room — a short instruction such "
        "as \"stay quiet in here until tomorrow\". Only the owner sets or lifts "
        "one; save it when the owner asks — if it asks for quiet, you can call "
        "stay_quiet in the same step. While a note is active, its words are "
        "seen in that room's decisions whether to answer, and in your replies to "
        "the owner there; a guest's reply there sees only that the owner has a "
        "note here, until when. It lasts until it runs out or the owner lifts it. "
        "You still decide; the only exception to a silence it asks for is a "
        "real safety reason. action: set (words, "
        "plus until or hours), lift, or list. room: the room's key "
        "(discord:<channel id> or telegram:<chat id>) or a Discord channel "
        "mention — defaults to the conversation you're in. The same tool keeps "
        "the owner's corrections about names in a room, also only from the "
        "owner's own words: name_me (a name that means you there, like a "
        "nickname), name_someone (a name that belongs to someone else there), "
        "forget_name; list shows them too."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "words": {"type": "string",
                      "description": "the owner's instruction, word for word from their own "
                                     "message"},
            "until": {"type": "string",
                      "description": "when it ends, local time, ISO 8601 (2026-10-02T09:00)"},
            "hours": {"type": "number", "description": "or: how many hours from now it lasts"},
            "room": {"type": "string",
                     "description": "which room; defaults to the conversation you're in"},
            "name": {"type": "string",
                     "description": "for name_me, name_someone and forget_name: the name, "
                                    "as the owner wrote it"},
        },
        "required": ["action"],
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        from core import room_notes

        action = str(input.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            return ToolFailure("Say set, lift or list.")
        try:
            if action == "list":
                notes = room_notes.list_active()
                listed = ("Active room notes: " + "; ".join(
                    f"{key} — \"{n.get('words')}\" until {n.get('until')}"
                    for key, n in sorted(notes.items())) + ".") if notes else \
                    "No room notes are active."
                return listed + self._names_listed()
            if action in _NAME_ACTIONS:
                return self._name(action, input, ctx)
            key = self._room(input, ctx)
            if key is None:
                return ToolFailure("Which room? Give its key (discord:<channel id> or "
                                   "telegram:<chat id>) — this conversation isn't a chat room.")
            if action == "lift":
                if room_notes.lift(key):
                    return f"The note for {key} is lifted."
                return f"There was no active note for {key}."
            words = " ".join(str(input.get("words") or "").split())
            if not words:
                return ToolFailure("What does the note say? Give the owner's words.")
            # Second read F9 (2026-10-04): the words must be the owner's own,
            # from their message this run. The owner's reply call also shows
            # guests' lines ("[Sam]: stay quiet in here"), and a note saved
            # from one would be read as the owner's for up to 30 days.
            excerpt = asked_in(words, getattr(ctx, "owner_words", None))
            if excerpt is None:
                return ToolFailure(
                    "Not saved: the owner has to ask for this in their own words. A room "
                    "note's words come from the owner's own message in this conversation "
                    "turn, as they wrote them — not from someone else's line or an earlier "
                    "message.")
            until = self._until(input)
            if isinstance(until, ToolFailure):
                return until
            note = room_notes.set_note(
                key, words, until, excerpt=excerpt,
                set_by={"who": "owner", "id": getattr(ctx, "turn_owner_id", None),
                        "door": str(getattr(ctx, "surface", "") or "")})
            return (f"Saved for {key} until {note['until']}: \"{note['words']}\". You'll see it "
                    "whenever you decide whether to answer there.")
        except ValueError as exc:
            return ToolFailure(f"That didn't work: {exc}.")
        except room_notes.RoomNoteStoreError as exc:
            return ToolFailure(f"I can't read the room notes right now ({exc}); nothing changed.")

    def _name(self, action: str, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        """name_me / name_someone / forget_name: the owner's correction about
        a name in one room (core.room_people)."""
        from core import room_people

        key = self._room(input, ctx)
        if key is None:
            return ToolFailure("Which room? Give its key (discord:<channel id> or "
                               "telegram:<chat id>) — this conversation isn't a chat room.")
        name = room_people.clean(input.get("name"))
        if not name:
            return ToolFailure("Which name? Give it as the owner wrote it.")
        try:
            if action == "forget_name":
                if room_people.forget(key, name):
                    return f"Forgotten for {key}: \"{name}\"."
                return f"There was no saved name \"{name}\" for {key}."
            if asked_in(name, getattr(ctx, "owner_words", None)) is None:
                return ToolFailure(
                    "Not saved: a name comes from the owner's own message in this "
                    "conversation turn, as they wrote it — not from someone else's line "
                    "or an earlier message.")
            room_people.teach(key, name, means_me=action == "name_me")
        except ValueError as exc:
            return ToolFailure(f"That didn't work: {exc}.")
        except room_people.RoomPeopleStoreError as exc:
            return ToolFailure(f"I can't read the saved names right now ({exc}); nothing changed.")
        if action == "name_me":
            return f"Saved for {key}: \"{name}\" means you there."
        return f"Saved for {key}: \"{name}\" belongs to someone else there."

    @staticmethod
    def _names_listed() -> str:
        from core import room_people

        rooms = room_people.taught_everywhere()
        parts = []
        for key, entry in sorted(rooms.items()):
            names = [f"\"{n}\" means you" for n in entry.get("for_me", [])] + \
                    [f"\"{n}\" is someone else" for n in entry.get("people", [])]
            if names:
                parts.append(f"{key} — " + ", ".join(names))
        return (" Saved names: " + "; ".join(parts) + ".") if parts else ""

    @staticmethod
    def _room(input: dict[str, Any], ctx: ToolContext) -> str | None:
        from core.session_keys import room_key

        raw = str(input.get("room") or "").strip()
        surface = str(getattr(ctx, "surface", "") or "")
        if raw:
            if _ROOM_KEY.match(raw):
                return raw
            mention = _CHANNEL_MENTION.match(raw)
            if mention:
                return room_key("discord", mention.group(1))
            if re.fullmatch(r"-?\d+", raw) and surface in _CHAT_PLATFORMS:
                return room_key(_CHAT_PLATFORMS[surface], raw)
            return None
        channel = int(getattr(ctx, "channel_id", 0) or 0)
        if surface in _CHAT_PLATFORMS and channel:
            return room_key(_CHAT_PLATFORMS[surface], channel)
        return None

    @staticmethod
    def _until(input: dict[str, Any]) -> datetime | ToolFailure:
        now = datetime.now()
        raw_until = str(input.get("until") or "").strip()
        if raw_until:
            try:
                until = datetime.fromisoformat(raw_until)
            except ValueError:
                return ToolFailure("I couldn't read that end time; use 2026-10-02T09:00, or hours.")
            if until.tzinfo is not None:
                until = until.astimezone().replace(tzinfo=None)
        elif input.get("hours") not in (None, ""):
            try:
                until = now + timedelta(hours=float(input.get("hours")))
            except (TypeError, ValueError):
                return ToolFailure("Hours has to be a number.")
        else:
            return ToolFailure("Until when? Give an end time or a number of hours.")
        if until > now + timedelta(hours=MAX_HOURS):
            return ToolFailure(f"A room note lasts at most {MAX_HOURS // 24} days; pick a "
                               "nearer end.")
        return until
