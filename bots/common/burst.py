"""The burst coalescer: someone typing three thoughts in a row is one turn.

Owns settle TIMING only — the caller records the fragments and dispatches
the coalesced turn. Moved here from bots/discord (telegram door,
2026-09-14, task 1): a person types in bursts at every door. The old path
``bots.discord.burst`` is a shim that aliases this module.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChannelBurstState:
    last_message_at: Optional[float] = None
    typing_active_until: float = 0.0
    first_unanswered_at: Optional[float] = None
    # Who is typing, until when (2026-10-12). A person's own message ends
    # their typing: Discord takes the indicator down the moment the message
    # arrives, and the 10 s window from a ping before it waited on nothing
    # (10-10 21:42: 8.5 s of a 26.8 s wait). typing_active_until is the
    # latest of these. Callers that name nobody share one entry (None),
    # which is the old single stamp.
    typing_until: dict = field(default_factory=dict)


def _end_typing(state: ChannelBurstState, user_id) -> None:
    state.typing_until.pop(user_id, None)
    state.typing_active_until = max(state.typing_until.values(), default=0.0)


def evaluate(
    state: ChannelBurstState,
    now: float,
    *,
    settle: float,
    typing_window: float,  # accepted for symmetry; window is applied by the caller when it stamps typing_active_until
    max_hold: float,
) -> tuple[str, Optional[float]]:
    """Pure firing decision. FIRE / WAIT(wake_at) / IDLE.

    IDLE when there is no un-answered message (nothing to reply to — e.g. the
    user typed and erased without sending). Otherwise the earliest fire time is
    ``max(last_message + settle, typing_active_until)`` bounded above by
    ``first_unanswered + max_hold``.
    """
    if state.first_unanswered_at is None or state.last_message_at is None:
        return ("idle", None)
    settle_at = state.last_message_at + settle
    cap_at = state.first_unanswered_at + max_hold
    ready_at = min(max(settle_at, state.typing_active_until), cap_at)
    if now >= ready_at:
        return ("fire", None)
    return ("wait", ready_at)


class BurstCoalescer:
    """Per-channel settle timer. Owns *timing* only; the caller records
    messages and dispatches the coalesced turn in ``on_fire``."""

    def __init__(
        self,
        *,
        on_fire: Callable[[int], Awaitable[None]],
        settle: float = 3.0,
        typing_window: float = 10.0,
        max_hold: float = 45.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._on_fire = on_fire
        self._settle = settle
        self._typing_window = typing_window
        self._max_hold = max_hold
        self._clock = clock or (lambda: asyncio.get_event_loop().time())
        self._state: dict[int, ChannelBurstState] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        # channel_id -> when the last burst that fired here began (its first
        # message, this clock). A burst put back keeps it (rearm), so the
        # max hold still counts from that message (Fable's review, 10-12).
        self._fired_first: dict[int, float] = {}

    def note_message(self, channel_id: int, user_id: Optional[int] = None) -> None:
        """A message that joins the burst. Naming its author ends their typing."""
        now = self._clock()
        st = self._state.setdefault(channel_id, ChannelBurstState())
        st.last_message_at = now
        if st.first_unanswered_at is None:
            st.first_unanswered_at = now
        if user_id is not None:
            _end_typing(st, user_id)
        self._reschedule(channel_id)

    def note_typing(self, channel_id: int, user_id: Optional[int] = None) -> None:
        now = self._clock()
        st = self._state.setdefault(channel_id, ChannelBurstState())
        st.typing_until[user_id] = now + self._typing_window
        st.typing_active_until = max(st.typing_until.values())
        self._reschedule(channel_id)

    def note_sent(self, channel_id: int, user_id: int) -> None:
        """A message that does not join the burst (a guest's line in a room)
        arrived: it ends its author's typing, nothing more."""
        st = self._state.get(channel_id)
        if st is None or user_id not in st.typing_until:
            return
        _end_typing(st, user_id)
        self._reschedule(channel_id)

    def rearm(self, channel_id: int) -> None:
        """A burst put back after its reply was cancelled (2026-10-12): it
        waits again for its author's next message or for their typing to
        lapse. It keeps its first message's time (Fable's review), so the
        max hold still answers it at most ``max_hold`` after that message,
        however long they go on typing."""
        now = self._clock()
        st = self._state.setdefault(channel_id, ChannelBurstState())
        first = self._fired_first.pop(channel_id, now)
        st.first_unanswered_at = (first if st.first_unanswered_at is None
                                  else min(first, st.first_unanswered_at))
        if st.last_message_at is None:
            st.last_message_at = now
        self._reschedule(channel_id)

    def may_hold_again(self, channel_id: int) -> bool:
        """Whether the burst that last fired here is still inside its max
        hold, so it may be put back to wait (Fable's review, 10-12). It reads
        the last fire through this coalescer: a burst dispatched any other
        way (a direct ``_fire_burst``) is judged by the fire before it, and
        an old one only refuses, so the rule then does nothing."""
        first = self._fired_first.get(channel_id)
        return first is None or self._clock() - first < self._max_hold

    def mark_replied(self, channel_id: int) -> None:
        """Clear channel state (called after a reply is delivered / on cancel)."""
        self._state.pop(channel_id, None)
        task = self._tasks.pop(channel_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _reschedule(self, channel_id: int) -> None:
        prior = self._tasks.get(channel_id)
        if prior is not None and not prior.done():
            prior.cancel()
        self._tasks[channel_id] = asyncio.create_task(self._run(channel_id))

    async def _run(self, channel_id: int) -> None:
        while True:
            st = self._state.get(channel_id)
            if st is None:
                return
            decision, wake_at = evaluate(
                st, self._clock(), settle=self._settle,
                typing_window=self._typing_window, max_hold=self._max_hold,
            )
            if decision == "idle":
                return
            if decision == "wait":
                # Poll toward wake_at. Short sleeps keep tests fast and let a
                # hand-advanced clock take effect; a new note cancels this task.
                try:
                    await asyncio.sleep(min(0.05, max(0.0, (wake_at or 0.0) - self._clock())))
                except asyncio.CancelledError:
                    return
                continue
            # decision == "fire"
            fired = self._state.pop(channel_id, None)
            self._tasks.pop(channel_id, None)
            if fired is not None and fired.first_unanswered_at is not None:
                self._fired_first[channel_id] = fired.first_unanswered_at
            try:
                await self._on_fire(channel_id)
            except Exception:
                logger.exception("burst coalescer: on_fire failed for channel %s", channel_id)
            return
