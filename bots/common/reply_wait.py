"""The wait a person actually has: from their message to the reply (2026-10-12).

The runner's ``latency_ms`` starts after the burst settles and stops before
the door sends, so it never showed the settle, the typing hold or the paced
send: the traced 10-10 21:42 reply waited 26.8 s, of which the runner saw
9.1 s. Two weeks of Discord gateway logs (Sep 27 to Oct 11, 418 replies) put
the real wait at a median of 31.5 s.

Each door stamps when a burst's messages arrive (``stamp``), keeps the stamp
with the burst, and logs one line once the reply is out
(``log_reply_wait``). Times are ``time.monotonic()``, the same clock as
the burst coalescer's default (the event loop's time) on the default
loop; the two are never compared with each other.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# (first message's arrival, last message's arrival), time.monotonic().
BurstTimes = Tuple[float, float]


def stamp(times: Optional[BurstTimes], now: Optional[float] = None) -> BurstTimes:
    """A burst's arrival times with one more message added."""
    now = time.monotonic() if now is None else now
    return (now, now) if times is None else (times[0], now)


def merge(a: Optional[BurstTimes], b: Optional[BurstTimes]) -> Optional[BurstTimes]:
    """Two stamps for fragments that answer as one burst (a room read said
    "wait" and they went back in): the earlier first, the later last."""
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), max(a[1], b[1]))


def last_arrival(times: Optional[BurstTimes]) -> Optional[float]:
    """When the last message of the burst arrived, or None without a stamp."""
    return None if times is None else times[1]


def log_reply_wait(door: str, channel_id: int, times: Optional[BurstTimes], *,
                   typing: float = 0.0, during_run: int = 0,
                   now: Optional[float] = None, kind: str = "") -> None:
    """One line per delivered reply: how long the person waited for it.
    Nothing without a stamp (reminders and self-started messages answer no
    burst).

    ``kind`` names a reply given while PaLLi works ("mid-task reply", "side
    conversation", "side task"; 10-12), so talking while working can be
    counted from the log.

    ``during_run``: messages of theirs that came in while the run worked
    (Fable's review, 10-12). A run can answer them too, so "since the last
    message" counts from the burst that started it; the line says so
    rather than pass a long run off as a long wait for one message.
    """
    if times is None:
        return
    now = time.monotonic() if now is None else now
    more = (f"; {during_run} more message{'s' if during_run != 1 else ''} "
            "came in during the run") if during_run > 0 else ""
    logger.info(
        "reply wait: %.1fs since the last message, %.1fs since the first; "
        "typing shown %.1fs (%s %s%s)%s",
        max(0.0, now - times[1]), max(0.0, now - times[0]), typing, door,
        channel_id, f", {kind}" if kind else "", more,
    )
