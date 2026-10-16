"""Tests for natural-language time parsing in the reminder time parser."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from core.reminders.parser import ReminderTimeParser


@pytest.fixture
def parser():
    return ReminderTimeParser()


@pytest.fixture
def fixed_now():
    """Mid-afternoon Tuesday so 'this evening' is in the future."""
    return datetime(2026, 5, 6, 14, 30, 0, tzinfo=timezone.utc)


def test_resolve_tonight_returns_today_9pm(parser, fixed_now):
    result = parser._resolve_natural_time_phrase("set a reminder for tonight to lock the door", now=fixed_now)
    assert result is not None
    assert result.date() == fixed_now.date()
    assert result.hour == 21
    assert result.minute == 0


def test_resolve_this_evening_returns_today_7pm(parser, fixed_now):
    result = parser._resolve_natural_time_phrase("ping me this evening about dinner", now=fixed_now)
    assert result is not None
    assert result.date() == fixed_now.date()
    assert result.hour == 19


def test_resolve_this_morning_returns_today_9am(parser, fixed_now):
    """Even though 9am is past at fixed_now, the resolver returns the
    intended datetime; caller (parse_time_expression_result) downgrades
    past-times to ambiguous."""
    result = parser._resolve_natural_time_phrase("this morning's check-in", now=fixed_now)
    assert result is not None
    assert result.hour == 9


def test_resolve_tomorrow_morning_picks_tomorrow_9am(parser, fixed_now):
    result = parser._resolve_natural_time_phrase("remind me tomorrow morning to call mom", now=fixed_now)
    assert result is not None
    assert result.date() == (fixed_now.date() + timedelta(days=1))
    assert result.hour == 9


def test_resolve_tomorrow_night_picks_tomorrow_9pm(parser, fixed_now):
    result = parser._resolve_natural_time_phrase("ping tomorrow night to check the workshops", now=fixed_now)
    assert result is not None
    assert result.date() == (fixed_now.date() + timedelta(days=1))
    assert result.hour == 21


def test_resolve_noon(parser, fixed_now):
    result = parser._resolve_natural_time_phrase("at noon", now=fixed_now)
    assert result is not None
    assert result.hour == 12
    assert result.minute == 0


def test_resolve_no_match_returns_none(parser, fixed_now):
    result = parser._resolve_natural_time_phrase("at 3:42 pm sharp", now=fixed_now)
    assert result is None


def test_resolve_longest_phrase_wins(parser, fixed_now):
    """'tomorrow evening' must be picked over 'tomorrow' alone."""
    result = parser._resolve_natural_time_phrase("tomorrow evening", now=fixed_now)
    assert result is not None
    assert result.hour == 19  # evening, not 9 (morning)


@pytest.mark.asyncio
async def test_full_parse_handles_tonight(parser):
    """End-to-end: 'tonight' should produce a valid future timestamp."""
    result = await parser.parse_time_expression_result("tonight")
    # Result depends on current time of day, but should not be parse_failed
    assert result.status in ("ok", "ambiguous"), (
        f"expected ok/ambiguous; got {result.status} reason={result.reason_code}"
    )


@pytest.mark.asyncio
async def test_full_parse_handles_this_evening(parser):
    result = await parser.parse_time_expression_result("this evening")
    assert result.status in ("ok", "ambiguous")


@pytest.mark.asyncio
async def test_full_parse_handles_tomorrow_morning(parser):
    """Tomorrow morning is always in the future, should always parse ok."""
    result = await parser.parse_time_expression_result("tomorrow morning")
    assert result.status == "ok"
    assert result.timestamp is not None


# ---------------------------------------------------------------------------
# 2026-07-10 live bug: the natural-phrase map matched bare "tomorrow" by
# substring BEFORE the explicit-clock branches ran, so "tomorrow at 12:00 PM"
# was silently scheduled for 09:00 (the bare-"tomorrow" default). An explicit
# clock reading in the text must defer to the clock-aware branches.
# ---------------------------------------------------------------------------


def test_resolve_defers_when_explicit_ampm_clock_present(parser, fixed_now):
    """'tomorrow at 12:00 pm' must NOT resolve via the bare-'tomorrow' 9am
    default — the phrase resolver should stand aside for the clock branch."""
    result = parser._resolve_natural_time_phrase(
        "remind me tomorrow at 12:00 pm to go take the sign down", now=fixed_now,
    )
    assert result is None


def test_resolve_defers_when_24h_clock_present(parser, fixed_now):
    result = parser._resolve_natural_time_phrase(
        "tomorrow 18:30 dinner prep", now=fixed_now,
    )
    assert result is None


def test_resolve_still_matches_bare_phrases_without_clock(parser, fixed_now):
    """No explicit clock → the phrase map still applies (tonight → 21:00)."""
    result = parser._resolve_natural_time_phrase("tonight", now=fixed_now)
    assert result is not None
    assert result.hour == 21


@pytest.mark.asyncio
async def test_full_parse_tomorrow_at_noon_clock_wins(parser):
    """End-to-end: 'tomorrow at 12:00 PM' schedules 12:00 tomorrow, not 09:00."""
    from datetime import datetime as _dt, timedelta as _td
    result = await parser.parse_time_expression_result("tomorrow at 12:00 PM")
    assert result.status == "ok", f"got {result.status} reason={result.reason_code}"
    scheduled = _dt.fromtimestamp(result.timestamp).astimezone()
    expected_date = (_dt.now().astimezone() + _td(days=1)).date()
    assert scheduled.date() == expected_date
    assert (scheduled.hour, scheduled.minute) == (12, 0), (
        f"clock component lost: scheduled {scheduled.isoformat()}"
    )
