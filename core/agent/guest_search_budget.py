"""How many web searches PaLLi runs for friends (R14, T7; plan
docs/2026-10-12-one-first-look-plan.md §2b.7a).

The owner, 10-12 21:20: "yes, friends should get web search"; 21:42: "yeah a
few searches per hour per friend is fine"; 22:03 (Q5): 5 per friend per
rolling hour, counted per platform account across both apps in one ledger,
60 per room per day, 5 results per search, and the owner's own searches never
count. Each search is an Exa charge the spending check never sees (it reads
the reply provider's ledger only); this is what bounds it.

A resource floor, not a messaging rule (the ``bots/common/guest_gate.py``
pattern): it decides whether a search is sent, never what PaLLi says. Over a
cap the guest's ``web_search`` returns a plain result saying so and when the
next one frees up — a fact for PaLLi to weigh, never a line to say — and the
run goes on to a reply or a quiet choice as always: the message is never
dropped.

- **Who counts:** a guest's search, keyed by the platform account
  ``(platform, id)`` — on a guest's turn ``ToolContext.turn_owner_id`` is the
  guest's platform id (the doors put it on ``turn.owner_id``). A friend on
  Discord and the same friend on Telegram are two accounts PaLLi cannot link,
  so two budgets. A collaborator is a guest and counts. The owner never counts
  and the ledger is never read on the owner's turn.
- **The rooms:** each room, ``(platform, channel id)``, takes so many guests'
  searches a rolling day, whoever asks.
- **When it counts:** when the search is sent to Exa. A failed Exa call counts
  too: it is the bill this protects, and a retry loop must not burn through
  the window. A refused search counts nowhere.
- **The numbers** are settings read at call time:
  ``features.guest_web_searches_per_hour`` (5) and
  ``features.guest_web_searches_per_room_per_day`` (60); 0 refuses every
  search with the same plain result. Results per search are a constant (5).
- **Restart:** the ledger lives in memory, as ``GuestGate``'s does, so a
  restart starts every friend's hour and every room's day over. Accepted in
  the plan as a floor, not a wall (§6 risk 13).

One process-wide ledger, ``shared_budget()``, is what both doors' turns spend
from (the doors share the one spine, so a second instance would be a second
budget, which is not a budget); a ``GuestSearchBudget`` built directly is
isolated, which is what a test wants.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

HOUR = 3600.0
DAY = 86400.0
# Q5 (the owner, 22:03): five results per search for a guest (cost).
RESULTS_PER_SEARCH = 5
_DEFAULT_PER_HOUR = 5
_DEFAULT_PER_ROOM_DAY = 60

# The tool's own words when a search is not sent (plan §2b.7a): a fact for
# PaLLi, never a line to say. Not wrapped as web content — they are not.
OVER_THE_HOUR = (
    "web search isn't available for this person right now: they've reached the "
    "{cap} searches an hour I can run for a guest; the next one frees up in {wait}."
)
OVER_THE_ROOM_DAY = (
    "web search isn't available in this room right now: it has reached the "
    "{cap} searches a day I can run for guests here; the next one frees up in {wait}."
)
NONE_FOR_THIS_PERSON = (
    "web search isn't available for this person right now: I can't run searches "
    "for guests at the moment."
)
NONE_IN_THIS_ROOM = (
    "web search isn't available in this room right now: I can't run searches "
    "for guests here at the moment."
)


def _wait_words(seconds: float) -> str:
    minutes = max(1, math.ceil(seconds / 60.0))
    if minutes < 120:
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    return f"{math.ceil(minutes / 60.0)} hours"


def limits() -> tuple[int, int]:
    """(per friend per hour, per room per day), read from her settings at call
    time; the schema's defaults when settings are unbooted or unreadable."""
    per_hour, per_day = _DEFAULT_PER_HOUR, _DEFAULT_PER_ROOM_DAY
    try:
        from core.settings_boot import settings_service

        feats = getattr(settings_service.snapshot(), "features", None)
        if feats is not None:
            per_hour = int(getattr(feats, "guest_web_searches_per_hour", per_hour))
            per_day = int(getattr(feats, "guest_web_searches_per_room_per_day", per_day))
    except Exception as exc:  # noqa: BLE001 — a floor is never lifted over this
        logger.warning("guest search budget: settings unreadable (%s: %s) — the defaults stand",
                       type(exc).__name__, exc)
    return max(0, per_hour), max(0, per_day)


def _keys(ctx: Any) -> tuple[tuple[str, str], tuple[str, str]]:
    from core.agent.tools import _platform_for_ctx

    surface = str(getattr(ctx, "surface", "") or "")
    platform = _platform_for_ctx(ctx) or surface or "unknown"
    friend = (platform, str(getattr(ctx, "turn_owner_id", "") or ""))
    room = (platform, str(getattr(ctx, "channel_id", "") or ""))
    return friend, room


def _who(key: tuple[str, str]) -> str:
    """A key as the log names it: the platform and the id, never a name."""
    return f"{key[0]}:{key[1]}"


class GuestSearchBudget:
    """The ledger: the times of each friend's and each room's searches."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._friends: dict[tuple[str, str], deque[float]] = {}
        self._rooms: dict[tuple[str, str], deque[float]] = {}

    @staticmethod
    def _window(book: dict, key: tuple[str, str], now: float, span: float) -> deque[float]:
        times = book.setdefault(key, deque())
        while times and now - times[0] >= span:
            times.popleft()
        return times

    def take_for(self, ctx: Any, *, per_hour: int, per_room_day: int) -> str | None:
        """None: the search may be sent, and it is counted now. Otherwise the
        plain result to return instead (nothing is counted). The owner's turn
        returns None before the ledger is touched. No await between the check
        and the count, so two turns on one event loop cannot both slip in."""
        if bool(getattr(ctx, "is_owner", False)):
            return None
        friend, room = _keys(ctx)
        # One INFO line per count and per refusal — the account and the room
        # by id only, as the doors' own logs name a guest — so a week's Exa
        # bill for friends, which the spending check never sees, can be read
        # off the log (Fable's review, 10-13).
        if per_hour <= 0 or per_room_day <= 0:
            logger.info("guest search: refused for %s — the cap is 0", _who(friend))
            return NONE_FOR_THIS_PERSON if per_hour <= 0 else NONE_IN_THIS_ROOM
        now = self._clock()
        mine = self._window(self._friends, friend, now, HOUR)
        if len(mine) >= per_hour:
            wait = _wait_words(mine[0] + HOUR - now)
            logger.info("guest search: refused for %s — %d/%d this hour; the next frees up in %s",
                        _who(friend), len(mine), per_hour, wait)
            return OVER_THE_HOUR.format(cap=per_hour, wait=wait)
        here = self._window(self._rooms, room, now, DAY)
        if len(here) >= per_room_day:
            wait = _wait_words(here[0] + DAY - now)
            logger.info("guest search: refused for %s — the room %s is at %d/%d today; "
                        "the next frees up in %s",
                        _who(friend), _who(room), len(here), per_room_day, wait)
            return OVER_THE_ROOM_DAY.format(cap=per_room_day, wait=wait)
        mine.append(now)
        here.append(now)
        logger.info("guest search: counted for %s — %d/%d this hour; %d/%d today in %s",
                    _who(friend), len(mine), per_hour, len(here), per_room_day, _who(room))
        return None


_shared: GuestSearchBudget | None = None


def shared_budget() -> GuestSearchBudget:
    """The one process-wide ledger every guest's search spends from."""
    global _shared
    if _shared is None:
        _shared = GuestSearchBudget()
    return _shared


def take_for(ctx: Any) -> str | None:
    """The guest tool's one call: the settings read now, the shared ledger."""
    if bool(getattr(ctx, "is_owner", False)):
        return None
    per_hour, per_day = limits()
    return shared_budget().take_for(ctx, per_hour=per_hour, per_room_day=per_day)
