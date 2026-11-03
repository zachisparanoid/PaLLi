"""What a command MEANS, once, for every door (telegram door, 2026-09-14,
task 3).

Three commands say something about her relationship with the person typing
them — what she has learned about them, whether she may speak first, which
account this chat belongs to. None of that is about Discord or about
Telegram, so none of it lives in a transport module. This is the logic; each
door is glue that resolves who is asking, calls a function here, and renders
the returned TEXT the way that transport renders text (an ephemeral followup
on Discord, ``send_turn_reply`` on Telegram).

Plain text, deliberately. Discord's embeds are Discord's; Telegram has no
such thing, and a shared renderer that emitted them would have forced the
other door to strip them back out. One text block is the shape both doors
can show without translating.

Three bugs this module exists to not repeat, all of them real:

  * **The consent row nobody read.** The dead Discord handler wrote the
    toggle to ``(interaction.user.id, "discord")`` — the raw SNOWFLAKE.
    core/local_mode.py seeds, and ai/proactive_policy.py:331 reads,
    ``(the app user id, "discord")``. Every reach-out decision he ever made
    on Discord landed in a row the arbiter never looked at. The owner's row
    is the one written here.
  * **The id space that is not shared.** His Telegram id is not his Discord
    id. ``resolve_caller`` asks the owner gate PER DOOR, and an unresolved
    ref (or a database that will not answer) makes everyone a guest — the
    owner path is proved into, never defaulted into.
  * **The link that could be stolen.** A pairing code names an account; it
    must not be able to move a platform id that already belongs to another
    account onto this one.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.database import brain
from core.local_mode import get_local_owner_refs
from core.owner_gate import classify_author, is_owner_verdict

logger = logging.getLogger(__name__)

# The profile row every surface already shares. "discord" is a historical
# NAME here, not a door: core/local_mode.py:105-126 seeds the owner's
# preferences on ``(palli_user_id, "discord")`` and ai/proactive_policy.py
# reads that row first. Writing a per-door row instead would split his
# settings in two and leave the arbiter reading the half he never touched.
PROFILE_PLATFORM = "discord"

# The doors she can actually link an account on, and the column each one
# writes. A platform absent from this map is refused rather than guessed at:
# `f"{platform}_user_id"` on an unknown word would silently write nothing
# (update_app_user drops unknown keys) and still report success.
_LINK_COLUMNS: dict[str, str] = {
    "discord": "discord_user_id",
    "telegram": "telegram_user_id",
}
_DOOR_LABELS: dict[str, str] = {"discord": "Discord", "telegram": "Telegram"}

# What she says when a shared call could not answer at all. Both doors say
# the same sentence because it is the same failure: discord.py and
# python-telegram-bot each SWALLOW what a command callback raises, so a
# command that says nothing is a thing he asked for that simply never
# happened (DEFECT §19's shape).
TROUBLE_LINE = "I had trouble with that just now — try again in a moment."


def owner_only_line(subject: str) -> str:
    """One shape for "that knob belongs to the person I live with".

    A guest is told the same way about every owner-only setting, at every
    door, so the boundary reads as a boundary rather than as six different
    brush-offs.
    """
    return f"{subject} are a setting for the person I live with."


_GUEST_CONSENT_LINE = owner_only_line("Reach-outs")


@dataclass(frozen=True)
class Caller:
    """Who is asking, and which id space the answer lives in.

    ``user_id`` is the owner's app-user id when this is him, and the raw
    platform id otherwise — the same id a guest's turns already carry as
    their owner_id (bots/discord/client.py:598), so a guest's ``/insights``
    reads the memories their OWN turns wrote and never a word of his.
    """

    is_owner: bool
    user_id: int
    platform_user_id: int
    platform: str = ""


async def resolve_caller(*, platform: str, platform_user_id: int) -> Caller:
    """Ask the owner gate who this is, on THIS door.

    Fail closed on every uncertainty: an unresolved owner ref, a
    non-numeric id, a database that will not answer. The cost is that
    during an outage his own command is answered as a guest's; the
    alternative is handing a stranger his memory.
    """
    door = str(platform or "").strip().lower()
    try:
        refs = await get_local_owner_refs()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "commands: owner refs unresolved (%s); treating %s:%s as a guest",
            exc, door or "?", platform_user_id,
        )
        refs = {}

    verdict = classify_author(platform_user_id, refs.get(f"{door}_user_id"))
    if is_owner_verdict(verdict):
        owner_id = refs.get("palli_user_id") or refs.get("app_user_id")
        if owner_id:
            return Caller(is_owner=True, user_id=int(owner_id),
                          platform_user_id=int(platform_user_id),
                          platform=door)
        # An owner nobody can give an id to is not an owner she can answer as.
        logger.warning("commands: owner recognised but has no app id; guest")

    try:
        own_id = int(platform_user_id)
    except (TypeError, ValueError):
        own_id = 0
    return Caller(is_owner=False, user_id=own_id,
                  platform_user_id=own_id, platform=door)


# ── reach-outs ───────────────────────────────────────────────────────────

def _consent_state(preferences: dict) -> bool:
    consent = preferences.get("proactive_consent")
    if isinstance(consent, dict):
        stored = consent.get("enabled")
        if isinstance(stored, bool):
            return stored
    elif isinstance(consent, bool):
        return consent
    return False


def _consent_line(enabled: bool) -> str:
    return "Reach-outs are on." if enabled else "Reach-outs are off."


async def _owner_preferences(caller: Caller) -> dict:
    profile = await brain.get_user_profile(caller.user_id, PROFILE_PLATFORM)
    prefs = profile.get("preferences", {}) if isinstance(profile, dict) else {}
    return dict(prefs or {})


async def set_proactive_consent(caller: Caller, enabled: bool | None) -> str:
    """Turn reach-outs on or off. OWNER ONLY — whether she speaks first is a
    setting about HIS life, not a knob a guest in a group can reach.

    ``None`` still toggles, but since review round 1 NEITHER door passes it:
    a bare ``/proactive`` and a bare ``/proactive_consent`` both READ
    (``proactive_status``), because one word meaning "tell me" at one door
    and "flip it" at the other is how a person turns a setting off by
    asking about it.
    """
    if not caller.is_owner:
        return _GUEST_CONSENT_LINE

    try:
        prefs = await _owner_preferences(caller)
        current = _consent_state(prefs)
        new_state = (not current) if enabled is None else bool(enabled)
        prefs["proactive_consent"] = {
            "enabled": new_state,
            "source": f"{caller.platform}_command" if caller.platform
                      else "slash_command",
            "updated_at": time.time(),
        }
        await brain.update_user_profile(
            caller.user_id, PROFILE_PLATFORM, {"preferences": prefs})
        logger.info("commands: proactive consent for user %s is now %s",
                    caller.user_id, new_state)
        return _consent_line(new_state)
    except Exception:
        logger.exception("commands: setting proactive consent failed")
        return "I couldn't change that just now — the setting is unchanged."


async def proactive_status(caller: Caller) -> str:
    """The same answer, read-only: asking must never be a write."""
    if not caller.is_owner:
        return _GUEST_CONSENT_LINE
    try:
        return _consent_line(_consent_state(await _owner_preferences(caller)))
    except Exception:
        logger.exception("commands: reading proactive consent failed")
        return "I couldn't read that setting just now."


# ── insights ─────────────────────────────────────────────────────────────

_TOO_EARLY = (
    "Getting to know you\n"
    "We haven't chatted enough yet for me to have real insights. Talk to me "
    "more, and I'll start learning who you are.\n"
    "\n"
    "What I'll learn:\n"
    "- Your personality and communication style\n"
    "- Your interests and what you care about\n"
    "- Important things you share with me\n"
    "- When you like to chat and how you're feeling\n"
    "\n"
    "Just be yourself. I'm paying attention."
)


def _first_chat(created_at: Any) -> str:
    try:
        return datetime.fromtimestamp(float(created_at)).strftime("%B %Y")
    except (ValueError, TypeError, OSError):
        return "a while ago"


async def insights_text(caller: Caller) -> str:
    """What she has learned about the person asking, as plain text.

    A port of the dead Discord ``/insights`` embed (discord_commands.py:
    1499-1605) — the same reads, the same five sections, the same sentences,
    rendered as short headed blocks instead of embed fields.
    """
    try:
        profile = await brain.get_user_profile(caller.user_id, PROFILE_PLATFORM)
        memories = await brain.get_user_memories(
            caller.user_id, memory_type=None, min_importance=0.3)
    except Exception:
        logger.exception("commands: reading insights failed")
        return "I had trouble pulling your insights. Try again in a moment."

    profile = profile if isinstance(profile, dict) else {}
    total = int(profile.get("total_interactions", 0) or 0)
    progress = float(profile.get("learning_progress", 0.0) or 0.0)

    if total < 5:
        return _TOO_EARLY

    blocks: list[str] = [
        "What I know about you",
        f"After {total} conversations, here's what I've learned.",
    ]

    traits = (profile.get("personality_data") or {}).get("traits") or {}
    if isinstance(traits, dict) and traits:
        top = sorted(traits.items(), key=lambda pair: pair[1], reverse=True)[:3]
        blocks.append("\n".join(
            ["Your personality"]
            + [f"- {name.replace('_', ' ').title()}: {value:.0%}"
               for name, value in top]))

    lines = []
    for memory in list(memories or [])[:5]:
        content = str(memory.get("content", "") or "")
        if len(content) > 60:
            content = content[:57] + "..."
        if content:
            lines.append(f"- {content}")
    if lines:
        blocks.append("\n".join(["Things I remember"] + lines))

    preferences = profile.get("preferences") or {}
    interests = preferences.get("interests") or []
    if interests:
        blocks.append("Your interests\n" + ", ".join(
            str(item) for item in list(interests)[:6]))

    style = profile.get("communication_style") or {}
    if isinstance(style, dict) and style:
        notes = [key.replace("_", " ").title()
                 for key, value in style.items()
                 if isinstance(value, (int, float)) and not isinstance(value, bool)
                 and value > 0.6]
        if notes:
            blocks.append("How you communicate\n" + ", ".join(notes[:3]))

    created_at = profile.get("created_at")
    if created_at:
        blocks.append(
            "Our connection\n"
            f"First chat: {_first_chat(created_at)}\n"
            f"Conversations: {total}\n"
            f"How well I know you: {progress:.0%}")

    blocks.append(
        "This is based on our conversations. You can manage your data in "
        "your dashboard.")
    return "\n\n".join(blocks)


# ── linking ──────────────────────────────────────────────────────────────

async def _existing_owner_of(platform: str, platform_user_id: str) -> Any:
    if platform == "discord":
        return await brain.get_app_user_by_discord_id(platform_user_id)
    return await brain.get_app_user_by_telegram_id(platform_user_id)


async def link_platform_account(
    *, platform: str, platform_user_id: int, code: str,
) -> str:
    """Redeem a dashboard pairing code for THIS door's id.

    The code is issued by api/web_account.py:764-775
    (``/account/link/{platform}/code``), which already knows every platform
    in ``_LINK_COLUMNS`` — nothing about the API changes for a second door.
    """
    door = str(platform or "").strip().lower()
    column = _LINK_COLUMNS.get(door)
    if column is None:
        return f"I can't link a {door or 'that'} account — only Discord and Telegram."

    clean = str(code or "").strip().upper()
    try:
        user_id = await brain.get_user_from_pairing_code(clean)
        if not user_id:
            return ("Invalid or expired pairing code — generate a new one on "
                    "the dashboard.")

        platform_id = str(int(platform_user_id))
        existing = await _existing_owner_of(door, platform_id)
        if existing and existing.get("id") != user_id:
            return (f"This {_DOOR_LABELS[door]} account is already linked to a "
                    "different PaLLi account.")

        if not await brain.update_app_user(int(user_id), {column: platform_id}):
            return "I couldn't update the account record — try again in a moment."

        logger.info("commands: linked %s id %s to app user %s",
                    door, platform_id, user_id)
        return (f"Your {_DOOR_LABELS[door]} account is linked to your PaLLi "
                "profile. You can manage preferences, memories and settings "
                "from the dashboard.")
    except Exception:
        logger.exception("commands: linking a %s account failed", door)
        return "Something went wrong linking that account."
