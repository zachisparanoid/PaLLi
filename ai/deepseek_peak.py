"""DeepSeek's peak hours (the owner, 10-05: "move the night run off-peak").

DeepSeek bills every token at double its off-peak price from 01:00 to 04:00
and from 06:00 to 10:00 UTC, Monday to Friday (cached input, uncached input
and output alike). Every other hour is off-peak, and so are Saturday and
Sunday whole. The night cycle is scheduled to start off-peak all year
(06:30 America/New_York is 10:30 UTC under EDT and 11:30 UTC under EST), and
her own-time sessions wait out the peak while her brain is DeepSeek.

Pure functions of a UTC moment, no settings and no clock of their own, so
every caller passes its ``now`` and every test pins one.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Tuple

# The published windows, in UTC, on UTC weekdays: [start, end).
PEAK_WINDOWS_UTC: Tuple[Tuple[time, time], ...] = (
    (time(1, 0), time(4, 0)),
    (time(6, 0), time(10, 0)),
)


def _as_utc(moment: datetime) -> datetime:
    """An aware moment converted to UTC; a naive one is taken AS UTC (the
    name of the argument is the contract)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def is_deepseek_peak(utc_dt: datetime) -> bool:
    """True when ``utc_dt`` falls in one of DeepSeek's peak windows."""
    moment = _as_utc(utc_dt)
    if moment.weekday() >= 5:  # Saturday, Sunday
        return False
    clock = moment.time()
    return any(start <= clock < end for start, end in PEAK_WINDOWS_UTC)


def peak_ends_at(utc_dt: datetime) -> datetime:
    """The first off-peak moment at or after ``utc_dt``, in UTC: ``utc_dt``
    itself when it is already off-peak, else the end of its window."""
    moment = _as_utc(utc_dt)
    if is_deepseek_peak(moment):
        clock = moment.time()
        for start, end in PEAK_WINDOWS_UTC:
            if start <= clock < end:
                return moment.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    return moment
