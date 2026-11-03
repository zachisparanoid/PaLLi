from __future__ import annotations

from datetime import datetime
from typing import Any

from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext
from core.doors import CHAT_DOORS, WEB_DOOR, last_used_door

# Fix plan 5.8, the owner's rule (2026-09-28): "Last chat, else refuse." Since
# 2026-10-10 (web-page reminders) the "else" is the web page, so this sentence
# only answers a lookup that could not be made at all.
NO_DOOR_REFUSAL = (
    "create_reminder: I don't have a chat to send this to yet — "
    "message me on Discord, Telegram or the phone app first"
)

# The surfaces that ARE a chat she can post in, and the door each one is:
# "<door>_text" for each door in core/doors.CHAT_DOORS, the one list.
_CHAT_SURFACES = {f"{door}_text": door for door in CHAT_DOORS}


async def _resolve_door(*, surface: str, channel_id: int, owner_id: int) -> tuple[str, int] | None:
    """Where a new reminder will be posted, as ``(platform, channel_id)``.

    A reminder made in a Discord or Telegram chat stays in that chat, a group
    or a server channel included: work started in a group goes back to it.
    One made anywhere else (the dashboard, a voice call, the local mic, a
    reminder or proactive turn, an unknown surface) goes to the private chat
    with him he used most recently, never a group (the owner, 2026-10-13).
    With no private chat at all it goes to the web page (``WEB_DOOR``,
    channel 0), which is his alone: the owner's rule since 2026-10-10 is
    "last chat, else the web page", so a web-page-only install can keep
    reminders too. The web page is never preferred over a chat: with a chat
    app the phone gets the notification.
    """
    platform = _CHAT_SURFACES.get(str(surface or "").strip())
    if platform and channel_id:
        return (platform, int(channel_id))
    door = await last_used_door(owner_id)
    return door if door is not None else (WEB_DOOR, 0)


async def can_deliver_from(ctx) -> bool:
    """True when a reminder made on this turn would have a chat to go to, by the
    same rule the tool applies (2026-10-08: the promise gate offers the scheduled
    check only when it can work). Since 2026-10-10 a web-page-only install has
    the web page, so only a lookup that fails answers False: never offer what
    may refuse."""
    try:
        door = await _resolve_door(
            surface=str(getattr(ctx, "surface", "") or ""),
            channel_id=int(getattr(ctx, "channel_id", 0) or 0),
            owner_id=int(getattr(ctx, "turn_owner_id", 0) or 0),
        )
    except Exception:
        return False
    return door is not None


class CreateReminderTool:
    name = "create_reminder"
    description = (
        "Schedule a new reminder. Provide message (what to remind about) "
        "and when (natural-language time phrase like 'tomorrow at 9am', "
        "'in 2 hours', or '2026-05-08 14:30'). Returns a confirmation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The reminder text (what to remind the user about).",
            },
            "when": {
                "type": "string",
                "description": "Natural-language time phrase for when the "
                "reminder should fire.",
            },
        },
        "required": ["message", "when"],
    }
    requires_approval = False
    owner_only = False

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str | ToolFailure:
        try:
            from core.reminders.scheduler import reminder_scheduler
        except ImportError:
            return (
                "create_reminder: core.reminders.scheduler import failed — "
                "scheduler path needs to be updated for this codebase."
            )

        message = str(input.get("message") or "").strip()
        when = str(input.get("when") or "").strip()
        if not message:
            return "create_reminder: message is required"
        if not when:
            return "create_reminder: when is required (natural language)"

        # Kept for delivery-preference inference + activity logs.
        phrase = f"remind me {when} to {message}"

        fn = getattr(reminder_scheduler, "parse_and_schedule_reminder", None)
        if fn is None:
            return (
                "create_reminder: parse_and_schedule_reminder not found on "
                "reminder_scheduler — scheduler API has changed."
            )

        # Fix plan 5.8 (the owner's rule, 2026-09-28): every reminder is
        # filed to a chat she can post in, or (2026-10-10) to the web page
        # when there is no chat at all; a lookup that fails still refuses. Before this the row
        # took its platform from the surface (reminder_fire, the dashboard or
        # a blank surface became "dashboard", the local mic "voice") with
        # channel 0: nowhere to send, so it failed three times and was
        # retired as if it had gone out. Refusing files NOTHING.
        door = await _resolve_door(
            surface=str(getattr(ctx, "surface", "") or ""),
            channel_id=int(getattr(ctx, "channel_id", 0) or 0),
            owner_id=int(ctx.turn_owner_id),
        )
        if door is None:
            return ToolFailure(NO_DOOR_REFUSAL)
        platform, channel_id = door

        # 2026-07-10 live bug: without a pre-baked analysis the binding falls
        # back to the deprecated _analyze_deferred_request STUB (reminder
        # analysis was absorbed into the retired pipeline's decide.py — C1.3),
        # which always returns is_deferred_request=False — so scheduling
        # short-circuited to None and EVERY `when` format came back
        # "couldn't parse". The tool already has clean when/message fields, so
        # bake the analysis dict here (no LLM needed) and let the scheduler's
        # real time parser do its job.
        analysis = {
            "is_deferred_request": True,
            "is_reminder_request": True,
            "kind": "reminder",
            "content_text": message,
            "reminder_text": message,
            "time_expression": when,
            "confidence": 1.0,
        }
        # 2026-07-10: thread the chat's address into the row —
        # channel_id=0 / surface_id="" left delivery with nowhere to send.
        try:
            result = await fn(
                user_id=int(ctx.turn_owner_id),
                platform=platform,
                channel_id=channel_id,
                message=phrase,
                analysis=analysis,
                surface_id=str(channel_id),
            )
        except Exception as exc:
            # The scheduler raised and this tool caught it: a call that ran
            # and did not work (week 2, task 1). Same words, carried as a
            # failure so dispatch marks it an error instead of a deed done.
            return ToolFailure(f"create_reminder failed: {type(exc).__name__}: {exc}")

        if result is None:
            return (
                f"create_reminder: scheduler couldn't parse "
                f"when={when!r} for message={message!r}"
            )

        # Fable's review (2026-10-10): with no chat on record the reminder
        # goes to the web page, and she says so, so it never goes quietly to
        # a page nobody opens (a voice-only owner, say). Since 10-13 only a
        # private chat counts, so a group on record does not make it untrue.
        where = (
            " (it will appear in the web page: there is no private Discord, "
            "Telegram or phone-app chat with them on record)"
            if platform == WEB_DOOR else ""
        )
        try:
            scheduled_at = (
                getattr(result, "scheduled_for", None)
                or getattr(result, "scheduled_at", None)
                or getattr(result, "scheduled_time", None)
            )
            text = (
                getattr(result, "message", None)
                or getattr(result, "content", None)
                or message
            )
            if scheduled_at is not None:
                if isinstance(scheduled_at, (int, float)):
                    sched_str = datetime.fromtimestamp(
                        float(scheduled_at)
                    ).strftime("%Y-%m-%d %H:%M")
                else:
                    sched_str = str(scheduled_at)
                rid = getattr(result, "id", None)
                rid_part = f"#{rid} " if rid is not None else ""
                return f"reminder {rid_part}scheduled for {sched_str}: {text}{where}"
            return f"reminder scheduled: {text}{where}"
        except Exception:
            return "reminder scheduled (details unavailable)"
