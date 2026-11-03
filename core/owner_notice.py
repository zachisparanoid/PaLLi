"""Plain notices to the owner — words that are not hers (2026-10-04).

The spending check (core/spending.py, owner decision T2b-6) has to tell the
owner something without going through her: no turn, no model, no voice of
hers. Two existing roads were wrong for it:

- ``channel_runtime.send_envelope`` wants a channel id, and the owner's DM
  channel has no id until Discord opens it; and every send through it tells
  her Present Moment "you reached out to them on your own"
  (``initiative_sent``) — exactly what did not happen.
- core may not import bots (tests/architecture/test_import_boundaries.py), so
  reaching for ``bots.discord.client.get_active_client`` from here is the
  debt that file exists to stop growing.

So the doors register a sender here when they start (bots/discord/client.py,
bots/telegram/client.py) and take it down when they stop, and core asks them
in order: the owner's Discord DMs first, then the owner's Telegram chat, then
the phone app (the door he names in policy.proactive.door goes first) — the
doors the owner is reached through outside a turn (reminders, proactive
outreach). A sender answers True when the text went out and False when it
could not send; it should not raise, and if it does, or hangs, the next door
is asked. Nothing here writes her conversation history or transcript: the
notice is not something she said.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Dict, Tuple

logger = logging.getLogger(__name__)

OwnerNoticeSender = Callable[[str], Awaitable[bool]]

# The order the doors are asked in. Discord first: the owner's DMs are where
# her other unprompted words to the owner go when Discord is up.
NOTICE_ORDER: Tuple[str, ...] = ("discord", "telegram", "mobile")
# One door's send, at most. A gateway that has gone away must not hold the
# hourly spending check (or anything else that calls this) for long.
SEND_TIMEOUT_S = 30.0

_senders: Dict[str, OwnerNoticeSender] = {}


def register_owner_notice_sender(platform: str, sender: OwnerNoticeSender) -> None:
    """The door ``platform`` can reach the owner through ``sender`` from now
    on. A second registration replaces the first (a restarted door)."""
    _senders[str(platform or "").strip().lower()] = sender


def unregister_owner_notice_sender(platform: str, sender: OwnerNoticeSender | None = None) -> None:
    """Take the door down. With ``sender``, only when it is still the one
    registered: an old door's shutdown must not remove its replacement. Bound
    methods compare with ``==`` (each attribute access makes a new object)."""
    key = str(platform or "").strip().lower()
    current = _senders.get(key)
    if current is None:
        return
    if sender is None or current == sender:
        _senders.pop(key, None)


def registered_doors() -> Tuple[str, ...]:
    return tuple(door for door in NOTICE_ORDER if door in _senders) + tuple(
        sorted(door for door in _senders if door not in NOTICE_ORDER)
    )


def _notice_order() -> Tuple[str, ...]:
    """NOTICE_ORDER, with the owner's preferred door (policy.proactive.door,
    the mobile door, 2026-10-13) asked first when he named one."""
    try:
        from core import doors

        preferred = doors.proactive_door_setting()
    except Exception:
        preferred = "last_used"
    if preferred in NOTICE_ORDER:
        return (preferred,) + tuple(d for d in NOTICE_ORDER if d != preferred)
    return NOTICE_ORDER


async def send_owner_notice(
    text: str,
    *,
    order: Tuple[str, ...] | None = None,
    timeout_s: float = SEND_TIMEOUT_S,
) -> str:
    """Send ``text`` to the owner through the first door that takes it.

    Returns the door's name ("discord", "telegram", "mobile"), or "" when no
    door could send — the caller decides what an untold notice means (the
    spending check still logs it and records it). Never raises."""
    order = _notice_order() if order is None else order
    clean = str(text or "").strip()
    if not clean:
        return ""
    for door in order:
        sender = _senders.get(door)
        if sender is None:
            continue
        try:
            sent = await asyncio.wait_for(sender(clean), timeout_s)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("owner notice: the %s door did not answer within %.0f s", door, timeout_s)
            continue
        except Exception as exc:  # noqa: BLE001 — the next door is asked
            logger.warning("owner notice: the %s door could not send (%s)", door, exc)
            continue
        if sent:
            return door
    return ""
