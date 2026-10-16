"""Surface→platform routing for agent reminder tools.

Phase B / B3 step 3 fix for RA-MED-6, RA-MED-7, QUE-CRIT-2:
- ``create_reminder`` agent tool used to hardcode ``platform="dashboard"``.
- ``reschedule_reminder`` agent tool used to hardcode ``platform="dashboard"``.
- ``list_reminders`` agent tool used to probe only
  ``("dashboard", "discord", "voice")`` — never ``"local"``, leaving
  agent-created reminders on the local surface invisible.

The fix:
- ``ToolContext`` carries ``surface`` (plumbed from runner.py).
- ``create_reminder`` and ``reschedule_reminder`` map ``ctx.surface``
  through ``recall.platform_for_surface`` instead of hardcoding
  ``"dashboard"``.
- ``list_reminders`` probes ``("dashboard", "discord", "voice", "local")``.

These tests verify each surface dispatches the tool against the
expected platform.

Re-premised to the owner's rule (fix plan 5.8, 2026-09-28: "Last chat, else
refuse"): ``create_reminder`` no longer files a reminder under the surface's
platform. One made in a Discord or Telegram chat stays in that chat; one made
anywhere else (voice, the dashboard, an unknown surface) goes to the chat he
used last (``last_used_door``), so it is never filed as "voice"/"dashboard"
with channel 0 and nowhere to send. The reschedule and list tests below are
unchanged: those still read the surface's platform.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _load_tool(name):
    from tests.agent.conftest import load_module
    return load_module(
        f"core/agent/builtin_tools/{name}.py", f"{name}_surface_routing_test",
    )


def _ctx_with_surface(surface: str, channel_id: int = 0):
    """Build a ToolContext with the given surface, using the canonical
    import — load_module would create a parallel ToolContext class and
    isinstance comparisons across the two would fail.
    """
    from core.agent.tools import ToolContext
    return ToolContext(
        turn_owner_id=1, is_owner=True, surface=surface, channel_id=channel_id,
    )


def _last_used_door(value):
    """An async stand-in for core.doors.last_used_door (the real one is async)."""
    async def _door(owner_id):
        return value
    return _door


# ---------------------------------------------------------------------------
# create_reminder
# ---------------------------------------------------------------------------


def test_create_reminder_dispatches_discord_text_as_discord():
    tool = _load_tool("create_reminder").CreateReminderTool()

    captured: list[dict] = []

    async def _fake_parse(*, user_id, platform, channel_id, message, **rest):
        captured.append({"platform": platform, "channel_id": channel_id})
        result = MagicMock()
        result.id = 1
        result.scheduled_for = 1700000000.0
        result.message = message
        return result

    scheduler = MagicMock()
    scheduler.parse_and_schedule_reminder = AsyncMock(side_effect=_fake_parse)

    with patch("core.reminders.scheduler.reminder_scheduler", scheduler):
        asyncio.run(tool.execute(
            {"message": "feed cat", "when": "tomorrow"},
            _ctx_with_surface("discord_text", channel_id=1483),
        ))

    # A reminder made in a Discord chat stays in that chat.
    assert captured[0]["platform"] == "discord"
    assert captured[0]["channel_id"] == 1483


def test_create_reminder_from_voice_local_files_to_the_last_used_chat(monkeypatch):
    module = _load_tool("create_reminder")
    monkeypatch.setattr(module, "last_used_door", _last_used_door(("telegram", 5222)))
    tool = module.CreateReminderTool()

    captured: list[dict] = []

    async def _fake_parse(*, user_id, platform, channel_id, message, **rest):
        captured.append({"platform": platform, "channel_id": channel_id, **rest})
        result = MagicMock()
        result.id = 2
        result.scheduled_for = 1700000000.0
        result.message = message
        return result

    scheduler = MagicMock()
    scheduler.parse_and_schedule_reminder = AsyncMock(side_effect=_fake_parse)

    with patch("core.reminders.scheduler.reminder_scheduler", scheduler):
        asyncio.run(tool.execute(
            {"message": "feed cat", "when": "tomorrow"},
            _ctx_with_surface("voice_local"),
        ))

    # The local mic is not a chat she can post in: the reminder goes to the
    # chat he used last (it used to be filed as "voice", channel 0).
    assert captured[0]["platform"] == "telegram"
    assert captured[0]["channel_id"] == 5222
    assert captured[0]["surface_id"] == "5222"


def test_create_reminder_from_the_dashboard_files_to_the_last_used_chat(monkeypatch):
    module = _load_tool("create_reminder")
    monkeypatch.setattr(module, "last_used_door", _last_used_door(("discord", 1483)))
    tool = module.CreateReminderTool()

    captured: list[dict] = []

    async def _fake_parse(*, user_id, platform, channel_id, message, **rest):
        captured.append({"platform": platform, "channel_id": channel_id, **rest})
        result = MagicMock()
        result.id = 3
        result.scheduled_for = 1700000000.0
        result.message = message
        return result

    scheduler = MagicMock()
    scheduler.parse_and_schedule_reminder = AsyncMock(side_effect=_fake_parse)

    with patch("core.reminders.scheduler.reminder_scheduler", scheduler):
        asyncio.run(tool.execute(
            {"message": "feed cat", "when": "tomorrow"},
            _ctx_with_surface("dashboard"),
        ))

    # The dashboard is not a chat she can post in (its buffer drops an
    # undrained message after five minutes): the chat he used last.
    assert captured[0]["platform"] == "discord"
    assert captured[0]["channel_id"] == 1483
    assert captured[0]["surface_id"] == "1483"


def test_create_reminder_unknown_surface_files_to_the_last_used_chat(monkeypatch):
    module = _load_tool("create_reminder")
    monkeypatch.setattr(module, "last_used_door", _last_used_door(("discord", 1483)))
    tool = module.CreateReminderTool()

    captured: list[dict] = []

    async def _fake_parse(*, user_id, platform, channel_id, message, **rest):
        captured.append({"platform": platform, "channel_id": channel_id, **rest})
        result = MagicMock()
        result.id = 4
        result.scheduled_for = 1700000000.0
        result.message = message
        return result

    scheduler = MagicMock()
    scheduler.parse_and_schedule_reminder = AsyncMock(side_effect=_fake_parse)

    with patch("core.reminders.scheduler.reminder_scheduler", scheduler):
        asyncio.run(tool.execute(
            {"message": "feed cat", "when": "tomorrow"},
            _ctx_with_surface(""),
        ))

    # An empty/unknown surface used to fall back to "dashboard" with
    # channel 0 — the black hole itself. It now goes to the chat he used last.
    assert captured[0]["platform"] == "discord"
    assert captured[0]["channel_id"] == 1483
    assert captured[0]["surface_id"] == "1483"


# ---------------------------------------------------------------------------
# reschedule_reminder
# ---------------------------------------------------------------------------


def test_reschedule_reminder_routes_discord_text_as_discord():
    tool = _load_tool("reschedule_reminder").RescheduleReminderTool()

    parser_calls: list[dict] = []

    async def fake_parse_time(expr, *, user_id, platform):
        parser_calls.append({"platform": platform})
        result = MagicMock()
        result.status = "ok"
        result.timestamp = 1700000000.0
        result.reason_code = ""
        return result

    parser_cls = MagicMock()
    parser_cls.return_value.parse_time_expression_result = AsyncMock(
        side_effect=fake_parse_time,
    )
    fake_brain = MagicMock()
    fake_brain.update_scheduled_reminder_schedule = AsyncMock(return_value=None)

    with patch("core.database.brain", fake_brain), patch(
        "core.reminders.parser.ReminderTimeParser", parser_cls,
    ):
        asyncio.run(tool.execute(
            {"reminder_id": 5, "new_time": "tomorrow"},
            _ctx_with_surface("discord_text"),
        ))

    assert parser_calls[0]["platform"] == "discord"


def test_reschedule_reminder_routes_voice_local_as_voice():
    tool = _load_tool("reschedule_reminder").RescheduleReminderTool()

    parser_calls: list[dict] = []

    async def fake_parse_time(expr, *, user_id, platform):
        parser_calls.append({"platform": platform})
        result = MagicMock()
        result.status = "ok"
        result.timestamp = 1700000000.0
        result.reason_code = ""
        return result

    parser_cls = MagicMock()
    parser_cls.return_value.parse_time_expression_result = AsyncMock(
        side_effect=fake_parse_time,
    )
    fake_brain = MagicMock()
    fake_brain.update_scheduled_reminder_schedule = AsyncMock(return_value=None)

    with patch("core.database.brain", fake_brain), patch(
        "core.reminders.parser.ReminderTimeParser", parser_cls,
    ):
        asyncio.run(tool.execute(
            {"reminder_id": 5, "new_time": "tomorrow"},
            _ctx_with_surface("voice_local"),
        ))

    assert parser_calls[0]["platform"] == "voice"


def test_reschedule_reminder_routes_dashboard_as_dashboard():
    tool = _load_tool("reschedule_reminder").RescheduleReminderTool()

    parser_calls: list[dict] = []

    async def fake_parse_time(expr, *, user_id, platform):
        parser_calls.append({"platform": platform})
        result = MagicMock()
        result.status = "ok"
        result.timestamp = 1700000000.0
        result.reason_code = ""
        return result

    parser_cls = MagicMock()
    parser_cls.return_value.parse_time_expression_result = AsyncMock(
        side_effect=fake_parse_time,
    )
    fake_brain = MagicMock()
    fake_brain.update_scheduled_reminder_schedule = AsyncMock(return_value=None)

    with patch("core.database.brain", fake_brain), patch(
        "core.reminders.parser.ReminderTimeParser", parser_cls,
    ):
        asyncio.run(tool.execute(
            {"reminder_id": 5, "new_time": "tomorrow"},
            _ctx_with_surface("dashboard"),
        ))

    assert parser_calls[0]["platform"] == "dashboard"


# ---------------------------------------------------------------------------
# list_reminders
# ---------------------------------------------------------------------------


def test_list_reminders_probes_local_platform():
    """QUE-CRIT-2: locally-created reminders must surface."""
    tool = _load_tool("list_reminders").ListRemindersTool()

    queried: list[str] = []

    async def fake_list(*, user_id, platform):
        queried.append(platform)
        if platform == "local":
            return [{
                "id": 9, "reminder_text": "stretch",
                "scheduled_time": 1700000000.0, "is_completed": False,
            }]
        return []

    fake_brain = MagicMock()
    fake_brain.list_user_scheduled_reminders = AsyncMock(side_effect=fake_list)

    with patch("core.database.brain", fake_brain):
        result = asyncio.run(tool.execute({}, _ctx_with_surface("voice_local")))

    assert "local" in queried
    assert "stretch" in result
