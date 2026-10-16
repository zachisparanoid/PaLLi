"""Which chat she can reach him in (fix plan 5.8; private chats only since 10-13).

A "door" is a Discord or Telegram chat: a place she can post a message and he
will see it. ``last_used_door`` answers "which private chat did he use most
recently", the same lookup the arbiter uses to put her reach-outs on the
private chat he used last (``core/turn/proactive.py::_pick_proactive_surface``).
This module wraps it, so the arbiter and the reminder tools read one answer;
the query, its ``PROACTIVE_ALLOWED_CHANNEL_IDS`` rule for Discord rows and its
never-a-random-room rule all stay where they are.

``is_private_chat`` is the owner's rule (2026-10-13), in one place: her own
messages to him (check-ins, follow-ups, shares of her work, her thought for
tonight, a saved question) go to a private chat with him, a Discord DM or a
Telegram private chat, never a server channel or a Telegram group. On 10-12
at 08:10 a note meant for him went to a server channel his friends could see.
Updates on work started in a group are the other kind: they carry their own
chat and never ask here.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The doors she converses on. A dashboard, a voice call and the local mic
# are places he talks to her, not chats she can post a message into. The
# phone app joined on 2026-10-13 (the mobile door): one conversation, his
# alone, on channel 1.
CHAT_DOORS: tuple[str, ...] = ("discord", "telegram", "mobile")

# The web page (2026-10-10, web-page reminders). Not a chat she converses on,
# and never "the door he used last": only a reminder with no private chat at
# all goes here, saved as a conversation row the page reads when it opens and
# every 15 seconds while it is open (GET /api/chat/history). The owner's rule:
# "last chat, else the web page". The page is his alone.
WEB_DOOR = "dashboard"

# Chats already warned about for rows that disagree on whether the chat is
# private: once per chat per process, not on every pick.
_MIXED_MARKS_WARNED: set[tuple[str, int]] = set()


async def is_private_chat(
    platform: str, channel_id: int, *, marked_private: bool, marked_group: bool,
) -> bool:
    """True only when the chat is shown to be a private chat with him.

    ``marked_private`` / ``marked_group``: whether any of his rows from this
    chat says it is private, or says it is not (core/turn/persistence.py marks
    every row from a Discord or Telegram chat since 10-13, from what the
    door's intake saw). The chat is shown private by:

    * Telegram: its id. Telegram numbers a private chat by the person's own
      user id, above zero, and a group or supergroup below zero; the door
      admits no other kind of chat (bots/telegram/intake.py). A chat below
      zero is a group whatever its rows say.
    * The phone app: by construction. A device token is always the owner,
      and the door has one chat (channel 1), so any nonzero chat is private
      with him (a row marked a group still never is).
    * Discord: a private mark. A row from before the mark carries nothing
      that tells his DM from a server channel, so the Discord door is asked
      whether the chat is his DM with her
      (``channel_runtime.shows_owner_private_chat``); a door that is down
      or cannot say shows nothing.

    A chat any of whose rows says it is not private never is; when others
    say it is, the disagreement is logged as a WARNING, once per chat per
    process (a door's intake cannot change its mind about a chat, so it
    points at a writer that marks wrongly). Anything that cannot be shown is
    False: such a chat is never picked."""
    door = str(platform or "").strip().lower()
    try:
        chat = int(channel_id)
    except (TypeError, ValueError):
        return False
    if marked_group:
        if marked_private and (door, chat) not in _MIXED_MARKS_WARNED:
            _MIXED_MARKS_WARNED.add((door, chat))
            logger.warning(
                "doors: the owner's rows from %s chat %s disagree on whether it is "
                "a private chat with him; it is treated as a group and never picked "
                "for her own messages to him", door, chat,
            )
        return False
    if not chat:
        return False
    if door == "telegram":
        return chat > 0
    if door == "mobile":
        # The phone app (2026-10-13): a device token is always the owner — no
        # guests, no groups — so its one chat (channel 1) is private with him.
        return chat > 0
    if marked_private:
        return True
    from core.channel_runtime import channel_runtime

    return await channel_runtime.shows_owner_private_chat(door, str(chat))


async def phone_reachable(owner_id: int) -> bool:
    """Whether the phone app can reach him now: the mobile door is up and
    ready, and at least one phone is paired (an unrevoked device; spec §7).

    The one reachability question for the phone, asked by every road that
    could send her words or a notice there: the preferred door and the
    last-used scan (core/turn/proactive.py) and the door's owner notice
    (bots/mobile/adapter.py). Without it, a phone swap that leaves no phone
    paired would send her into a timeline nobody reads while he waits on
    another door. ``is_private_chat`` stays the privacy question only.
    Never raises: anything that cannot be shown is False."""
    try:
        from core.channel_runtime import channel_runtime

        if not await channel_runtime.door_ready("mobile"):
            return False
        from core.mobile_auth import device_store

        return await device_store().count_devices(owner_id=int(owner_id)) > 0
    except Exception as exc:  # noqa: BLE001 — unknown reads as unreachable
        logger.debug("doors: could not tell whether a phone is reachable (%s)", exc)
        return False


async def last_used_door(owner_id: int) -> tuple[str, int] | None:
    """The Discord or Telegram private chat with him he used most recently, as
    ``(platform, channel_id)``, or None when there is none. Never a server
    channel or a group (the owner, 10-13).

    The lookup returns the id as a string; a chat id that is not a nonzero
    integer is not a chat anyone can post to, so it answers None rather than
    a guess."""
    # Imported here: proactive pulls in the whole turn spine, and this module
    # is imported by a tool at load time.
    from core.turn import proactive

    picked = await proactive._pick_proactive_surface(owner_id=owner_id, platforms=CHAT_DOORS)
    if picked is None:
        return None
    platform, raw_id = picked
    try:
        channel_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    if not channel_id:
        return None
    return (str(platform), channel_id)


# The owner's reach-out door (policy.proactive.door, the mobile door spec §7).
_REACH_OUT_DOORS = ("last_used", "mobile", "discord", "telegram")


def proactive_door_setting() -> str:
    """Which door her own messages to him prefer: a door's name, or
    "last_used" (the private chat he used most recently — the default).
    Anything unreadable reads as "last_used"."""
    try:
        from core.settings_boot import settings_service

        value = str(settings_service.snapshot().policy.proactive.door or "last_used")
    except Exception:
        return "last_used"
    return value if value in _REACH_OUT_DOORS else "last_used"
