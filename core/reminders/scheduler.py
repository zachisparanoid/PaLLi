"""Canonical home for reminder scheduling on the new core/turn spine.

Post-C1.3: this module owns the public surface of the reminder scheduler:

- ``fire_reminder`` (Wave 4 entry point that flows through dispatch_turn)
- ``ScheduledReminder`` dataclass (the public reminder row shape)
- ``ReminderScheduler`` class + ``reminder_scheduler`` singleton

Module-level helpers (dataclasses, planning, runtime loop, delivery transport)
live in ``core/reminders/binding.py`` and are re-exported here so legacy
shims (``ai/reminder_*.py``) and existing consumers keep working until C1.4
rewires them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytz

# Re-export every module-level symbol that lived in the legacy ai/reminder_*
# modules. The wildcard makes the shims (and any existing consumer that
# imports from ai.reminder_scheduler etc.) keep working without name-by-name
# maintenance.
from core.reminders.binding import *  # noqa: F401,F403
from core.reminders.binding import (
    _DEFAULT_DEFERRED_ANALYSIS_RESULT,
    _DEFERRED_DEFAULT_SLEEP_SECONDS,
    _clear_delivery_failure,
    _note_delivery_failure,
    DOOR_NOT_READY,
    REMINDER_MAX_DELIVERY_ATTEMPTS,
    door_wait_exempt,
    check_due_reminders_for_target,
    cancel_reminder_for_target,
    compute_next_check_delay_seconds,
    execute_reminder_delivery,
    get_reminder_by_id_for_target,
    get_user_reminders_for_target,
    infer_delivery_preference,
    override_delivery_preference,
    prepare_reminder_delivery_request,
    record_runtime_event,
    reminder_record_value,
    run_check_loop,
    scheduled_reminder_from_record,
    schedule_deferred_action_for_target,
    send_reminder_for_target,
    should_reduce_reminder_interruptions,
    snooze_reminder_for_target,
    start_runtime,
    stop_runtime,
    DeferredActionScheduleRequest,
    ReminderDeliveryRequest,
    ReminderDeliveryResult,
    analysis_requests_deferred_action,
)
from core.config import config
from core.activity_logger import activity_logger
from core.database import brain
from core.doors import CHAT_DOORS, WEB_DOOR
from core.identity import identity_service
from core.local_mode import resolve_local_owner_observer_user_id_for_subject
from core.reminders.parser import ReminderTimeParseResult, ReminderTimeParser
from core.runtime_control import runtime_control
from core.setup_mode import lane_idle
from core.turn.types import Turn, TurnMetadata, TurnResult

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# fire_reminder — Wave 4 entry point that flows through the new spine.
# ----------------------------------------------------------------------------


# Batch 4 task 2 (R3b): what a late digest's fire turn is told. Every row in
# the digest is marked fired once the message is sent, so none may be left out.
_LATE_DIGEST_FIRE_NOTE = (
    "These came due while you were down, so each one is late. Name every one "
    "of them in your message and leave none out. If one is already handled "
    "or moot, say so for that one, but still name it."
)


# The doors she converses on (core/doors.py holds the one list). A reminder
# whose record names one of these (and a real chat) reads that chat's recent
# lines; every other door has no conversation of its own to read.
_CHAT_DOORS = CHAT_DOORS


async def _recent_conversation_lines(
    owner_id: int,
    *,
    platform: Optional[str] = None,
    channel_id: Optional[int] = None,
    limit: int = 30,
) -> str:
    """§16: the last few exchanges, oldest first, one line each — structure
    the fire turn reads, never a gate. "" on any failure.

    ``platform`` + ``channel_id`` name the chat the reminder will be posted
    to (Batch 4 task 1, 2026-09-28). When ``platform`` is "discord" or
    "telegram" and ``channel_id`` is a nonzero int, the lines come from THAT
    chat only — before, a Telegram reminder read the owner's Discord lines and a
    Discord one read every channel at once. Anything else (no door on the
    record, a local reminder, channel 0) keeps the §16 query: the owner's
    Discord lines, any channel. A web-page reminder (2026-10-10) reads the web
    chat it will appear in. ``limit`` is rows, not exchanges."""
    try:
        from core.database import brain
        door = str(platform or "").strip().lower()
        chat = int(channel_id or 0)
        query: Dict[str, Any] = {
            "user_id": int(owner_id), "platform": "discord", "limit": limit,
        }
        if door in _CHAT_DOORS and chat:
            query.update(platform=door, channel_id=chat)
        elif door == WEB_DOOR:
            query.update(platform=WEB_DOOR, channel_id=0)
        rows = await brain.get_conversation_history(**query)
    except Exception:  # noqa: BLE001 — eyes are a nicety, never the reminder's problem
        return ""
    rows = list(rows or [])
    try:
        rows.sort(key=lambda r: float((r or {}).get("timestamp") or 0.0))
    except Exception:  # noqa: BLE001
        pass
    lines = []
    for r in rows[-limit:]:
        role = str((r or {}).get("role") or "").strip().lower() or "?"
        text = " ".join(str((r or {}).get("content") or "").split())[:200]
        if text:
            # A ``system`` row is the record's own note — a reply the door
            # could not send (persist_delivery_failure) — never her words.
            who = {"user": "they", "system": "system"}.get(role, "you")
            lines.append(f"- {who}: {text}")
    return "\n".join(lines)


async def fire_reminder(
    reminder: Any, *, owner_id: int = 1,
) -> Optional[TurnResult]:
    """Synthesize a Turn(surface="reminder_fire") and run it through dispatch_turn.

    Routing goes through core.agent.dispatch so reminder fires honor
    PALLI_AGENT_LOOP the same as every other trigger — pre-fix this called
    process_turn directly, so reminders always ran the legacy pipeline even
    when the deployment ran ReAct (different tools/cadence/persistence).

    Returns the TurnResult so legacy delivery code can use the response_text
    when wiring through `deliver_reminder` (Task D2). Returns None when the
    reminder has no usable text or when dispatch_turn raises.

    Tolerates either `reminder.what` or `reminder.text` as the source field
    (different reminder shapes show up across the legacy code).

    Optional `reminder.platform` + `reminder.channel_id` name the chat the
    reminder will be posted to; the turn reads that chat's recent lines
    (see ``_recent_conversation_lines``). A reminder without them reads the
    owner's Discord lines, as before.
    """
    what = str(
        getattr(reminder, "what", None) or getattr(reminder, "text", "") or "",
    ).strip()
    if not what:
        return None
    reminder_id = int(getattr(reminder, "id", 0) or 0)
    # Batch 4 task 2 (R3b): a late digest carries several reminders, and every
    # one of them is marked fired once this message is sent — so she is told
    # to name each one, and dispatch keeps the length governor off the turn.
    late_digest = bool(getattr(reminder, "late_digest", False))
    content = f"[REMINDER FIRE] {what}"
    if late_digest:
        content += "\n\n" + _LATE_DIGEST_FIRE_NOTE
    # Batch 4 task 1 (1.7): the chat the reminder goes into, from its record.
    # It feeds the recent-lines block ONLY — never the turn's metadata, where
    # it would tell the "Where I Am" block the fire runs IN that chat and hand
    # the chat to create_reminder's ToolContext.
    recent = await _recent_conversation_lines(
        owner_id,
        platform=getattr(reminder, "platform", None),
        channel_id=getattr(reminder, "channel_id", None),
    )
    if recent:
        # DEFECT_RECORD §16 (2026-09-01): the fire turn ran on its own
        # session and could not see that he had already brought the budget
        # up two hours earlier — so it said "never came up tonight". Now it
        # sees the recent conversation; whether the reminder still lands as
        # written, or as "you beat me to it", is hers. Delivery itself is
        # still guaranteed (reminders never fall to the arbiter's gates).
        if late_digest:
            # A digest may not skip a line: "already handled" is said per
            # reminder, next to its name.
            content += (
                "\n\nWhat's been said recently (newest last). If one of these "
                "reminders is already handled, moot, or they beat you to it, "
                "say so for that one, but still name it:\n" + recent
            )
        else:
            content += (
                "\n\nWhat's been said recently (newest last). If this reminder is "
                "already handled, moot, or they beat you to it, say so lightly "
                "instead of repeating it:\n" + recent
            )
    metadata_kwargs: Dict[str, Any] = {}
    if late_digest:
        metadata_kwargs["extra"] = {"late_digest": True}
    synthesized = Turn(
        surface="reminder_fire",
        owner_id=owner_id,
        content=content,
        metadata=TurnMetadata(
            origin="reminder",
            reminder_id=reminder_id,
            **metadata_kwargs,
        ),
    )
    try:
        # Lazy import: dispatch pulls in the agent-loop stack (which reaches
        # back into core.turn.*); importing at module top would extend the
        # binding/scheduler import cycle tracked in
        # tests/architecture/test_no_circular_imports.py.
        from core.agent.dispatch import dispatch_turn
        return await dispatch_turn(synthesized)
    except Exception as exc:
        logger.warning("fire_reminder: dispatch_turn raised: %s", exc)
        return None


# ----------------------------------------------------------------------------
# Late fires — one "while I was down" message per chat (Batch 4 task 2, 5.7)
# ----------------------------------------------------------------------------


def partition_due(rows, *, now, grace_seconds=1800) -> tuple[list, list]:
    """Split due rows into (on_time, late), keeping their order.

    After downtime every overdue row used to fire on its own — one turn and
    one message each, none of them saying it was late. A row is LATE when it
    came due more than ``grace_seconds`` before ``now``; it then goes out
    inside one digest per chat instead. A row whose due time is absent, None,
    unreadable or 0 (the store's stand-in for "no time") stays on time:
    lateness that cannot be shown is not lateness. A time gate only — nothing
    here reads the reminder's words."""
    on_time: list = []
    late: list = []
    for row in rows or []:
        try:
            due_at = float(reminder_record_value(row, "scheduled_time", 0.0) or 0.0)
        except (TypeError, ValueError):
            due_at = 0.0
        if due_at != 0 and float(now) - due_at > float(grace_seconds):
            late.append(row)
        else:
            on_time.append(row)
    return on_time, late


def _late_due_label(due_at: float, *, today) -> str:
    # Host-local clock, as create/list/reschedule_reminder print a due time;
    # the date joins it when the reminder came due on another day, because
    # a bare "23:15" after a long outage does not say which night.
    try:
        when = datetime.fromtimestamp(due_at)
    except (OverflowError, OSError, ValueError):
        return ""
    if when.date() == today:
        return when.strftime("%H:%M")
    return when.strftime("%Y-%m-%d %H:%M")


def render_late_digest(late_rows, *, now: Optional[float] = None) -> str:
    """The words of the one message that carries every late reminder of a
    chat, each with the time it came due. ``now`` decides what "today" is
    (default: the current time)."""
    today = datetime.fromtimestamp(time.time() if now is None else float(now)).date()
    entries = []
    for row in late_rows or []:
        text = " ".join(str(reminder_record_value(row, "reminder_text", "") or "").split())
        try:
            due_at = float(reminder_record_value(row, "scheduled_time", 0.0) or 0.0)
        except (TypeError, ValueError):
            due_at = 0.0
        label = _late_due_label(due_at, today=today) if due_at != 0 else ""
        entries.append((label, text or "(no text)"))
    if len(entries) == 1:
        label, text = entries[0]
        when = f" ({label})" if label else ""
        return f"This one came due while I was down{when}: {text}"
    lines = [f"- {label}: {text}" if label else f"- {text}" for label, text in entries]
    return "While I was down, these came due:\n" + "\n".join(lines)


# ----------------------------------------------------------------------------
# ScheduledReminder dataclass
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduledReminder:
    """Structured deferred reminder/timer/alarm data."""
    id: int
    user_id: int
    platform: str
    channel_id: int
    reminder_text: str
    scheduled_time: float
    created_time: float
    is_completed: bool
    surface_id: str = ""
    kind: str = "reminder"
    runtime_job_id: str = ""
    runtime_session_key: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------
# ReminderScheduler — public class + singleton
# ----------------------------------------------------------------------------


class ReminderScheduler:
    """LLM-powered reminder detection and scheduling"""

    def __init__(self):
        self.bot = None  # Will be set by the bot
        self.check_task = None
        self.is_running = False
        self._wake_event = asyncio.Event()
        self._time_parser = ReminderTimeParser()
        self.confidence_threshold: float = float(
            getattr(config, "REMINDER_CONFIDENCE_THRESHOLD", 0.6)
        )
        self.loop_state: str = "idle"
        self.last_check_started_at: Optional[float] = None
        self.last_check_completed_at: Optional[float] = None
        self.last_error: str = ""
        self.last_dispatch_attempt: Optional[Dict[str, Any]] = None
        # C1.3: build_runtime_snapshot / get_runtime_snapshot were deleted as
        # dead code (no external dashboard consumes them). The deque +
        # _record_runtime_event remain because module-level helpers + tests
        # rely on them for in-process observability (e.g. surfacing
        # followthrough_flip_failed for QUE-HIGH-1 compliance).
        self.recent_events = deque(maxlen=20)
        # R17a: stamped by start_runtime; the boot grace window counts from
        # it. None until the loop starts, and then no door is forgiven.
        self.runtime_started_at: Optional[float] = None

    def _record_runtime_event(self, kind: str, **payload: Any) -> None:
        record_runtime_event(self.recent_events, kind, **payload)

    async def _emit_runtime_update_for_subject(
        self,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
        account_user_id: Optional[int] = None,
    ) -> None:
        # Legacy dashboard section-update emit removed in W5.4.
        return

    async def run_check_now(self) -> None:
        await self._check_due_reminders()

    async def start(self):
        """Start the reminder scheduler"""
        started = await start_runtime(
            target=self,
            create_check_task=lambda: asyncio.create_task(self._reminder_check_loop()),
            wake_scheduler=self._wake_event.set,
            now=time.time,
        )
        if not started:
            return
        logger.info("Reminder scheduler started")
        await self._emit_runtime_update_for_subject()

    async def stop(self):
        """Stop the reminder scheduler"""
        if not self.is_running:
            return
        logger.info("Stopping reminder scheduler...")
        stopped = await stop_runtime(
            target=self,
            wake_scheduler=self._wake_event.set,
            logger_override=logger,
        )
        if not stopped:
            return
        logger.info("Reminder scheduler stopped")
        await self._emit_runtime_update_for_subject()

    async def _reminder_check_loop(self):
        """Main loop for checking due reminders and timers."""
        await run_check_loop(
            target=self,
            wake_event=self._wake_event,
            compute_next_check_delay_seconds=self._compute_next_check_delay_seconds,
            check_due_reminders=self._check_due_reminders,
            logger_override=logger,
        )

    async def parse_and_schedule_reminder(
        self,
        user_id: int,
        platform: str,
        channel_id: int,
        message: str,
        analysis: Optional[Dict] = None,
        source_is_dm: Optional[bool] = None,
        account_user_id: Optional[int] = None,
        surface_id: str = "",
    ) -> Optional[ScheduledReminder]:
        """Backward-compatible wrapper for deferred-action scheduling."""
        return await self.parse_and_schedule_deferred_action(
            user_id=user_id,
            platform=platform,
            channel_id=channel_id,
            message=message,
            analysis=analysis,
            source_is_dm=source_is_dm,
            account_user_id=account_user_id,
            surface_id=surface_id,
        )

    async def parse_and_schedule_deferred_action(
        self,
        user_id: int,
        platform: str,
        channel_id: int,
        message: str,
        analysis: Optional[Dict] = None,
        source_is_dm: Optional[bool] = None,
        account_user_id: Optional[int] = None,
        surface_id: str = "",
    ) -> Optional[ScheduledReminder]:
        """Parse message for deferred-action requests using LLM."""
        return await schedule_deferred_action_for_target(
            target=self,
            request=DeferredActionScheduleRequest(
                user_id=user_id,
                platform=platform,
                channel_id=channel_id,
                message=message,
                analysis=analysis,
                source_is_dm=source_is_dm,
                account_user_id=account_user_id,
                surface_id=surface_id,
            ),
            scheduled_reminder_factory=ScheduledReminder,
            wake_scheduler=self._wake_event.set,
            ensure_surface_session=runtime_control.ensure_surface_session,
            create_job=runtime_control.create_job,
            update_scheduled_reminder_runtime_link=brain.update_scheduled_reminder_runtime_link,
            activity_logger_override=activity_logger,
            logger_override=logger,
            now=time.time,
        )

    async def analyze_reminder_request(self, message: str) -> Dict:
        """DEPRECATED stub — reminder analysis was absorbed by core/turn/decide.py.

        Returns the default empty deferred-analysis result. Kept for compat
        with core/message_processor.py:2989-2998; Phase C7 deletes that file
        and this stub goes with it.
        """
        return dict(_DEFAULT_DEFERRED_ANALYSIS_RESULT)

    async def analyze_deferred_request(self, message: str) -> Dict:
        """DEPRECATED stub — reminder analysis was absorbed by core/turn/decide.py.

        Returns the default empty deferred-analysis result. Kept for compat
        with core/message_processor.py:2989-2998; Phase C7 deletes that file
        and this stub goes with it.
        """
        return dict(_DEFAULT_DEFERRED_ANALYSIS_RESULT)

    async def schedule_from_analysis(
        self,
        user_id: int,
        platform: str,
        channel_id: int,
        analysis: Dict,
        original_message: str,
        source_is_dm: Optional[bool] = None,
        account_user_id: Optional[int] = None,
        surface_id: str = "",
    ) -> Optional[ScheduledReminder]:
        """
        Schedule a deferred action using a precomputed analysis result.
        """
        if not analysis_requests_deferred_action(analysis):
            return None
        return await self.parse_and_schedule_deferred_action(
            user_id,
            platform,
            channel_id,
            original_message,
            analysis=analysis,
            source_is_dm=source_is_dm,
            account_user_id=account_user_id,
            surface_id=surface_id,
        )

    def _infer_delivery_preference(
        self,
        message: str,
        *,
        source_is_dm: Optional[bool],
        kind: str = "reminder",
    ) -> str:
        return infer_delivery_preference(
            message,
            source_is_dm=source_is_dm,
            kind=kind,
        )

    async def _analyze_deferred_request(
        self,
        message: str,
        *,
        identity_context: str = "",
    ) -> Dict:
        """DEPRECATED stub kept so coordination.schedule_deferred_action_for_target
        can still reach the no-deferred-action default through the target hook.
        Returns the empty default result; the real LLM-judged path is in
        core/turn/decide.py.
        """
        return dict(_DEFAULT_DEFERRED_ANALYSIS_RESULT)

    def _get_default_analysis_result(self) -> Dict:
        """Get default analysis result when LLM fails"""
        return dict(_DEFAULT_DEFERRED_ANALYSIS_RESULT)

    async def _resolve_user_timezone(
        self, user_id: int, platform: str
    ) -> Optional[timezone]:
        """Resolve a user's preferred timezone (IANA) from their stored profile."""
        try:
            profile = await brain.get_user_profile(user_id, platform)
            prefs = profile.get("preferences", {}) if isinstance(profile, dict) else {}

            tz_info = prefs.get("time_zone")
            tz_name: Optional[str] = None
            if isinstance(tz_info, dict):
                tz_name = tz_info.get("value")
            elif isinstance(tz_info, str):
                tz_name = tz_info

            if tz_name and isinstance(tz_name, str):
                try:
                    return pytz.timezone(tz_name.strip())
                except Exception:
                    return None
            return None
        except Exception:
            return None

    def _parse_result(
        self,
        *,
        status: str,
        reason_code: str = "",
        timestamp: Optional[float] = None,
    ) -> ReminderTimeParseResult:
        return self._time_parser.parse_result(
            status=status,
            reason_code=reason_code,
            timestamp=timestamp,
        )

    def _default_deferred_content(self, kind: str) -> str:
        return self._time_parser.default_deferred_content(kind)

    def _format_duration_words(self, seconds: float) -> str:
        return self._time_parser.format_duration_words(seconds)

    def _parse_spoken_number(self, text: str) -> Optional[float]:
        return self._time_parser.parse_spoken_number(text)

    def _parse_relative_duration_seconds(self, text: str) -> Optional[float]:
        return self._time_parser.parse_relative_duration_seconds(text)

    def _extract_clock_components(self, text: str) -> Optional[Dict[str, Any]]:
        return self._time_parser.extract_clock_components(text)

    def _resolve_clock_phrase(self, text: str, *, allow_bare_search: bool = False) -> Optional[Dict[str, Any]]:
        return self._time_parser.resolve_clock_phrase(
            text,
            allow_bare_search=allow_bare_search,
        )

    def _build_timestamp(
        self,
        *,
        base_date,
        components: Dict[str, Any],
        tz: timezone,
    ) -> float:
        return self._time_parser.build_timestamp(
            base_date=base_date,
            components=components,
            tz=tz,
            datetime_cls=datetime,
        )

    def _build_time_parse_feedback(
        self,
        *,
        time_expression: str,
        parse_result: ReminderTimeParseResult,
    ) -> str:
        return self._time_parser.build_time_parse_feedback(
            time_expression=time_expression,
            parse_result=parse_result,
        )

    def build_reminder_clarification_instruction(
        self,
        *,
        time_expression: str,
        parse_result: ReminderTimeParseResult,
        kind: str = "reminder",
    ) -> str:
        return self._time_parser.build_reminder_clarification_instruction(
            time_expression=time_expression,
            parse_result=parse_result,
            kind=kind,
        )

    async def _parse_time_expression_result(
        self,
        time_expression: str,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
    ) -> ReminderTimeParseResult:
        return await self._time_parser.parse_time_expression_result(
            time_expression,
            user_id=user_id,
            platform=platform,
            resolve_user_timezone=self._resolve_user_timezone,
            datetime_cls=datetime,
        )

    async def _parse_time_expression(
        self,
        time_expression: str,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
    ) -> Optional[float]:
        return await self._time_parser.parse_time_expression(
            time_expression,
            user_id=user_id,
            platform=platform,
            resolve_user_timezone=self._resolve_user_timezone,
            datetime_cls=datetime,
        )

    def _extract_number(self, text: str) -> Optional[int]:
        """Extract number from text"""
        try:
            # Look for digits
            numbers = re.findall(r"\d+", text)
            if numbers:
                return int(numbers[0])

            # Handle word numbers
            word_numbers = {
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            }

            for word, num in word_numbers.items():
                if word in text.lower():
                    return num

            return None
        except Exception:
            return None

    async def _compute_next_check_delay_seconds(self) -> float:
        # Setup mode holds due reminders (_check_due_reminders below), and a
        # held row stays overdue: for a timer or an alarm the next-due delay is
        # then the 1 s floor, for as long as the hold lasts — a connection a
        # second to change nothing. While held the loop sleeps the deferred
        # default instead and does not ask the database; scheduling a reminder
        # still sets the wake event, and a brain is noticed within that minute.
        if lane_idle("reminders"):
            return _DEFERRED_DEFAULT_SLEEP_SECONDS
        return await compute_next_check_delay_seconds(
            is_running=self.is_running,
            connection_factory=brain.get_connection,
            now=time.time,
            logger_override=logger,
        )

    async def _store_reminder(
        self,
        user_id: int,
        platform: str,
        channel_id: int,
        reminder_text: str,
        scheduled_time: float,
        *,
        kind: str = "reminder",
        metadata: Optional[Dict] = None,
        surface_id: str = "",
    ) -> int:
        """Store reminder in database"""
        conn = await brain.get_connection()
        try:
            reminder_id = await conn.fetchval(
                """
                INSERT INTO scheduled_reminders
                (user_id, platform, channel_id, surface_id, reminder_text, scheduled_time, created_time, is_completed, kind, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, $8, $9::jsonb)
                RETURNING id
            """,
                user_id,
                platform,
                channel_id,
                str(surface_id or channel_id),
                reminder_text,
                scheduled_time,
                time.time(),
                str(kind or "reminder"),
                json.dumps(metadata) if metadata else None,
            )

            return int(reminder_id)
        finally:
            await conn.close()

    def _prepare_reminder_delivery_request(self, reminder_record):
        return prepare_reminder_delivery_request(
            reminder_id=reminder_record_value(reminder_record, "id", 0),
            user_id=reminder_record_value(reminder_record, "user_id", 0),
            platform=reminder_record_value(reminder_record, "platform", ""),
            channel_id=reminder_record_value(reminder_record, "channel_id", 0),
            surface_id=reminder_record_value(reminder_record, "surface_id", ""),
            reminder_text=reminder_record_value(reminder_record, "reminder_text", ""),
            scheduled_time=reminder_record_value(reminder_record, "scheduled_time", 0.0),
            kind=reminder_record_value(reminder_record, "kind", "reminder"),
            metadata=reminder_record_value(reminder_record, "metadata", {}),
            runtime_job_id=reminder_record_value(reminder_record, "runtime_job_id", ""),
            format_duration_words=self._format_duration_words,
        )

    def _override_reminder_delivery_preference(
        self,
        request: ReminderDeliveryRequest,
        delivery_preference: str,
    ) -> ReminderDeliveryRequest:
        return override_delivery_preference(
            request,
            delivery_preference,
        )

    async def _execute_reminder_delivery(
        self,
        request: ReminderDeliveryRequest,
    ) -> ReminderDeliveryResult:
        return await execute_reminder_delivery(
            request,
            bot=self.bot,
        )

    async def _resolve_reminder_identity_owner_id(
        self,
        *,
        platform: str,
        account_user_id: Optional[int],
    ) -> Optional[int]:
        normalized_platform = str(platform or "").strip().lower()
        if normalized_platform == "local":
            return await identity_service.resolve_local_owner_user_id()
        if account_user_id is not None:
            return await identity_service.resolve_local_owner_user_id(
                account_user_id=int(account_user_id),
            )
        return None

    async def _get_reminder_action_policy(
        self,
        *,
        user_id: int,
        platform: str,
        account_user_id: Optional[int],
    ) -> Any:
        # C1.3 step 1: identity-context snapshot was deleted (decide.py
        # owns the BehaviorFrame). Action policy is still fetched directly
        # so the runtime can softly respect interruption + quiet-hours
        # preferences at delivery time.
        try:
            identity_owner_id = await self._resolve_reminder_identity_owner_id(
                platform=platform,
                account_user_id=account_user_id,
            )
            return await identity_service.get_compiled_action_policy(
                user_id=int(user_id),
                platform=str(platform),
                identity_owner_id=identity_owner_id,
            )
        except Exception:
            return {}

    async def _check_due_reminders(self):
        """Check for and send due reminders."""
        # Setup mode (first door, task 1, 2026-09-17): a due reminder is HELD.
        # With no brain both chat doors are shut, so a reminder that fired
        # could not be delivered — and the dead-letter guard in binding.py
        # retires a row after REMINDER_MAX_DELIVERY_ATTEMPTS failed
        # deliveries: marked completed, never said. Gated here, above the
        # listing, nothing is attempted, counted or marked; the row stays due
        # in the database and goes out, in her words, once she has a brain.
        if lane_idle("reminders"):
            return
        # Great Excision (2026-07-09): mark_followthrough_delivered is no
        # longer passed — the operator followthrough store is gone. The
        # binding's guard block stays (it only ever fired for reminders whose
        # metadata carried origin=workflow_followthrough + followthrough_id,
        # which only the deleted operator engine could create).
        #
        # Batch 4 task 2 (5.7): the binding's per-row loop sees only the rows
        # still inside the grace; anything more than thirty minutes late is
        # set aside here and goes out afterwards as ONE message per chat.
        late_rows: List[Any] = []

        async def _list_on_time(**kwargs):
            rows = await brain.list_due_scheduled_reminders(**kwargs)
            as_of = kwargs.get("as_of")
            on_time, late = partition_due(
                list(rows or []), now=time.time() if as_of is None else float(as_of),
            )
            late_rows.extend(late)
            return on_time

        await check_due_reminders_for_target(
            target=self,
            list_due_scheduled_reminders=_list_on_time,
            mark_scheduled_reminder_completed=brain.mark_scheduled_reminder_completed,
            send_reminder=self._send_reminder,
            now=time.time,
            logger_override=logger,
            # Fix plan 5.8: a row retired at the dead-letter cap is stamped
            # ``dead_lettered`` first, so it never reads as delivered.
            merge_scheduled_reminder_metadata=brain.merge_scheduled_reminder_metadata,
        )
        if late_rows:
            await self._deliver_late_digests(late_rows)

    async def _deliver_late_digests(self, late_rows: List[Any]) -> None:
        """Send each owner's late reminders for a chat as one digest turn.

        Rows are grouped by (user_id, platform, channel_id), so one user's
        reminder text never goes into another user's message. The digest
        rides the ordinary delivery path (``_send_reminder``) as a record of
        its own: the group's owner and door, the digest as its text, the first
        row's id, metadata flagged ``late_digest`` (the fire turn names every
        line, and the length governor stays off), and no runtime job to begin
        or complete. Custody: a row is retired as ``late_fired`` only after
        the digest reached the chat. A failed digest leaves every row open
        and counts toward the binding's dead-letter cap under a key of its
        own, ``digest:<user>:<platform>:<channel>`` — the first row's id may
        already carry failed on-time tries (final review, minor 5), so the
        cap means three digest attempts. At the cap each row is retired as
        ``dead_lettered``, never as fired. A door that is not up yet
        (``DOOR_NOT_READY``) inside the boot grace window is not a failed
        digest: nothing is counted and every row stays open for the next
        check; after the window it counts like any failed send (R17a, the
        same rule as the per-row path). Reminders stay outside the arbiter
        here too."""
        groups: Dict[tuple, List[Any]] = {}
        for row in late_rows:
            key = (
                int(reminder_record_value(row, "user_id", 0) or 0),
                str(reminder_record_value(row, "platform", "") or ""),
                int(reminder_record_value(row, "channel_id", 0) or 0),
            )
            groups.setdefault(key, []).append(row)
        now = time.time()
        for (user_id, platform, channel_id), rows in groups.items():
            first = rows[0]
            digest_id = int(reminder_record_value(first, "id", 0) or 0)
            failure_key = f"digest:{user_id}:{platform}:{channel_id}"
            self._record_runtime_event(
                "late_digest", reminder_id=digest_id, count=len(rows),
            )
            try:
                delivered = await self._send_reminder({
                    "id": digest_id,
                    "user_id": user_id,
                    "platform": platform,
                    "channel_id": channel_id,
                    "surface_id": reminder_record_value(first, "surface_id", ""),
                    "reminder_text": render_late_digest(rows, now=now),
                    "scheduled_time": reminder_record_value(first, "scheduled_time", 0.0),
                    # A digest is a reminder whatever its first row was: a
                    # timer's fallback text would drop every other line.
                    "kind": "reminder",
                    "metadata": {
                        **reminder_record_value(first, "metadata", {}),
                        "late_digest": True,
                    },
                    "runtime_job_id": "",
                })
            except Exception as exc:  # noqa: BLE001 — counted like any failed send
                self.last_error = str(exc)
                self._record_runtime_event("error", error=str(exc))
                logger.error("Error sending late reminder digest %s: %s", digest_id, exc)
                delivered = False
            if delivered is DOOR_NOT_READY and door_wait_exempt(self, time.time()):
                # The chat door is not up yet (after a restart, Discord is
                # registered before its gateway connects), inside the boot
                # grace window: nothing was tried, so nothing is counted, and
                # every row stays open for the next check.
                # send_reminder_for_target already logged it at INFO. After
                # the window it counts below like any failed digest (R17a).
                continue
            if delivered:
                _clear_delivery_failure(self, failure_key)
                await self._retire_delivered_late_rows(rows)
                continue
            if digest_id <= 0:
                continue
            attempts = _note_delivery_failure(self, failure_key)
            if attempts >= REMINDER_MAX_DELIVERY_ATTEMPTS:
                logger.warning(
                    "reminder_runtime: late digest %s (%s reminders) undeliverable"
                    " after %s attempts; dead-lettering to stop the retry loop",
                    digest_id,
                    len(rows),
                    attempts,
                )
                self._record_runtime_event(
                    "dead_lettered", reminder_id=digest_id, attempts=attempts,
                )
                _clear_delivery_failure(self, failure_key)
                await self._dead_letter_late_rows(rows)

    async def _retire_delivered_late_rows(self, rows: List[Any]) -> None:
        """The digest was sent: complete each row, then stamp it
        ``late_fired``. Completion comes first because the message is out —
        a row left open would be sent again. Each row stands alone, so one
        failure never strands the rest of the group."""
        for row in rows:
            reminder_id = int(reminder_record_value(row, "id", 0) or 0)
            if reminder_id <= 0:
                continue
            try:
                await brain.mark_scheduled_reminder_completed(reminder_id=reminder_id)
            except Exception as exc:  # noqa: BLE001 — the next row is still retired
                logger.error(
                    "reminder_runtime: could not retire reminder %s after its late"
                    " digest was sent (it may be sent again): %s",
                    reminder_id, exc,
                )
                continue
            # A row may carry a count from failed on-time tries before it
            # turned late; it is retired now, so that count goes too.
            _clear_delivery_failure(self, reminder_id)
            try:
                await brain.merge_scheduled_reminder_metadata(
                    reminder_id=reminder_id, patch={"late_fired": True},
                )
            except Exception as exc:  # noqa: BLE001 — the stamp is bookkeeping; completion already holds
                logger.warning(
                    "reminder_runtime: could not stamp late_fired on reminder %s: %s",
                    reminder_id, exc,
                )

    async def _dead_letter_late_rows(self, rows: List[Any]) -> None:
        """The digest never reached the chat: stamp each row
        ``dead_lettered``, THEN complete it. The stamp comes first so a
        completed row always says how it was retired; a row whose stamp
        failed stays open and is tried again instead of looking delivered."""
        for row in rows:
            reminder_id = int(reminder_record_value(row, "id", 0) or 0)
            if reminder_id <= 0:
                continue
            try:
                await brain.merge_scheduled_reminder_metadata(
                    reminder_id=reminder_id, patch={"dead_lettered": True},
                )
                await brain.mark_scheduled_reminder_completed(reminder_id=reminder_id)
            except Exception as exc:  # noqa: BLE001 — the row stays open; the next row is still retired
                logger.error(
                    "reminder_runtime: could not retire reminder %s as dead-lettered"
                    " (it stays open and will be tried again): %s",
                    reminder_id, exc,
                )
                continue
            _clear_delivery_failure(self, reminder_id)

    async def _send_reminder(self, reminder_row) -> bool:
        """Send a reminder to the user."""
        return await send_reminder_for_target(
            target=self,
            reminder_record=reminder_row,
            prepare_reminder_delivery_request=self._prepare_reminder_delivery_request,
            override_delivery_preference=self._override_reminder_delivery_preference,
            execute_reminder_delivery=self._execute_reminder_delivery,
            get_action_policy=self._get_reminder_action_policy,
            should_reduce_interruptions=self._should_reduce_reminder_interruptions,
            now=time.time,
            logger_override=logger,
        )

    async def _should_reduce_reminder_interruptions(
        self,
        *,
        user_id: int,
        platform: str,
        action_preferences,
    ) -> bool:
        return await should_reduce_reminder_interruptions(
            user_id=user_id,
            platform=platform,
            action_preferences=action_preferences,
            resolve_user_timezone=self._resolve_user_timezone,
            datetime_cls=datetime,
        )

    def _scheduled_reminder_from_record(self, reminder_record: Dict[str, Any]) -> ScheduledReminder:
        return scheduled_reminder_from_record(
            reminder_record,
            scheduled_reminder_factory=ScheduledReminder,
        )

    async def get_user_reminders(
        self, user_id: int, platform: str
    ) -> List[ScheduledReminder]:
        """Get user's scheduled reminders."""
        return await get_user_reminders_for_target(
            user_id=user_id,
            platform=platform,
            list_user_scheduled_reminders=brain.list_user_scheduled_reminders,
            build_scheduled_reminder=self._scheduled_reminder_from_record,
            logger_override=logger,
        )

    async def get_reminder_by_id(self, reminder_id: int) -> Optional[ScheduledReminder]:
        return await get_reminder_by_id_for_target(
            reminder_id=int(reminder_id),
            get_scheduled_reminder_record=brain.get_scheduled_reminder_record,
            build_scheduled_reminder=self._scheduled_reminder_from_record,
            logger_override=logger,
        )

    async def snooze_reminder(self, reminder_id: int, *, minutes: int = 15) -> Optional[ScheduledReminder]:
        return await snooze_reminder_for_target(
            target=self,
            reminder_id=int(reminder_id),
            minutes=int(minutes),
            get_reminder_by_id=self.get_reminder_by_id,
            update_scheduled_reminder_schedule=brain.update_scheduled_reminder_schedule,
            update_runtime_job=runtime_control.update_job,
            wake_scheduler=self._wake_event.set,
            now=time.time,
            logger_override=logger,
        )

    async def cancel_reminder(self, reminder_id: int) -> bool:
        """Cancel a scheduled reminder."""
        return await cancel_reminder_for_target(
            target=self,
            reminder_id=int(reminder_id),
            get_reminder_by_id=self.get_reminder_by_id,
            mark_scheduled_reminder_completed=brain.mark_scheduled_reminder_completed,
            complete_runtime_job=runtime_control.complete_job,
            wake_scheduler=self._wake_event.set,
            now=time.time,
            logger_override=logger,
        )


# Global reminder scheduler instance
reminder_scheduler = ReminderScheduler()
