"""A tool that catches its own failure still says it FAILED (week 2, the final
review's I3).

Task 1 gave the shell, the file reader and the artifact deliverer the
``ToolFailure`` return, but five tools were left handling their own failures
and returning a plain string: her workbench shell (a refusal, a timeout, a
shell that will not start, a non-zero exit) and the three reminder tools and
web_search (``except Exception: return f"… failed: {exc}"``). Each of those
reached ``ToolRegistry.dispatch`` as ``is_error=False`` — a success, to the
error streak that wakes her on a second failure and to the record of her
deeds.

One failing path per touched file, pinned by TYPE: reading the text through
``str()`` accepts either type, so only ``isinstance`` catches a reversion.
The wording is asserted alongside it, because the whole point is that the
words did not change.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext


def _ctx() -> ToolContext:
    return ToolContext(turn_owner_id=1, is_owner=True, surface="dashboard")


# --- her workbench shell (core/agent/builtin_tools/workbench_shell.py) ------


def _shell(monkeypatch, tmp_path):
    # Direct mode + a throwaway workspace, the way tests/agent/test_workbench_shell.py
    # runs it: no sudo, no palli-work user, nothing outside tmp_path touched.
    monkeypatch.setenv("WORKBENCH_SHELL_DIRECT", "1")
    monkeypatch.setenv("WORKBENCH_ROOT", str(tmp_path))
    from core.agent.builtin_tools.workbench_shell import WorkbenchShellTool

    return WorkbenchShellTool()


def test_workbench_shell_nonzero_exit_is_a_failure(monkeypatch, tmp_path):
    """A real subprocess, a real non-zero exit — the branch that carries the
    ``(exit N)`` suffix she reads."""
    raw = asyncio.run(_shell(monkeypatch, tmp_path).execute({"command": "exit 3"}, _ctx()))
    assert isinstance(raw, ToolFailure)
    assert "(exit 3)" in str(raw)


def test_workbench_shell_refusal_is_a_failure(monkeypatch, tmp_path):
    """The belt declines before anything runs, and a refusal is a call that
    ran and did not work — the same rule local_shell's guard follows."""
    raw = asyncio.run(_shell(monkeypatch, tmp_path).execute({"command": "sudo whoami"}, _ctx()))
    assert isinstance(raw, ToolFailure)
    assert "declined" in str(raw).lower()


def test_workbench_shell_exit_zero_is_still_a_plain_string(monkeypatch, tmp_path):
    """The other half of the contract: a command that worked is not an error."""
    raw = asyncio.run(_shell(monkeypatch, tmp_path).execute({"command": "echo ok"}, _ctx()))
    assert isinstance(raw, str)
    assert raw.strip() == "ok"


# --- the reminder tools -----------------------------------------------------


async def _boom(*args, **kwargs):
    raise RuntimeError("store is down")


def test_create_reminder_scheduler_failure_is_a_failure(monkeypatch):
    import core.agent.builtin_tools.create_reminder as create_reminder_module
    from core.agent.builtin_tools.create_reminder import CreateReminderTool
    from core.reminders import scheduler as scheduler_module

    # Fix plan 5.8: a dashboard reminder is filed to the chat he used last,
    # and with no chat at all the tool refuses before the scheduler runs. Give
    # it a chat, so the call reaches the scheduler this test breaks.
    async def _last_used_door(owner_id):
        return ("discord", 1483)

    monkeypatch.setattr(create_reminder_module, "last_used_door", _last_used_door)
    monkeypatch.setattr(
        scheduler_module.reminder_scheduler, "parse_and_schedule_reminder", _boom,
    )
    raw = asyncio.run(CreateReminderTool().execute(
        {"message": "water the plants", "when": "in 2 hours"}, _ctx(),
    ))
    assert isinstance(raw, ToolFailure)
    assert str(raw) == "create_reminder failed: RuntimeError: store is down"


def test_cancel_reminder_store_failure_is_a_failure(monkeypatch):
    from core.agent.builtin_tools.cancel_reminder import CancelReminderTool
    from core.database import brain

    monkeypatch.setattr(brain, "mark_scheduled_reminder_completed", _boom)
    raw = asyncio.run(CancelReminderTool().execute({"reminder_id": 7}, _ctx()))
    assert isinstance(raw, ToolFailure)
    assert str(raw) == "cancel_reminder failed: RuntimeError: store is down"


def test_reschedule_reminder_store_failure_is_a_failure(monkeypatch):
    from core.agent.builtin_tools.reschedule_reminder import RescheduleReminderTool
    from core.database import brain

    # The time parses (the real parser, as the live path uses it); the WRITE is
    # what fails, so the reminder is still at its old time.
    monkeypatch.setattr(brain, "update_scheduled_reminder_schedule", _boom)
    raw = asyncio.run(RescheduleReminderTool().execute(
        {"reminder_id": 7, "new_time": "in 2 hours"}, _ctx(),
    ))
    assert isinstance(raw, ToolFailure)
    assert str(raw) == "reschedule_reminder failed: RuntimeError: store is down"


# --- web_search -------------------------------------------------------------


def test_web_search_fetch_failure_is_a_failure():
    from core.agent.builtin_tools.web_search import WebSearchTool

    tool = WebSearchTool()
    client = SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("exa is down")))
    with patch(
        "ai.web_search.web_search_engine", SimpleNamespace(exa_client=client),
    ), patch.object(
        type(tool), "_api_key_present", staticmethod(lambda: True),
    ):
        raw = asyncio.run(tool.execute({"query": "macaw cognition"}, ctx=None))

    assert isinstance(raw, ToolFailure)
    assert str(raw) == "web search failed: exa is down"
