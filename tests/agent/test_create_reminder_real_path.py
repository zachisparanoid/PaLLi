"""create_reminder through the REAL scheduler chain (2026-07-10 live bug).

Every earlier test mocked ``parse_and_schedule_reminder`` wholesale, which hid
the real failure: the builtin tool passed NO analysis dict, the binding fell
back to the deprecated ``_analyze_deferred_request`` stub (reminder analysis
was absorbed into the retired pipeline's decide.py — C1.3), the stub returns
``is_deferred_request: False``, and ``prepare_deferred_schedule_request``
returned None for EVERY input. Live symptom (Discord, 2026-07-10): nine
different ``when`` formats — including plain ISO — all answered
"scheduler couldn't parse", and PaLLi fell back to installing a useless
crontab entry.

These tests run the tool against the real scheduler singleton, real binding,
real time parser, and the (isolated) test DB. Only the LLM-adjacent stub and
network paths are absent — none are needed: the tool pre-bakes its analysis.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

from tests.agent.conftest import load_module


def _tool():
    return load_module(
        "core/agent/builtin_tools/create_reminder.py", "create_reminder_real_path_test",
    ).CreateReminderTool()


def _ctx():
    # Fix plan 5.8: a chat surface carries its chat, or the reminder has no
    # door of its own (without one the tool asks for the chat he used last,
    # and refuses when there is none).
    from core.agent.tools import ToolContext
    return ToolContext(
        turn_owner_id=1, is_owner=True, surface="discord_text",
        channel_id=100000000000000001,
    )


def test_create_reminder_tomorrow_noon_schedules_for_real():
    """The exact live failure: when='tomorrow at 12:00 PM' must schedule,
    not report 'couldn't parse'."""
    tool = _tool()
    expected_day = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    result = asyncio.run(tool.execute(
        {"message": "Go take the sign down in the woods", "when": "tomorrow at 12:00 PM"},
        _ctx(),
    ))

    assert "couldn't parse" not in result, f"tool still failing: {result!r}"
    assert "scheduled for" in result, f"unexpected tool reply: {result!r}"
    # Scheduled for tomorrow at noon, host-local.
    assert expected_day in result, f"wrong day in: {result!r}"
    assert re.search(r"\b12:00\b", result), f"wrong time in: {result!r}"


def test_create_reminder_explicit_datetime_schedules_for_real():
    """An explicit future datetime (the dateutil branch) must also work.

    The date is dynamic (+10 days) to stay in the SAME DST regime as today:
    the parser attaches the CURRENT fixed utc-offset to explicit dates, so a
    cross-DST-boundary date drifts by the DST delta (pre-existing, reported
    2026-07-10, deliberately not fixed here).
    """
    tool = _tool()
    when = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d") + " 14:30"

    result = asyncio.run(tool.execute(
        {"message": "far-future probe", "when": when},
        _ctx(),
    ))

    assert "couldn't parse" not in result, f"tool still failing: {result!r}"
    assert "scheduled for" in result, f"unexpected tool reply: {result!r}"
    assert when in result, f"wrong schedule in: {result!r}"


def test_create_reminder_carries_channel_address():
    """Tool-created reminders must carry the asking channel's address —
    channel_id + surface_id — or delivery has nowhere to send them
    (channel_id=0 / surface_id='' was undeliverable)."""
    import asyncio as _asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.agent.tools import ToolContext

    tool = _tool()
    captured: dict = {}

    async def _fake_parse(*, user_id, platform, channel_id, message, **rest):
        captured.update({"channel_id": channel_id, **rest})
        result = MagicMock()
        result.id = 9
        result.scheduled_for = 1800000000.0
        result.message = message
        return result

    scheduler = MagicMock()
    scheduler.parse_and_schedule_reminder = AsyncMock(side_effect=_fake_parse)

    ctx = ToolContext(
        turn_owner_id=1, is_owner=True, surface="discord_text",
        channel_id=100000000000000001,
    )
    with patch("core.reminders.scheduler.reminder_scheduler", scheduler):
        _asyncio.run(tool.execute(
            {"message": "feed cat", "when": "tomorrow at 9:00 am"}, ctx,
        ))

    assert captured["channel_id"] == 100000000000000001
    assert captured["surface_id"] == "100000000000000001"
