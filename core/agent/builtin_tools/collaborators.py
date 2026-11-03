"""The ``collaborators`` tool (Batch 8, task 1 — B8-1, rulings R9, R10).

The owner names who counts as a collaborator in shared rooms ("make Sam a
collaborator"), and she keeps the list through this tool. Owner-only, and
always on the owner's belt (no flag: the filter it feeds is a security fix,
not a feature). The list itself lives in ``core.collaborators``
(``data/collaborators.json``); ``palli_ctl collaborators`` is the owner's
other door to the same file.

Who: a platform user id works on both doors (a Discord mention too). A name
works on Discord, looked up among the members she can see
(``hands_runtime.resolve_member``; an unsure match goes back to her as
candidates — her read). Telegram has no lookup by name, so there she asks
for the numeric id. Removing also finds someone by the name stored on the
list, so a person who has left the server can still be taken off.
"""
from __future__ import annotations

from typing import Any

from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext

_ACTIONS = ("add", "remove", "list")


def _platform_here(ctx: ToolContext) -> str | None:
    """The chat door this turn is on, if it is one of the two."""
    surface = str(getattr(ctx, "surface", "") or "")
    if surface in ("discord_text", "discord_voice"):
        return "discord"
    if surface == "telegram_text":
        return "telegram"
    return None


class CollaboratorsTool:
    name = "collaborators"
    description = (
        "Keep the owner's list of collaborators for shared rooms (a server "
        "channel, a group chat). In a shared room you take part as a "
        "participant, and everyone there reads the room's conversation. A "
        "collaborator also reads the work you did in that room (your tool "
        "receipts); everyone else there is a stranger and reads only what was "
        "said out loud. Your private sections about the owner stay the "
        "owner's for both. Use this when the owner tells you who is a "
        "collaborator. action: add, remove or list. who: their user id "
        "(either door), or their name on Discord. platform: discord or "
        "telegram — defaults to the chat you're in."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "who": {"type": "string", "description": "their user id, or their name on Discord"},
            "platform": {"type": "string", "enum": ["discord", "telegram"]},
        },
        "required": ["action"],
    }
    requires_approval = False
    owner_only = True

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        from core import collaborators as store

        action = str(input.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            return ToolFailure("Say add, remove or list.")
        try:
            if action == "list":
                entries = store.list_collaborators()
                if not entries:
                    return ("No collaborators yet — in shared rooms, everyone but the "
                            "owner is a stranger.")
                return "Collaborators: " + "; ".join(store.describe(e) for e in entries) + "."
        except store.CollaboratorStoreError as exc:
            return ToolFailure(f"I can't read the collaborators list right now ({exc}); nothing changed.")

        raw_platform = str(input.get("platform") or "").strip().lower()
        platform = raw_platform or _platform_here(ctx)
        if platform not in store.PLATFORMS:
            return ToolFailure("Which platform — discord or telegram? A collaborator is "
                               "named on one door at a time.")
        who = str(input.get("who") or "").strip()
        if not who:
            return ToolFailure("Who? Give me their user id, or their name on Discord.")

        user_id, name, unsure = self._resolve(platform, who, action)
        if unsure:
            return ToolFailure(unsure)
        try:
            if action == "add":
                is_new = store.add_collaborator(platform, user_id, name=name)
                label = store.describe({"platform": platform, "user_id": user_id, "name": name})
                if not is_new:
                    return f"{label} was already a collaborator."
                return (f"{label} is a collaborator now: in shared rooms on {platform} "
                        "they also read the work you do there.")
            removed = store.remove_collaborator(platform, user_id)
            label = store.describe({"platform": platform, "user_id": user_id, "name": name})
            if not removed:
                return f"{label} wasn't on the list."
            return f"{label} is a stranger again: in shared rooms they read only what is said."
        except ValueError as exc:
            return ToolFailure(f"That didn't work: {exc}.")
        except store.CollaboratorStoreError as exc:
            return ToolFailure(f"I can't read the collaborators list right now ({exc}); nothing changed.")

    def _resolve(self, platform: str, who: str, action: str) -> tuple[str, str, str]:
        """(user_id, name, "") — or ("", "", what to tell her)."""
        from core import collaborators as store

        if platform == "telegram":
            try:
                return store.normalize_user_id(who), "", ""
            except ValueError:
                pass
            found = self._stored_by_name(platform, who) if action == "remove" else None
            if found is not None:
                return found
            return "", "", "Telegram can't look people up by name — give me their numeric id."

        from core.agent import hands_runtime as hr

        raw_id = hr.parse_raw_id(who)
        if raw_id is not None:
            member = hr.find_member_by_id(raw_id)
            name = (getattr(member, "display_name", None) or getattr(member, "name", None) or "") if member else ""
            return str(raw_id), str(name), ""
        if action == "remove":
            found = self._stored_by_name(platform, who)
            if found is not None:
                return found
        member, candidates = hr.resolve_member(who)
        if member is None:
            if candidates:
                return "", "", f"More than one person could be '{who}': {', '.join(candidates)}. Your call."
            return "", "", f"Nobody I can see matches '{who}'. Their user id works too."
        name = getattr(member, "display_name", None) or getattr(member, "name", None) or who
        try:
            return store.normalize_user_id(getattr(member, "id", None)), str(name), ""
        except ValueError:
            return "", "", f"I found '{who}' but not their id. Their user id works too."

    @staticmethod
    def _stored_by_name(platform: str, who: str) -> tuple[str, str, str] | None:
        from core import collaborators as store

        try:
            entries = store.list_collaborators()
        except store.CollaboratorStoreError:
            return None
        wanted = " ".join(who.split()).lower()
        hits = [e for e in entries if e.get("platform") == platform
                and str(e.get("name") or "").strip().lower() == wanted]
        if len(hits) == 1:
            return str(hits[0].get("user_id")), str(hits[0].get("name") or ""), ""
        return None
