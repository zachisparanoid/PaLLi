"""Unit tests for core/turn/temporal_format.py.

Coverage targets:
- format_relative_time across the boundary cases (just now, minutes,
  hours, time-of-day buckets, day labels, week buckets, explicit dates)
- format_message_with_temporal_context with and without previous_sent_at
- Cross-midnight handling
- Clock skew protection (future timestamps don't render negative)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.turn.temporal_format import (
    format_message_with_temporal_context,
    format_relative_time,
)


# ---------------------------------------------------------------------------
# format_relative_time
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 4, 14, 30, 0)  # Monday afternoon, 2:30pm


def test_just_now_under_90s():
    assert format_relative_time(NOW - timedelta(seconds=10), NOW) == "just now"
    assert format_relative_time(NOW - timedelta(seconds=89), NOW) == "just now"


def test_few_minutes_under_5m():
    assert format_relative_time(NOW - timedelta(minutes=2), NOW) == "a few minutes ago"
    assert format_relative_time(NOW - timedelta(minutes=4), NOW) == "a few minutes ago"


def test_explicit_minutes_under_60m():
    assert format_relative_time(NOW - timedelta(minutes=23), NOW) == "23 minutes ago"
    assert format_relative_time(NOW - timedelta(minutes=59), NOW) == "59 minutes ago"


def test_about_hours_under_3h():
    assert format_relative_time(NOW - timedelta(hours=1), NOW) == "about 1 hour ago"
    assert format_relative_time(NOW - timedelta(hours=2), NOW) == "about 2 hours ago"


def test_same_day_3_to_12_hours():
    # NOW is 14:30. 7 hours back = 7:30am same day.
    when = NOW - timedelta(hours=7)
    result = format_relative_time(when, NOW)
    assert "earlier" in result
    assert "morning" in result


def test_yesterday_with_bucket():
    # 14 hours back from 2:30pm Monday = 12:30am Monday → still today
    # actually: 14h before 2:30pm Monday = 12:30am Monday. Let's go 18h back.
    # 2:30pm Monday - 18h = 8:30pm Sunday.
    when = NOW - timedelta(hours=18)
    result = format_relative_time(when, NOW)
    assert "yesterday" in result.lower()
    # should be evening since 8:30pm
    assert "evening" in result.lower() or "tonight" in result.lower()


def test_yesterday_morning():
    # 2:30pm Monday minus 30 hours = 8:30am Sunday
    when = NOW - timedelta(hours=30)
    result = format_relative_time(when, NOW)
    # 30 hours back is technically two calendar days; check that we get
    # a reasonable day label
    assert "Sunday" in result or "yesterday" in result.lower()


def test_three_days_ago():
    when = NOW - timedelta(days=3)
    result = format_relative_time(when, NOW)
    assert "Friday" in result  # Monday - 3 days = Friday


def test_last_week():
    when = NOW - timedelta(days=10)
    result = format_relative_time(when, NOW)
    assert "last" in result.lower()


def test_old_explicit_date():
    when = NOW - timedelta(days=30)
    result = format_relative_time(when, NOW)
    # Apr 4 — depends on platform's strftime
    assert "Apr" in result


def test_clock_skew_future_timestamp():
    when = NOW + timedelta(minutes=5)
    assert format_relative_time(when, NOW) == "moments ago"


def test_none_input():
    assert format_relative_time(None, NOW) == "(unknown time)"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# format_message_with_temporal_context
# ---------------------------------------------------------------------------


def test_first_message_has_no_gap_suffix():
    sent_at = NOW - timedelta(minutes=10)
    result = format_message_with_temporal_context(
        role="user", content="hey", sent_at=sent_at, now=NOW,
        previous_sent_at=None,
    )
    assert "user" in result
    assert "hey" in result
    assert "later" not in result  # no gap suffix on first row


def test_subsequent_message_has_gap_suffix():
    prev = NOW - timedelta(hours=8)
    sent = NOW - timedelta(minutes=10)
    result = format_message_with_temporal_context(
        role="assistant", content="okay", sent_at=sent, now=NOW,
        previous_sent_at=prev,
    )
    assert "later" in result or "hour" in result


def test_close_messages_get_no_annotation():
    """Sub-5-second gaps are effectively contiguous; no annotation."""
    prev = NOW - timedelta(seconds=15)
    sent = NOW - timedelta(seconds=14)
    result = format_message_with_temporal_context(
        role="user", content="ok", sent_at=sent, now=NOW,
        previous_sent_at=prev,
    )
    # 1s gap should be no annotation
    assert "later" not in result


def test_short_gap_gets_a_moment_later():
    """5-90 second gaps render as 'a moment later'."""
    prev = NOW - timedelta(seconds=30)
    sent = NOW - timedelta(seconds=10)
    result = format_message_with_temporal_context(
        role="user", content="ok", sent_at=sent, now=NOW,
        previous_sent_at=prev,
    )
    assert "a moment later" in result


def test_long_gap_renders_days_later():
    prev = NOW - timedelta(days=5, hours=2)
    sent = NOW - timedelta(hours=2)
    result = format_message_with_temporal_context(
        role="user", content="back", sent_at=sent, now=NOW,
        previous_sent_at=prev,
    )
    assert "days later" in result


def test_today_message_uses_just_clock():
    sent_at = NOW - timedelta(minutes=30)
    result = format_message_with_temporal_context(
        role="user", content="hi", sent_at=sent_at, now=NOW,
        previous_sent_at=None,
    )
    # Today messages should NOT include the day-of-week prefix
    assert "Mon " not in result


def test_yesterday_message_includes_day():
    sent_at = NOW - timedelta(days=1, hours=2)
    result = format_message_with_temporal_context(
        role="user", content="last night thing", sent_at=sent_at, now=NOW,
        previous_sent_at=None,
    )
    # Should include day-of-week (Sun) since it's not today
    assert "Sun" in result or "Sunday" in result


def test_old_message_includes_full_date():
    sent_at = NOW - timedelta(days=15)
    result = format_message_with_temporal_context(
        role="user", content="ancient", sent_at=sent_at, now=NOW,
        previous_sent_at=None,
    )
    # Should include month name since older than a week
    assert "Apr" in result or "Mar" in result


def test_none_sent_at_falls_back_gracefully():
    result = format_message_with_temporal_context(
        role="user", content="huh", sent_at=None, now=NOW,  # type: ignore[arg-type]
        previous_sent_at=None,
    )
    assert "user" in result
    assert "huh" in result
