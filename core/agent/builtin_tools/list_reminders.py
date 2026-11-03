from __future__ import annotations

from datetime import datetime
from typing import Any

from core.agent.tools import ToolContext


class ListRemindersTool:
    name = "list_reminders"
    description = (
        "List the user's open scheduled reminders. Returns a markdown "
        "bulleted list with id, scheduled time, and message text. "
        "Completed/past reminders are not included."
    )
    input_schema = {
        "type": "object",
        "properties": {},
    }
    requires_approval = False
    owner_only = False

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        try:
            from core.database import brain
        except ImportError:
            return (
                "list_reminders: core.database.brain import failed — reminder "
                "store import path needs to be updated for this codebase."
            )

        owner_id = int(ctx.turn_owner_id)

        # Mirror respond_tier1._run_list_reminders: probe each surface so
        # cross-surface reminders surface to the caller.
        # The "local" entry covers reminders written by ambient_service,
        # companion_agenda, operator_continuity/followthrough, home_presence,
        # and runtime_control — all of which directly write platform="local".
        # Without "local" in the probe set, those rows are invisible to
        # list_reminders (Phase B / B3 step 3 fix for QUE-CRIT-2).
        # "telegram" (fix plan 5.8): a reminder made off a chat is filed to
        # the private chat he used last, which can be his Telegram chat.
        rows: list[dict] = []
        for platform in ("dashboard", "discord", "telegram", "mobile", "voice", "local"):
            try:
                fetched = await brain.list_user_scheduled_reminders(
                    user_id=owner_id, platform=platform,
                )
            except Exception:
                continue
            if fetched:
                rows.extend(fetched)

        rows.sort(key=lambda r: float(r.get("scheduled_time") or 0))

        if not rows:
            return "(no reminders)"

        lines: list[str] = []
        for r in rows[:50]:  # cap for prompt economy
            rid = r.get("id", "?")
            scheduled = r.get("scheduled_time")
            try:
                sched_str = (
                    datetime.fromtimestamp(float(scheduled)).strftime("%Y-%m-%d %H:%M")
                    if scheduled else "(no time)"
                )
            except (TypeError, ValueError):
                sched_str = str(scheduled)
            text = (r.get("reminder_text") or r.get("message") or "(no text)").strip()
            done = " [done]" if r.get("is_completed") else ""
            lines.append(f"- #{rid} at {sched_str}: {text}{done}")
        if len(rows) > 50:
            lines.append(f"(...and {len(rows) - 50} more)")
        return "\n".join(lines)
