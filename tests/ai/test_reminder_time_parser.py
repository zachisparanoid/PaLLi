from __future__ import annotations

from datetime import datetime as real_datetime

import pytest
import pytz

from core.reminders.parser import ReminderTimeParseResult, ReminderTimeParser

NY_TZ = pytz.timezone("America/New_York")


class FixedDateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        base = NY_TZ.localize(real_datetime(2026, 3, 22, 12, 43, 55, 110398))
        if tz is None:
            return base
        return base.astimezone(tz)


@pytest.mark.asyncio
async def test_parse_time_expression_result_uses_injected_clock_and_timezone():
    parser = ReminderTimeParser()

    async def fake_resolve_timezone(user_id: int, platform: str):
        assert user_id == 1
        assert platform == "discord"
        return NY_TZ

    result = await parser.parse_time_expression_result(
        "at 1PM",
        user_id=1,
        platform="discord",
        resolve_user_timezone=fake_resolve_timezone,
        datetime_cls=FixedDateTime,
    )

    assert result.status == "ok"
    assert result.reason_code == ""
    assert result.timestamp is not None
    rendered = real_datetime.fromtimestamp(result.timestamp, tz=NY_TZ)
    assert (rendered.hour, rendered.minute, rendered.second) == (13, 0, 0)


@pytest.mark.asyncio
async def test_parse_time_expression_result_flags_ambiguous_bare_hour():
    parser = ReminderTimeParser()

    result = await parser.parse_time_expression_result(
        "monday at 8",
        datetime_cls=FixedDateTime,
    )

    assert result.status == "ambiguous"
    assert result.reason_code == "ambiguous_bare_hour"
    assert result.timestamp is None


@pytest.mark.asyncio
async def test_parse_time_expression_result_supports_fractional_relative_minutes():
    parser = ReminderTimeParser()

    result = await parser.parse_time_expression_result(
        "4 and a half minutes",
        datetime_cls=FixedDateTime,
    )

    assert result.status == "ok"
    assert result.timestamp is not None
    assert int(round(result.timestamp - FixedDateTime.now().timestamp())) == 270


def test_build_reminder_clarification_instruction_is_reason_aware():
    parser = ReminderTimeParser()

    note = parser.build_reminder_clarification_instruction(
        time_expression="today at 2pm",
        parse_result=ReminderTimeParseResult(
            status="ambiguous",
            timestamp=None,
            reason_code="past_same_day",
        ),
        kind="reminder",
    )

    assert "already passed today" in note
    assert "Do not claim the reminder was scheduled" in note
