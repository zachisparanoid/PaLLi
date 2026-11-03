from __future__ import annotations

from datetime import datetime
from typing import Any

from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext


class RescheduleReminderTool:
    name = "reschedule_reminder"
    description = (
        "Move an existing reminder to a new time. Provide reminder_id "
        "(from list_reminders) and new_time as a natural-language phrase "
        "('tomorrow at 9am', 'in 2 hours', '2026-05-08 14:30')."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "reminder_id": {
                "type": "integer",
                "description": "The numeric id of the reminder to move.",
            },
            "new_time": {
                "type": "string",
                "description": "Natural-language time phrase for the new "
                "scheduled time (e.g., 'tomorrow at 9am', 'in 2 hours').",
            },
        },
        "required": ["reminder_id", "new_time"],
    }
    requires_approval = False
    owner_only = False

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        try:
            from core.database import brain
        except ImportError:
            return (
                "reschedule_reminder: core.database.brain import failed — "
                "reminder store import path needs to be updated for this codebase."
            )
        try:
            from core.reminders.parser import ReminderTimeParser
        except ImportError:
            return (
                "reschedule_reminder: core.reminders.parser import failed — "
                "natural-language time parser path needs adjustment."
            )

        if "reminder_id" not in input or input.get("reminder_id") is None:
            return "reminder_id is required"
        try:
            reminder_id = int(input.get("reminder_id"))
        except (TypeError, ValueError):
            return (
                f"reschedule_reminder: invalid reminder_id "
                f"{input.get('reminder_id')!r} — must be an integer."
            )
        if reminder_id <= 0:
            return f"reschedule_reminder: reminder_id must be > 0 (got {reminder_id})"

        new_time_raw = str(input.get("new_time") or "").strip()
        if not new_time_raw:
            return "reschedule_reminder: new_time is required (natural language)"

        # Phase B / B3 step 3 (2026-05-09): pass the caller's surface
        # through to the time parser so its anchor matches the platform
        # the row was originally written under. Pre-fix this hardcoded
        # "dashboard" and a Discord/voice-local-anchored reminder
        # rescheduled via the agent tool would re-anchor incorrectly
        # (RA-MED-7).
        from core.turn.recall import platform_for_surface
        platform = platform_for_surface(getattr(ctx, "surface", ""))
        parser = ReminderTimeParser()
        try:
            result = await parser.parse_time_expression_result(
                new_time_raw,
                user_id=int(ctx.turn_owner_id),
                platform=platform,
            )
        except Exception as exc:
            return f"reschedule_reminder: time parse raised {type(exc).__name__}: {exc}"

        if result.status != "ok" or result.timestamp is None:
            return (
                f"reschedule_reminder: could not parse new_time={new_time_raw!r} "
                f"(status={result.status}, reason={result.reason_code})"
            )

        new_ts = float(result.timestamp)

        try:
            rows_changed = await brain.update_scheduled_reminder_schedule(
                reminder_id=reminder_id, scheduled_time=new_ts, user_id=int(ctx.turn_owner_id),
            )
        except Exception as exc:
            # The store raised and this tool caught it (week 2, task 1): the
            # reminder is still at its old time, so this is an error result.
            return ToolFailure(f"reschedule_reminder failed: {type(exc).__name__}: {exc}")

        # Batch 4 task 4 (2026-09-28): same "not yours" rule as cancel_reminder —
        # 0 rows or a non-int result (e.g. a mock returning None) both mean the
        # row was never moved, so nothing about it should read as rescheduled.
        if not isinstance(rows_changed, int) or rows_changed <= 0:
            return f"reminder #{reminder_id} isn't yours, or doesn't exist"

        try:
            new_str = datetime.fromtimestamp(new_ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            new_str = str(new_ts)
        return f"reminder #{reminder_id} rescheduled to {new_str}"
