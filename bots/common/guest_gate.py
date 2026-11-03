"""How many guest turns run at once (stranger-proofing week 1, task 2 —
moved here for the telegram door, 2026-09-14, task 1; a queue since Batch 8,
task 2b, 2026-10-01).

The owner gate (2026-08-19) decided WHAT a guest gets: a plain reply, no
tools, none of the owner's memory. Week 1 added HOW MANY: a per-guest rate limit
and a global in-flight cap, with one canned "at capacity" line over either.

Batch 8 (B8-2, ruling T2b-6) changed both on the owner's word:

  * the per-guest rate limit is gone (09-30 16:18: "Drop the limit.") —
    strangers are answered as she chooses, like anyone;
  * the in-flight cap stays as flood protection, without the canned line
    (09-30 16:35): ``GUEST_MAX_IN_FLIGHT`` is a QUEUE. A guest turn over the
    cap waits for a free slot — inside its own task, never holding a door —
    and then she reads it like any other message. First come, first served;
    a released slot is handed straight to the next in line. Floored at one,
    because a queue of zero would wait forever.

A resource floor, not a messaging rule: it decides how many run at once,
never whether she answers. A stranger's slash command is a cheap read, not a
message she decides on, so commands keep the non-waiting ``admit`` (and its
one line, ``AT_CAPACITY_LINE``) — ``bots/telegram/commands.py``.

It lives in bots/common because the money is ONE account: a second door
with its own copy of this would be two budgets, which is not a budget. The
process-wide ``shared_guest_gate()`` is the instance the live doors spend
from; a gate constructed directly is isolated, which is what a test wants.

The knob is read from ``core.config.config`` at CALL time, never captured
at construction: tests monkeypatch the config object, and an operator may
edit .env between boots.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


def _cap() -> int:
    from core.config import config

    try:
        return max(1, int(getattr(config, "GUEST_MAX_IN_FLIGHT", 2) or 0))
    except (TypeError, ValueError):
        return 2


class GuestGate:
    """One guest budget: ``slot()`` for a message turn (it waits its turn),
    ``admit`` / ``release`` for a command (it never waits)."""

    AT_CAPACITY_LINE = (
        "I'm at my limit for guests right now — try me again in a few minutes."
    )

    def __init__(self, cap: Any = None) -> None:
        # ``cap``: a zero-argument callable read at CALL time (as ``_cap``
        # is); None is the guest-turn budget. The guests' voice-note hearing
        # queue (bots/common/voice_note.py, 10-12) passes its own.
        self._cap = cap if cap is not None else _cap
        # Guest turns (and admitted commands) running RIGHT NOW, across
        # everyone; and the turns waiting for a slot, oldest first.
        self.in_flight = 0
        self._waiters: deque[asyncio.Future] = deque()

    @property
    def waiting(self) -> int:
        """How many guest turns are queued for a slot."""
        return sum(1 for fut in self._waiters if not fut.done())

    def admit(self, author_id: Any = None) -> bool:
        """For a stranger's COMMAND: True when a slot is free now (and it is
        then counted), False at the cap — the caller says the one line. Never
        waits, and never jumps the queue of message turns."""
        if self.in_flight >= self._cap() or self.waiting:
            logger.info("guest gate: command from guest %s answered with the at-capacity "
                        "line — %d guest turns in flight", author_id, self.in_flight)
            return False
        self.in_flight += 1
        return True

    async def acquire(self) -> None:
        """For a guest's MESSAGE turn: take a slot, waiting in line for one
        when the cap is reached. Cancellation-safe: a turn cancelled while it
        waits leaves no slot taken, and a slot handed to it at that very
        moment passes on to the next in line."""
        if self.in_flight < self._cap() and not self.waiting:
            self.in_flight += 1
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        logger.info("guest gate: a guest turn waits for a slot (%d in flight, %d waiting)",
                    self.in_flight, self.waiting)
        try:
            await fut                  # woken holding the slot release() handed over
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release()         # it was handed a slot it will never use
            else:
                with contextlib.suppress(ValueError):
                    self._waiters.remove(fut)
            raise

    def release(self, author_id: Any = None) -> None:
        """Give a slot back: straight to the oldest waiting turn, else free it.
        Never below zero: a double release must not hand out a slot that was
        never taken."""
        while self._waiters:
            fut = self._waiters.popleft()
            if fut.done():
                continue
            try:
                fut.set_result(None)   # in_flight stays: the slot changed hands
            except RuntimeError:       # a future of a loop that has closed
                continue
            return
        self.in_flight = max(0, self.in_flight - 1)

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """``async with gate.slot():`` — wait for a slot, give it back after."""
        await self.acquire()
        try:
            yield
        finally:
            self.release()


_shared: GuestGate | None = None


def shared_guest_gate() -> GuestGate:
    """The one process-wide budget every live door spends from."""
    global _shared
    if _shared is None:
        _shared = GuestGate()
    return _shared
