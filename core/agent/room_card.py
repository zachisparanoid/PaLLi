"""The room card and the owner's accounts (step 2, part 5; the who-is-talking
plan, A3 and add-on b; the owner, 2026-10-08).

Two short sections, built by the runner on every turn so every door and every
test sees them, in the volatile tail (they change turn to turn and never touch
the cached prefix):

- ``## This Room``, in a shared room with group chat on, on every turn, a
  guest's included: the app, the channel and server or the group's title, who
  has spoken lately, what she can't see, and the time zone of the times on the
  lines. Facts anyone in the room has; nothing of the owner's.
- ``## The Owner's Accounts``, on the owner's turns only, on every app: who the
  owner is on each app. 10-08, on the web chat: asked to DM the owner on
  Discord, she looked for a name the owner's Discord account doesn't use, got a
  vague answer, and said Discord wasn't working.

Names come from the apps (a channel's, a server's, a group's) or from the
owner's own account records, never from what anyone typed; each is cut to one
short line.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Sequence

from core.agent.speakers import clean_name, is_shared_room, speaker_of

logger = logging.getLogger(__name__)

ROOM_HEADING = "## This Room"
ACCOUNTS_HEADING = "## The Owner's Accounts"
_NAME_CHARS = 80
_APPS = {"discord": "Discord", "telegram": "Telegram"}

# Telegram's privacy setting for her bot, as the Telegram door read it at
# startup (getMe): True on, False off, None not known. Said only when on.
_telegram_privacy: bool | None = None
# The owner's names as the owner's own turns showed them, per app: (name,
# handle). In memory only; account ids always come from the owner's record.
_seen_owner: dict[str, tuple[str, str]] = {}


def reset_for_tests() -> None:
    global _telegram_privacy
    from core import room_people

    _telegram_privacy = None
    _seen_owner.clear()
    room_people.reset_for_tests()


def set_telegram_privacy(on: bool | None) -> None:
    """The Telegram door's startup check (step 2, part 8) says what it found."""
    global _telegram_privacy
    _telegram_privacy = on if isinstance(on, bool) else None


def telegram_privacy_on() -> bool:
    return _telegram_privacy is True


def remember_owner(turn: Any) -> None:
    """Keep the names the owner's own account showed on this turn. Only an
    owner record counts: the doors set it after checking the account."""
    speaker = speaker_of(turn)
    if not speaker or speaker.get("kind") != "owner" or speaker.get("platform") not in _APPS:
        return
    name, handle = _one_line(speaker.get("name")), _one_line(speaker.get("handle"))
    if name or handle:
        _seen_owner[speaker["platform"]] = (name, handle)


def _one_line(value: Any) -> str:
    """A name or title from an app, as she shows it: one short line with no
    symbol, bracket or colon, so a server, group, channel or thread named
    after the owner's mark can't show it (Fable's final review, 3)."""
    return clean_name(str(value or "").strip().strip("#"))[:_NAME_CHARS].strip()


def _quoted(value: Any) -> str:
    return '"' + _one_line(value).replace('"', "'") + '"'


# ── the room ─────────────────────────────────────────────────────────────────

def _room_line(turn: Any) -> str:
    md = getattr(turn, "metadata", None)
    channel = _one_line(getattr(md, "channel_name", None))
    if str(getattr(turn, "surface", "")) == "telegram_text":
        return f"This is a Telegram group, {_quoted(channel)}." if channel else "This is a Telegram group."
    server = _one_line(getattr(md, "guild_name", None))
    extra = getattr(md, "extra", None)
    thread_in = extra.get("thread_in") if isinstance(extra, dict) else None
    if thread_in is not None:                    # a thread (step 2, part 7)
        line = f"This is a thread, {_quoted(channel)}" if channel else "This is a thread"
        if _one_line(thread_in):
            line += f", in #{_one_line(thread_in)}"
        if server:
            line += f", in the server {_quoted(server)}"
        return line + "."
    where = f"#{channel}" if channel else ""
    if server:
        where = f"{where}, in the server {_quoted(server)}" if where else f"the server {_quoted(server)}"
    return f"This is a group chat on Discord: {where}." if where else "This is a group chat on Discord."


def room_section(turn: Any, rows: Sequence[Any], key: str = "") -> str:
    """The room card for a turn in a shared room; "" anywhere else. With the
    room's ``key``, who's who there too (the room-reading fix)."""
    if not is_shared_room(turn):
        return ""
    from core.agent import message_times as _mt
    from core.agent.room_tail import recent_speakers

    lines = [ROOM_HEADING, _room_line(turn)]
    names = recent_speakers(rows or [])
    if names:
        lines.append(f"Recently here, newest first: {', '.join(names)}.")
    if key:
        lines.extend(_whos_who(turn, key, names))
    lines.append("What I can't see here: messages from other bots, and edits made after a "
                 "message was sent.")
    if str(getattr(turn, "surface", "")) == "telegram_text" and _telegram_privacy is True:
        lines.append("Telegram's privacy setting is on for me, so unless I'm an admin here I "
                     "only see messages that mention me, reply to me, or start with a slash "
                     "command.")
    if _mt.message_times_enabled():
        zone = datetime.now().astimezone().tzname() or "the server's local time"
        lines.append(f"The time in front of each message is when it was sent ({zone}).")
    return "\n".join(lines)


PEOPLE_SHOWN = 8


def _whos_who(turn: Any, key: str, recent: Sequence[str]) -> list[str]:
    """Who else is in the room (the Discord member list and the names the
    owner taught for the room, less the owner and whoever is already in
    "Recently here"), and, on the owner's turns, the names the owner taught
    her for herself here (the room-reading fix, 2026-10-12)."""
    from core import room_people
    from core.agent.speakers import OWNER_MARK
    from core.owner_gate import is_guest_turn

    shown = {n.replace(OWNER_MARK, "").strip().casefold() for n in recent}
    shown.update(n.casefold() for pair in _seen_owner.values() for n in pair if n)
    taught = room_people.taught(key)
    others = [n for n in room_people.people_here(key) if n.casefold() not in shown]
    out: list[str] = []
    if others:
        more = len(others) - PEOPLE_SHOWN
        listed = ", ".join(others[:PEOPLE_SHOWN]) + (f", and {more} more" if more > 0 else "")
        out.append(f"Also in this room: {listed}.")
    if taught["for_me"] and not is_guest_turn(turn):
        out.append("The owner told me I also go by " + " and ".join(taught["for_me"]) + " here.")
    return out


def names_lines(turn: Any, rows: Sequence[Any], key: str) -> tuple[str, str]:
    """The names this turn's message calls (``core.room_people``): for the
    quick reads, in the third person, and for her reply call, in her own."""
    from core import room_people

    try:
        found = room_people.names_in(
            str(getattr(turn, "content", "") or ""),
            mine=room_people.her_names(key), here=room_people.people_here(key, rows))
    except Exception as exc:  # noqa: BLE001 — a fact line is never worth the turn
        logger.warning("room card: could not read the names in a message (%s)",
                       type(exc).__name__)
        return "", ""
    return (room_people.render(found, voice="her"), room_people.render(found, voice="me"))


# ── the owner's accounts ─────────────────────────────────────────────────────

def _discord_names(account_id: int) -> tuple[str, str]:
    """(display name, account name) from Discord's member list when the door
    is connected; else what the owner's own turns showed; else nothing."""
    try:
        from core.agent import hands_runtime as _hr

        member = _hr.find_member_by_id(int(account_id))
    except Exception:
        member = None
    if member is not None:
        return _one_line(getattr(member, "display_name", "")), _one_line(getattr(member, "name", ""))
    return _seen_owner.get("discord", ("", ""))


def _account(app: str, account_id: Any, names: tuple[str, str]) -> str:
    display, handle = names
    shown = ""
    if handle:
        shown = f"@{handle}" + (f" ({_quoted(display)})" if display and display != handle else "")
    elif display:
        shown = _quoted(display)
    if shown:
        return f"On {app} the owner is {shown}, id {account_id}."
    return f"On {app} the owner's id is {account_id}."


def owner_accounts(refs: Mapping[str, Any] | None) -> str:
    """Who the owner is on each app, from the owner's account ids (dispatch
    reads them beside the owner check, so the run never waits on them); ""
    when no account is known."""
    if not refs:
        return ""
    lines: list[str] = []
    discord_id = refs.get("discord_user_id")
    if discord_id:
        lines.append(_account("Discord", discord_id, _discord_names(discord_id)))
    telegram_id = refs.get("telegram_user_id")
    if telegram_id:
        lines.append(_account("Telegram", telegram_id, _seen_owner.get("telegram", ("", ""))))
    if not lines:
        return ""
    return "\n".join([ACCOUNTS_HEADING, *lines,
                      "Messages from the web chat, the dashboard and the phone app are always the owner's."])
