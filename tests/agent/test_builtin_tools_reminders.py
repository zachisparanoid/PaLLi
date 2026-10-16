from __future__ import annotations

import asyncio


def _load(name):
    from tests.agent.conftest import load_module
    return load_module(f"core/agent/builtin_tools/{name}.py", f"{name}_tool_test")


def test_list_reminders_tool_shape():
    tool = _load("list_reminders").ListRemindersTool()
    assert tool.name == "list_reminders"
    assert tool.input_schema["type"] == "object"


def test_cancel_reminder_tool_shape():
    tool = _load("cancel_reminder").CancelReminderTool()
    assert tool.name == "cancel_reminder"
    assert "reminder_id" in tool.input_schema["properties"]


def test_reschedule_reminder_tool_shape():
    tool = _load("reschedule_reminder").RescheduleReminderTool()
    assert tool.name == "reschedule_reminder"
    assert "reminder_id" in tool.input_schema["properties"]
    assert "new_time" in tool.input_schema["properties"]


def test_create_reminder_tool_shape():
    tool = _load("create_reminder").CreateReminderTool()
    assert tool.name == "create_reminder"
    assert "message" in tool.input_schema["properties"]
    assert "when" in tool.input_schema["properties"]
