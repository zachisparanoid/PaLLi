"""DeepSeek's peak hours (the owner, 10-05: "move the night run off-peak").

Published: peak is 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday; every
other hour, and all of Saturday and Sunday, is off-peak (half price).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ai.deepseek_peak import is_deepseek_peak, peak_ends_at

UTC = timezone.utc
MONDAY = datetime(2026, 10, 5, tzinfo=UTC)  # a Monday
NY = ZoneInfo("America/New_York")


def _at(day: datetime, hour: int, minute: int = 0) -> datetime:
    return day.replace(hour=hour, minute=minute)


@pytest.mark.parametrize("hour, minute, peak", [
    (0, 59, False),
    (1, 0, True), (2, 30, True), (3, 59, True),
    (4, 0, False), (5, 59, False),
    (6, 0, True), (8, 0, True), (9, 59, True),
    (10, 0, False), (10, 30, False), (11, 30, False), (16, 0, False), (23, 59, False),
])
def test_the_published_windows_on_every_weekday(hour, minute, peak):
    for offset in range(5):  # Monday to Friday
        day = MONDAY + timedelta(days=offset)
        assert is_deepseek_peak(_at(day, hour, minute)) is peak, (day.strftime("%A"), hour, minute)


def test_weekends_are_never_peak():
    for offset in (5, 6):  # Saturday, Sunday
        day = MONDAY + timedelta(days=offset)
        for hour in range(24):
            assert not is_deepseek_peak(_at(day, hour)), (day.strftime("%A"), hour)


def test_the_weekday_is_the_utc_one():
    # Friday 22:30 in New York (EDT) is Saturday 02:30 UTC: off-peak.
    friday_night = datetime(2026, 10, 9, 22, 30, tzinfo=NY)
    assert friday_night.astimezone(UTC).weekday() == 5
    assert not is_deepseek_peak(friday_night)
    # Sunday 22:30 in New York (EDT) is Monday 02:30 UTC: peak.
    assert is_deepseek_peak(datetime(2026, 10, 11, 22, 30, tzinfo=NY))


def test_a_naive_moment_is_read_as_utc():
    assert is_deepseek_peak(datetime(2026, 10, 5, 8, 0))
    assert not is_deepseek_peak(datetime(2026, 10, 5, 10, 30))


def test_the_night_run_time_is_off_peak_in_edt_and_est():
    """06:30 New York: 10:30 UTC under EDT, 11:30 UTC under EST."""
    edt = datetime(2026, 10, 5, 6, 30, tzinfo=NY)   # Monday, EDT
    est = datetime(2026, 11, 2, 6, 30, tzinfo=NY)   # Monday, after the Nov 1 change
    assert edt.astimezone(UTC).hour == 10 and est.astimezone(UTC).hour == 11
    assert not is_deepseek_peak(edt)
    assert not is_deepseek_peak(est)


def test_the_old_times_were_peak():
    """04:00 New York was 08:00/09:00 UTC: peak in both seasons. 01:00 was
    ruled out because it is peak once the clocks change (06:00 UTC)."""
    assert is_deepseek_peak(datetime(2026, 10, 5, 4, 0, tzinfo=NY))
    assert is_deepseek_peak(datetime(2026, 11, 2, 4, 0, tzinfo=NY))
    assert not is_deepseek_peak(datetime(2026, 10, 5, 1, 0, tzinfo=NY))  # EDT: 05:00 UTC
    assert is_deepseek_peak(datetime(2026, 11, 2, 1, 0, tzinfo=NY))      # EST: 06:00 UTC


def test_peak_ends_at():
    assert peak_ends_at(_at(MONDAY, 2, 15)) == _at(MONDAY, 4, 0)
    assert peak_ends_at(_at(MONDAY, 6, 0)) == _at(MONDAY, 10, 0)
    assert peak_ends_at(_at(MONDAY, 9, 59)) == _at(MONDAY, 10, 0)
    # already off-peak: the moment itself
    assert peak_ends_at(_at(MONDAY, 4, 0)) == _at(MONDAY, 4, 0)
    saturday = MONDAY + timedelta(days=5)
    assert peak_ends_at(_at(saturday, 8, 0)) == _at(saturday, 8, 0)
    # an aware local moment comes back in UTC
    local = datetime(2026, 10, 5, 4, 12, tzinfo=NY)  # 08:12 UTC
    assert peak_ends_at(local) == _at(MONDAY, 10, 0)
