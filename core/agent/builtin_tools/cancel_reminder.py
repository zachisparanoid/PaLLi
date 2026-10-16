from __future__ import annotations

from typing import Any

from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext


class CancelReminderTool:
    name = "cancel_reminder"
    description = (
        "Cancel a scheduled reminder by id. Use list_reminders first to "
        "find the id. Returns a confirmation string or an error message."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "reminder_id": {
                "type": "integer",
                "description": "The numeric id of the reminder to cancel "
                "(from list_reminders).",
            },
        },
        "required": ["reminder_id"],
    }
    requires_approval = False
    owner_only = False

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        try:
            from core.database import brain
        except ImportError:
            return (
                "cancel_reminder: core.database.brain import failed — reminder "
                "store import path needs to be updated for this codebase."
            )

        if "reminder_id" not in input or input.get("reminder_id") is None:
            return "reminder_id is required"
        try:
            reminder_id = int(input.get("reminder_id"))
        except (TypeError, ValueError):
            return (
                f"cancel_reminder: invalid reminder_id "
                f"{input.get('reminder_id')!r} — must be an integer."
            )
        if reminder_id <= 0:
            return f"cancel_reminder: reminder_id must be > 0 (got {reminder_id})"

        try:
            rows_changed = await brain.mark_scheduled_reminder_completed(
                reminder_id=reminder_id, user_id=int(ctx.turn_owner_id),
            )
        except Exception as exc:
            # The store raised and this tool caught it (week 2, task 1): the
            # reminder is still live, so the result is an error, not a deed.
            return ToolFailure(f"cancel_reminder failed: {type(exc).__name__}: {exc}")

        # Batch 4 task 4 (2026-09-28): 0 rows changed means either the id
        # doesn't exist or it belongs to someone else — either way it was
        # never this caller's to cancel. A fake/mock returning something
        # that isn't an int (e.g. None) reads the same way, fail closed.
        if not isinstance(rows_changed, int) or rows_changed <= 0:
            return f"reminder #{reminder_id} isn't yours, or doesn't exist"

        return f"reminder #{reminder_id} cancelled"
