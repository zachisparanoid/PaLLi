"""Natural-language temporal framing for conversation context.

The decide LLM and respond_tier0 both need to convey to the response
model how much time has elapsed between messages, but raw timestamps
buried in conversation history don't get used reliably — the model
won't do the arithmetic to notice that a turn happened 9 hours ago.

This module renders deltas in the way humans actually parse time in
conversation: "just now", "yesterday morning", "earlier this
afternoon", "last week". The model reads the prose; no arithmetic
required.

Two public helpers:

- ``format_relative_time(when, now)`` — "9 hours ago" / "yesterday
  evening" / "just now"-style framing for a single timestamp.
- ``format_message_with_temporal_context(role, content, sent_at,
  now, previous_sent_at)`` — full one-line annotation of one message
  for prompt rendering.

Pure functions. No side effects. Easy to unit-test.

Plan: docs/specs/2026-05-04-temporal-awareness-and-session-boundaries-plan.md
Workstream A.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


# Time-of-day buckets. Tunable but informed by typical waking-hours
# language: "morning" runs from sunrise-ish to lunch, "afternoon" runs
# from lunch to dusk, "evening" runs to ~10pm, "late night" / "early
# morning" cover the small hours.
def _time_of_day_bucket(when: datetime) -> str:
    h = when.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 21:
        return "evening"
    if 21 <= h < 24:
        return "tonight"
    # 0-5: wee hours
    return "early morning"


def _day_label(when: datetime, now: datetime) -> str:
    """Return 'today' / 'yesterday' / 'Sunday' / explicit date."""
    when_date = when.date()
    now_date = now.date()
    delta_days = (now_date - when_date).days
    if delta_days == 0:
        return "today"
    if delta_days == 1:
        return "yesterday"
    if 2 <= delta_days <= 6:
        return when.strftime("%A")  # "Sunday"
    if 7 <= delta_days <= 13:
        return "last " + when.strftime("%A")
    if 14 <= delta_days <= 21:
        return "two weeks ago"
    return when.strftime("%b %d")  # "Apr 18"


def format_relative_time(when: datetime, now: datetime) -> str:
    """Render a natural-language relative-time string.

    Examples:
        - now - 30s        → "just now"
        - now - 4 minutes  → "a few minutes ago"
        - now - 23 minutes → "23 minutes ago"
        - now - 2 hours    → "about 2 hours ago"
        - now - 7 hours    → "earlier today"
        - now - 14 hours   → "yesterday evening"
        - now - 3 days     → "Sunday afternoon"
        - now - 10 days    → "last Friday"
        - now - 30 days    → "Apr 4"

    Falls back gracefully for clock skew (when > now) → "moments ago".
    """
    if when is None:
        return "(unknown time)"
    delta: timedelta = now - when

    # Clock skew protection: future timestamps shouldn't render as
    # negative time; treat as "just now."
    if delta.total_seconds() < 0:
        return "moments ago"

    seconds = delta.total_seconds()

    # Sub-minute
    if seconds < 90:
        return "just now"

    # Minutes
    if seconds < 5 * 60:
        return "a few minutes ago"
    if seconds < 60 * 60:
        minutes = int(seconds // 60)
        return f"{minutes} minutes ago"

    # Hours
    if seconds < 3 * 60 * 60:
        hours = int(seconds // 3600)
        return f"about {hours} hour{'s' if hours != 1 else ''} ago"

    # Same calendar day, 3-12 hours ago: use time-of-day bucket
    if when.date() == now.date() and seconds < 12 * 60 * 60:
        bucket = _time_of_day_bucket(when)
        return f"earlier {bucket}" if bucket != "early morning" else "earlier this morning"

    # 12-24 hours: cross midnight or wraps; use day label + bucket
    if seconds < 24 * 60 * 60:
        day = _day_label(when, now)
        bucket = _time_of_day_bucket(when)
        if day == "yesterday":
            return f"yesterday {bucket}"
        if day == "today":
            return f"earlier {bucket}"
        return f"{day} {bucket}"

    # 1-7 days
    if delta.days <= 6:
        day = _day_label(when, now)
        bucket = _time_of_day_bucket(when)
        return f"{day} {bucket}"

    # 7-21 days
    if delta.days <= 21:
        return _day_label(when, now)

    # 21+ days: explicit date
    return when.strftime("%b %d")


def _gap_label(seconds: float) -> str:
    """Render the gap between two messages as compact prose.

    Used as the second annotation in
    format_message_with_temporal_context. Examples:
        - 0-5s     → ""  (no annotation; messages are effectively
                          contiguous)
        - 5-90s    → "1 minute later"
        - 1-60min  → "23 minutes later"
        - 1-3h     → "about 2 hours later"
        - 3-12h    → "8 hours later"
        - 12-72h   → "1 day later"
        - 72h+     → "5 days later"
    """
    if seconds < 0:
        return ""
    if seconds < 5:
        return ""
    if seconds < 90:
        return "a moment later"
    if seconds < 60 * 60:
        minutes = max(1, int(seconds // 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''} later"
    if seconds < 3 * 60 * 60:
        hours = int(seconds // 3600)
        return f"about {hours} hour{'s' if hours != 1 else ''} later"
    if seconds < 12 * 60 * 60:
        hours = int(seconds // 3600)
        return f"{hours} hours later"
    if seconds < 72 * 60 * 60:
        days = max(1, int(seconds // (24 * 60 * 60)))
        return f"{days} day{'s' if days != 1 else ''} later"
    days = int(seconds // (24 * 60 * 60))
    return f"{days} days later"


def format_message_with_temporal_context(
    role: str,
    content: str,
    sent_at: datetime,
    now: datetime,
    previous_sent_at: Optional[datetime] = None,
) -> str:
    """Render one conversation row with absolute + relative timing.

    Format:
        user (Mon 2:14pm, 8 hours later): <content>
        assistant (Mon 2:15pm, a moment later): <content>

    When previous_sent_at is None (first message in the rendered
    history), the gap suffix is dropped:
        user (Mon 2:14pm): <content>

    The absolute time is "Day Hour:Min am/pm" — short enough not to
    dominate the line. Day-of-week prefix omitted when sent_at is
    today (saves visual noise on dense in-session histories).
    """
    if sent_at is None:
        return f"{role}: {content}"

    # Absolute time. If today, just clock; otherwise day + clock.
    if sent_at.date() == now.date():
        abs_str = sent_at.strftime("%-I:%M%p" if hasattr(sent_at, "strftime") else "%H:%M").lower() if _supports_dash_strftime() else sent_at.strftime("%I:%M%p").lstrip("0").lower()
    else:
        # "Mon 2:14pm" or "Apr 18 2:14pm" if older than a week
        delta_days = (now.date() - sent_at.date()).days
        if delta_days <= 6:
            day_part = sent_at.strftime("%a")
        else:
            day_part = sent_at.strftime("%b %-d") if _supports_dash_strftime() else sent_at.strftime("%b %d").lstrip("0")
        time_part = sent_at.strftime("%I:%M%p").lstrip("0").lower()
        abs_str = f"{day_part} {time_part}"

    # Relative gap suffix
    suffix = ""
    if previous_sent_at is not None:
        gap_seconds = (sent_at - previous_sent_at).total_seconds()
        gap_label = _gap_label(gap_seconds)
        if gap_label:
            suffix = f", {gap_label}"

    return f"{role} ({abs_str}{suffix}): {content}"


def _supports_dash_strftime() -> bool:
    """``%-I`` / ``%-d`` works on POSIX but not Windows (where ``%#I`` is
    the equivalent). Detect once and cache. The ``.lstrip("0")`` fallback
    handles cross-platform formatting without a dedicated branch in every
    call site.
    """
    global _DASH_STRFTIME_CACHE
    if _DASH_STRFTIME_CACHE is not None:
        return _DASH_STRFTIME_CACHE
    try:
        datetime(2024, 1, 5, 9, 0).strftime("%-I")
        _DASH_STRFTIME_CACHE = True
    except ValueError:
        _DASH_STRFTIME_CACHE = False
    return _DASH_STRFTIME_CACHE


_DASH_STRFTIME_CACHE: Optional[bool] = None
