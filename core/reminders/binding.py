"""Module-level reminder helpers split out of core/reminders/scheduler.py.

This file holds the dataclasses, constants, and module-level functions
that the legacy ai/reminder_runtime.py, ai/reminder_schedule_planning.py,
ai/reminder_schedule_coordination.py, and ai/reminder_delivery.py
historically exposed. Keeping them here keeps scheduler.py focused on
the ReminderScheduler class + public API surface.

Consumers should import either from this module (canonical) or, until
C1.4 wires the rest of the codebase to the canonical paths, through the
wildcard re-exports in scheduler.py.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import timezone
from types import SimpleNamespace
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    TypeVar,
)

from core.activity_logger import activity_logger
from core.channel_runtime import ChannelDeliveryEnvelope, DoorNotReady, channel_runtime
from core.continuity_service import continuity_service
from core.database import brain
from core.doors import CHAT_DOORS, WEB_DOOR
from core.identity.policy_access import policy_value
from core.reminder_continuity import (
    WORKFLOW_FOLLOWTHROUGH_REMINDER_ORIGIN,
    build_reminder_continuity_view,
)
from core.reminders.parser import ReminderTimeParseResult
from core.runtime_control import runtime_control

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# C1.3 step 1: reminder_analysis was absorbed by core/turn/decide.py. The
# default below preserves the public shape of analyze_deferred_request for
# message_processor.py:2989-2998 (deprecated stub) until C7 deletes that
# caller entirely.
_DEFAULT_DEFERRED_ANALYSIS_RESULT: Dict[str, Any] = {
    "is_deferred_request": False,
    "is_reminder_request": False,
    "kind": "reminder",
    "content_text": "",
    "reminder_text": "",
    "time_expression": "",
    "confidence": 0.0,
}

_ALLOWED_DEFERRED_KINDS = {"timer", "reminder", "alarm"}
_VALID_DELIVERY_PREFERENCES = {"dm", "channel"}

_DEFERRED_TIMER_NEAR_WINDOW_SECONDS = 900.0
_DEFERRED_TIMER_MAX_SLEEP_SECONDS = 5.0
_DEFERRED_DEFAULT_SLEEP_SECONDS = 60.0
_DEFERRED_MIN_SLEEP_SECONDS = 1.0

_SCHEDULED_REMINDER_ROW_INDEX = {
    "id": 0,
    "user_id": 1,
    "platform": 2,
    "channel_id": 3,
    "surface_id": 4,
    "reminder_text": 5,
    "scheduled_time": 6,
    "created_time": 7,
    "is_completed": 8,
    "kind": 9,
    "runtime_job_id": 10,
    "runtime_session_key": 11,
    "metadata": 12,
}

ScheduledReminderT = TypeVar("ScheduledReminderT")


# ----------------------------------------------------------------------------
# Dataclasses
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReminderDeliveryRequest:
    reminder_id: int
    user_id: int
    platform: str
    channel_id: int
    delivery_surface_id: str
    reminder_text: str
    reminder_message: str
    scheduled_time: float
    kind: str
    metadata: Dict[str, Any]
    delivery_preference: str
    runtime_job_id: str = ""
    account_user_id: Optional[int] = None


@dataclass(frozen=True)
class ReminderDeliveryResult:
    sent: bool
    delivered_in: str
    delivery_channel_id: int
    delivery_surface_id: str
    # DEFECT_RECORD §16 (2026-09-01): the text that ACTUALLY went out — her
    # composed spine reply when the fire turn produced one. The record must
    # hold what he received, never the raw note-to-self the reminder stored.
    delivered_text: str = ""
    # Final review, Important 3: the chat door was not up yet (not
    # registered, or Discord's gateway still connecting). Nothing was tried,
    # so inside the boot grace window (R17a, ``door_wait_exempt``) the
    # dead-letter cap does not count it; after the window it does.
    door_not_ready: bool = False


@dataclass
class PreparedDeferredScheduleRequest:
    kind: str
    reminder_text: str
    time_expression: str
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class DeferredRuntimeBindingSeed:
    owner_user_id: int
    session_kwargs: Dict[str, Any]
    job_kwargs: Dict[str, Any]


@dataclass(frozen=True)
class DeferredActionScheduleRequest:
    user_id: int
    platform: str
    channel_id: int
    message: str
    analysis: Optional[Dict[str, Any]] = None
    source_is_dm: Optional[bool] = None
    account_user_id: Optional[int] = None
    surface_id: str = ""


# ----------------------------------------------------------------------------
# Protocols (kept local for type-hint coupling)
# ----------------------------------------------------------------------------


class ReminderRuntimeTarget(Protocol):
    loop_state: str
    last_check_started_at: Optional[float]
    last_check_completed_at: Optional[float]
    last_error: str
    last_dispatch_attempt: Optional[Dict[str, Any]]

    def _record_runtime_event(self, kind: str, **payload: Any) -> None: ...

    async def _emit_runtime_update_for_subject(self, **kwargs: Any) -> None: ...


class ReminderRuntimeLoopTarget(ReminderRuntimeTarget, Protocol):
    is_running: bool
    recent_events: deque[dict[str, Any]]
    check_task: asyncio.Task[None] | None
    # When start_runtime began the loop (R17a: the boot grace window's start).
    runtime_started_at: Optional[float]


class ReminderScheduleCoordinationTarget(Protocol):
    confidence_threshold: float

    async def _analyze_deferred_request(self, message: str) -> Dict[str, Any]: ...

    async def _parse_time_expression_result(
        self,
        time_expression: str,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
    ) -> ReminderTimeParseResult: ...

    def _parse_relative_duration_seconds(self, text: str) -> Optional[float]: ...

    def _default_deferred_content(self, kind: str) -> str: ...

    def _build_time_parse_feedback(
        self,
        *,
        time_expression: str,
        parse_result: ReminderTimeParseResult,
    ) -> str: ...

    async def _store_reminder(
        self,
        user_id: int,
        platform: str,
        channel_id: int,
        reminder_text: str,
        scheduled_time: float,
        *,
        kind: str = "reminder",
        metadata: Optional[Dict[str, Any]] = None,
        surface_id: str = "",
    ) -> int: ...

    async def _emit_runtime_update_for_subject(
        self,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
        account_user_id: Optional[int] = None,
    ) -> None: ...

    def _record_runtime_event(self, kind: str, **payload: Any) -> None: ...


CreateCheckTaskCallback = Callable[[], asyncio.Task[None]]
ComputeNextCheckDelayCallback = Callable[[], Awaitable[float]]
CheckDueRemindersCallback = Callable[[], Awaitable[None]]


# ----------------------------------------------------------------------------
# Planning helpers
# ----------------------------------------------------------------------------


def analysis_requests_deferred_action(analysis: Any) -> bool:
    if not isinstance(analysis, dict):
        return False
    return bool(
        analysis.get("is_deferred_request", analysis.get("is_reminder_request", False))
    )


def normalize_deferred_kind(kind: Any) -> str:
    normalized_kind = str(kind or "reminder").strip().lower()
    if normalized_kind not in _ALLOWED_DEFERRED_KINDS:
        return "reminder"
    return normalized_kind


def infer_delivery_preference(
    message: str,
    *,
    source_is_dm: Optional[bool],
    kind: str = "reminder",
) -> str:
    """
    Infer whether the deferred action should be delivered via DM vs the source channel.

    Default behavior:
    - Timers and alarms default to the same originating surface.
    - Reminders keep the privacy-first DM default for server-originated requests.

    C1.3 step 1: identity-context-driven delivery bias was deleted. decide.py
    owns the BehaviorFrame; the heuristic group-cue scan below is the
    no-LLM fallback for the rare API-direct schedule path.
    """
    normalized_kind = normalize_deferred_kind(kind)
    if normalized_kind in {"timer", "alarm"}:
        return "channel"
    if source_is_dm is True:
        return "channel"
    if source_is_dm is None:
        # Without context, respect the provided channel_id (API-created reminders).
        return "channel"

    text = (message or "").strip().lower()
    if not text:
        return "dm"

    group_cues = (
        "remind us",
        "remind everyone",
        "remind the group",
        "remind the team",
        "everyone remember",
        "in this channel",
        "here in the channel",
        "post it here",
        "drop it here",
        "tell everyone",
        "ping everyone",
        "ping us",
    )
    if any(cue in text for cue in group_cues):
        return "channel"
    return "dm"


def prepare_deferred_schedule_request(
    analysis: Dict[str, Any],
    *,
    message: str,
    confidence_threshold: float,
    source_is_dm: Optional[bool],
    account_user_id: Optional[int],
    default_content: Callable[[str], str],
) -> Optional[PreparedDeferredScheduleRequest]:
    if not analysis_requests_deferred_action(analysis):
        return None

    prepared_analysis = dict(analysis)
    try:
        confidence = float(prepared_analysis.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    if confidence < confidence_threshold:
        logger.debug(
            "Skipping reminder scheduling due to low confidence (%.2f < %.2f)",
            confidence,
            confidence_threshold,
        )
        return None

    kind = normalize_deferred_kind(prepared_analysis.get("kind"))
    prepared_analysis["kind"] = kind

    try:
        if source_is_dm is None and isinstance(prepared_analysis.get("source_is_dm"), bool):
            source_is_dm = bool(prepared_analysis.get("source_is_dm"))
        prepared_analysis["source_is_dm"] = (
            bool(source_is_dm) if source_is_dm is not None else None
        )
        if account_user_id is not None:
            prepared_analysis["account_user_id"] = int(account_user_id)
        delivery = prepared_analysis.get("delivery_preference")
        if (
            not isinstance(delivery, str)
            or delivery.strip().lower() not in _VALID_DELIVERY_PREFERENCES
        ):
            prepared_analysis["delivery_preference"] = infer_delivery_preference(
                message,
                source_is_dm=source_is_dm,
                kind=kind,
            )
    except Exception:
        # Best-effort only; never block reminder scheduling.
        pass

    content_text = str(
        prepared_analysis.get("content_text")
        or prepared_analysis.get("reminder_text")
        or ""
    ).strip()
    reminder_text = content_text or default_content(kind)
    prepared_analysis["content_text"] = reminder_text
    prepared_analysis["reminder_text"] = reminder_text

    return PreparedDeferredScheduleRequest(
        kind=kind,
        reminder_text=reminder_text,
        time_expression=str(prepared_analysis.get("time_expression") or "").strip(),
        metadata=prepared_analysis,
    )


def apply_time_parse_result(
    prepared_request: PreparedDeferredScheduleRequest,
    *,
    parse_result: ReminderTimeParseResult,
    parse_relative_duration_seconds: Callable[[str], Optional[float]],
) -> Optional[float]:
    prepared_request.metadata["parse_status"] = str(parse_result.status or "")
    prepared_request.metadata["parse_reason_code"] = str(parse_result.reason_code or "")
    if parse_result.timestamp is not None:
        prepared_request.metadata["normalized_scheduled_time"] = float(
            parse_result.timestamp
        )

    if prepared_request.kind == "timer":
        duration_seconds = parse_relative_duration_seconds(
            prepared_request.time_expression
        )
        if duration_seconds is not None:
            prepared_request.metadata["duration_seconds"] = float(duration_seconds)

    if parse_result.status != "ok" or parse_result.timestamp is None:
        return None
    return float(parse_result.timestamp)


def build_store_reminder_kwargs(
    prepared_request: PreparedDeferredScheduleRequest,
    *,
    surface_id: str = "",
) -> Dict[str, Any]:
    store_kwargs: Dict[str, Any] = {
        "kind": prepared_request.kind,
        "metadata": prepared_request.metadata,
    }
    if str(surface_id or "").strip():
        store_kwargs["surface_id"] = surface_id
    return store_kwargs


def _normalize_optional_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_optional_refs(values: Iterable[Any]) -> List[str]:
    refs: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_optional_text(value)
        if not text:
            continue
        normalized = text.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        refs.append(text)
    return refs


def build_runtime_binding_seed(
    *,
    reminder_id: int,
    user_id: int,
    account_user_id: Optional[int],
    platform: str,
    channel_id: int,
    source_is_dm: Optional[bool],
    surface_id: str,
    scheduled_time: float,
    reminder_text: str,
    kind: str,
    delivery_preference: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
    current_time: Optional[float] = None,
) -> DeferredRuntimeBindingSeed:
    owner_user_id = int(account_user_id or user_id)
    normalized_surface_id = str(surface_id or "").strip()
    normalized_metadata = metadata if isinstance(metadata, dict) else {}
    continuity_view = build_reminder_continuity_view(
        {
            "metadata": normalized_metadata,
        }
    )
    continuity_refs = _normalize_optional_refs(continuity_view.get("continuity_refs") or [])
    workflow_id = _normalize_optional_text(continuity_view.get("workflow_id"))
    outcome_id = _normalize_optional_text(continuity_view.get("outcome_id"))
    followthrough_id = _normalize_optional_text(continuity_view.get("followthrough_id"))
    lane_id = _normalize_optional_text(continuity_view.get("lane_id"))
    thread_id = _normalize_optional_text(continuity_view.get("thread_id"))
    thread_kind = _normalize_optional_text(continuity_view.get("thread_kind"))
    thread_label = _normalize_optional_text(continuity_view.get("thread_label"))
    session_metadata: Dict[str, Any] = {"origin": "deferred_action", "kind": kind}
    for key, value in (
        ("workflow_id", workflow_id),
        ("outcome_id", outcome_id),
        ("followthrough_id", followthrough_id),
        ("lane_id", lane_id),
    ):
        if value:
            session_metadata[key] = value
    if continuity_refs:
        session_metadata["continuity_refs"] = continuity_refs
    session_kwargs: Dict[str, Any] = {
        "owner_user_id": owner_user_id,
        "platform": platform,
        "channel_id": channel_id,
        "is_dm": bool(source_is_dm),
        "status": "ready",
        "metadata": session_metadata,
    }
    if normalized_surface_id:
        session_kwargs["surface_id"] = normalized_surface_id
    if thread_id:
        session_kwargs["thread_id"] = thread_id
    if thread_kind:
        session_kwargs["thread_kind"] = thread_kind
    if thread_label:
        session_kwargs["thread_label"] = thread_label

    now = float(current_time if current_time is not None else time.time())
    job_metadata: Dict[str, Any] = {
        "platform": platform,
        "channel_id": channel_id,
        "surface_id": str(normalized_surface_id or channel_id),
        "delivery_preference": delivery_preference,
    }
    for key in (
        "identity_delivery_bias",
        "identity_delivery_tone",
        "identity_urgency_style",
        "identity_followthrough_style",
        "identity_proactive_cadence",
    ):
        value = normalized_metadata.get(key)
        if value:
            job_metadata[key] = value
    for key, value in (
        ("workflow_id", workflow_id),
        ("outcome_id", outcome_id),
        ("followthrough_id", followthrough_id),
        ("thread_id", thread_id),
        ("thread_kind", thread_kind),
        ("thread_label", thread_label),
        ("lane_id", lane_id),
    ):
        if value:
            job_metadata[key] = value
    if continuity_refs:
        job_metadata["continuity_refs"] = continuity_refs

    job_kwargs: Dict[str, Any] = {
        "owner_user_id": owner_user_id,
        "job_type": "deferred_action",
        "subtype": kind,
        "status": "queued" if float(scheduled_time or 0.0) <= now else "scheduled",
        "request_text": reminder_text,
        "scheduled_for": float(scheduled_time),
        "source_table": "scheduled_reminders",
        "source_id": str(reminder_id),
        "metadata": job_metadata,
    }
    return DeferredRuntimeBindingSeed(
        owner_user_id=owner_user_id,
        session_kwargs=session_kwargs,
        job_kwargs=job_kwargs,
    )


# ----------------------------------------------------------------------------
# Coordination orchestrator
# ----------------------------------------------------------------------------


async def schedule_deferred_action_for_target(
    *,
    target: ReminderScheduleCoordinationTarget,
    request: DeferredActionScheduleRequest,
    scheduled_reminder_factory: Callable[..., ScheduledReminderT],
    wake_scheduler: Callable[[], None],
    ensure_surface_session: Callable[..., Awaitable[Dict[str, Any]]],
    create_job: Callable[..., Awaitable[Dict[str, Any]]],
    update_scheduled_reminder_runtime_link: Callable[..., Awaitable[None]],
    activity_logger_override: Any = activity_logger,
    logger_override: Optional[logging.Logger] = None,
    now: Callable[[], float] = time.time,
) -> Optional[ScheduledReminderT]:
    active_logger = logger_override or logger
    try:
        # C1.3 step 1: identity-context snapshot was deleted — decide.py
        # owns BehaviorFrame tone now. We still call the analysis hook so
        # callers that pre-baked an analysis dict (the common path from
        # decide.py) keep working; without a pre-baked analysis, the stub
        # returns no-deferred-action and we short-circuit cleanly.
        analysis = request.analysis or await target._analyze_deferred_request(
            request.message,
        )
        prepared_request = prepare_deferred_schedule_request(
            analysis,
            message=request.message,
            confidence_threshold=target.confidence_threshold,
            source_is_dm=request.source_is_dm,
            account_user_id=request.account_user_id,
            default_content=target._default_deferred_content,
        )
        if prepared_request is None:
            return None

        activity_logger_override.log_reminder_detected(
            request.user_id,
            prepared_request.reminder_text,
        )
        activity_logger_override.log_database_operation(
            "parse_time_expression",
            prepared_request.time_expression,
        )

        parse_result = await target._parse_time_expression_result(
            prepared_request.time_expression,
            user_id=request.user_id,
            platform=request.platform,
        )
        scheduled_time = apply_time_parse_result(
            prepared_request,
            parse_result=parse_result,
            parse_relative_duration_seconds=target._parse_relative_duration_seconds,
        )
        if scheduled_time is None:
            activity_logger_override.log_warning(
                "reminder_scheduler",
                "Could not parse time: "
                f"{prepared_request.time_expression} "
                f"({parse_result.reason_code or 'parse_failed'})",
            )
            return None

        activity_logger_override.log_database_operation(
            "store_reminder",
            f"user {request.user_id}",
        )
        store_kwargs = build_store_reminder_kwargs(
            prepared_request,
            surface_id=request.surface_id,
        )
        reminder_id = int(
            await target._store_reminder(
                request.user_id,
                request.platform,
                request.channel_id,
                prepared_request.reminder_text,
                scheduled_time,
                **store_kwargs,
            )
        )

        runtime_job_id = ""
        runtime_session_key = ""
        try:
            binding_seed = build_runtime_binding_seed(
                reminder_id=reminder_id,
                user_id=request.user_id,
                account_user_id=request.account_user_id,
                platform=request.platform,
                channel_id=request.channel_id,
                source_is_dm=request.source_is_dm,
                surface_id=request.surface_id,
                scheduled_time=scheduled_time,
                reminder_text=prepared_request.reminder_text,
                kind=prepared_request.kind,
                delivery_preference=prepared_request.metadata.get(
                    "delivery_preference"
                ),
                metadata=prepared_request.metadata,
            )
            session = await ensure_surface_session(**binding_seed.session_kwargs)
            runtime_session_key = str(session.get("session_key") or "")
            job = await create_job(
                runtime_session_key=runtime_session_key,
                **binding_seed.job_kwargs,
            )
            runtime_job_id = str(job.get("job_id") or "")
            await update_scheduled_reminder_runtime_link(
                reminder_id=reminder_id,
                runtime_job_id=runtime_job_id,
                runtime_session_key=runtime_session_key,
            )
            prepared_request.metadata["runtime_job_id"] = runtime_job_id
            prepared_request.metadata["runtime_session_key"] = runtime_session_key
        except Exception:
            active_logger.debug(
                "Failed to attach deferred action to runtime control plane",
                exc_info=True,
            )

        activity_logger_override.log_reminder_scheduled(
            request.user_id,
            prepared_request.reminder_text,
            scheduled_time,
        )
        target._record_runtime_event(
            "scheduled",
            reminder_id=reminder_id,
            user_id=int(request.user_id),
            platform=str(request.platform),
            scheduled_time=float(scheduled_time),
            reminder_text=str(prepared_request.reminder_text),
            deferred_kind=str(prepared_request.kind),
        )
        await target._emit_runtime_update_for_subject(
            user_id=int(request.user_id),
            platform=str(request.platform),
            account_user_id=(
                int(request.account_user_id)
                if request.account_user_id is not None
                else None
            ),
        )
        wake_scheduler()

        return scheduled_reminder_factory(
            id=reminder_id,
            user_id=request.user_id,
            platform=request.platform,
            channel_id=request.channel_id,
            surface_id=str(request.surface_id or request.channel_id),
            reminder_text=prepared_request.reminder_text,
            scheduled_time=scheduled_time,
            created_time=now(),
            is_completed=False,
            kind=prepared_request.kind,
            runtime_job_id=runtime_job_id,
            runtime_session_key=runtime_session_key,
            metadata=prepared_request.metadata,
        )
    except Exception as exc:
        activity_logger_override.log_error("reminder_scheduler", str(exc))
        return None


# ----------------------------------------------------------------------------
# Runtime helpers (loop, dispatch, due-checking, snooze, cancel)
# ----------------------------------------------------------------------------


def record_runtime_event(
    recent_events: deque[dict[str, Any]],
    kind: str,
    **payload: Any,
) -> None:
    event = {
        "kind": str(kind or "event"),
        "timestamp": time.time(),
    }
    event.update(payload)
    recent_events.appendleft(event)


async def start_runtime(
    *,
    target: ReminderRuntimeLoopTarget,
    create_check_task: CreateCheckTaskCallback,
    wake_scheduler: Callable[[], None],
    now: Callable[[], float] = time.time,
) -> bool:
    if target.is_running:
        return False

    target.is_running = True
    # R17a: the boot grace window (REMINDER_DOOR_BOOT_GRACE_SECONDS) counts
    # from here — a chat door still coming up is forgiven only this long.
    target.runtime_started_at = now()
    wake_scheduler()
    target.loop_state = "idle"
    target.last_error = ""
    target._record_runtime_event("start")
    target.check_task = create_check_task()
    return True


async def stop_runtime(
    *,
    target: ReminderRuntimeLoopTarget,
    wake_scheduler: Callable[[], None],
    logger_override: Optional[logging.Logger] = None,
) -> bool:
    active_logger = logger_override or logger
    if not target.is_running:
        return False

    target.is_running = False
    wake_scheduler()
    target.loop_state = "stopped"
    target._record_runtime_event("stop")

    if target.check_task:
        target.check_task.cancel()
        try:
            await target.check_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            active_logger.warning("Error stopping reminder scheduler: %s", exc)
        finally:
            target.check_task = None

    return True


async def run_check_loop(
    *,
    target: ReminderRuntimeLoopTarget,
    wake_event: asyncio.Event,
    compute_next_check_delay_seconds: ComputeNextCheckDelayCallback,
    check_due_reminders: CheckDueRemindersCallback,
    logger_override: Optional[logging.Logger] = None,
) -> None:
    active_logger = logger_override or logger

    while target.is_running:
        try:
            sleep_seconds = await compute_next_check_delay_seconds()
            try:
                await asyncio.wait_for(wake_event.wait(), timeout=sleep_seconds)
                wake_event.clear()
                continue
            except asyncio.TimeoutError:
                pass
            await check_due_reminders()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            target.loop_state = "error"
            target.last_error = str(exc)
            target._record_runtime_event("error", error=str(exc))
            active_logger.error("Error in reminder check loop: %s", exc)


async def compute_next_check_delay_seconds(
    *,
    is_running: bool,
    connection_factory: Callable[[], Awaitable[Any]],
    now: Callable[[], float],
    logger_override: Optional[logging.Logger] = None,
) -> float:
    active_logger = logger_override or logger
    if not is_running:
        return _DEFERRED_MIN_SLEEP_SECONDS

    try:
        conn = await connection_factory()
    except Exception:
        active_logger.debug(
            "Failed to open reminder scheduler connection for wake delay",
            exc_info=True,
        )
        return _DEFERRED_DEFAULT_SLEEP_SECONDS

    try:
        row = await conn.fetchrow(
            """
            SELECT scheduled_time, COALESCE(kind, 'reminder')
            FROM scheduled_reminders
            WHERE is_completed = FALSE
            ORDER BY scheduled_time ASC
            LIMIT 1
            """
        )
    except Exception:
        active_logger.debug(
            "Failed to load next scheduled reminder for wake delay",
            exc_info=True,
        )
        return _DEFERRED_DEFAULT_SLEEP_SECONDS
    finally:
        await conn.close()

    if not row:
        return _DEFERRED_DEFAULT_SLEEP_SECONDS

    scheduled_time = float(row[0] or 0.0)
    kind = str(row[1] or "reminder").strip().lower()
    seconds_until_due = max(scheduled_time - now(), 0.0)
    if (
        kind in {"timer", "alarm"}
        and seconds_until_due <= _DEFERRED_TIMER_NEAR_WINDOW_SECONDS
    ):
        return max(
            _DEFERRED_MIN_SLEEP_SECONDS,
            min(
                _DEFERRED_TIMER_MAX_SLEEP_SECONDS,
                seconds_until_due or _DEFERRED_MIN_SLEEP_SECONDS,
            ),
        )
    return max(
        _DEFERRED_MIN_SLEEP_SECONDS,
        min(
            _DEFERRED_DEFAULT_SLEEP_SECONDS,
            seconds_until_due or _DEFERRED_DEFAULT_SLEEP_SECONDS,
        ),
    )


def normalize_reminder_metadata(metadata: Any) -> Dict[str, Any]:
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return dict(metadata)
    if isinstance(metadata, list):
        return {"items": list(metadata)}
    try:
        parsed = json.loads(metadata)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def reminder_record_value(
    reminder_record: Any,
    key: str,
    default: Any = None,
) -> Any:
    if isinstance(reminder_record, dict):
        value = reminder_record.get(key, default)
    elif isinstance(reminder_record, (list, tuple)):
        index = _SCHEDULED_REMINDER_ROW_INDEX.get(key)
        if index is None or index >= len(reminder_record):
            value = default
        else:
            value = reminder_record[index]
    else:
        value = getattr(reminder_record, key, default)

    if key == "metadata":
        return normalize_reminder_metadata(value)
    return default if value is None else value


def scheduled_reminder_from_record(
    reminder_record: Mapping[str, Any],
    *,
    scheduled_reminder_factory: Callable[..., ScheduledReminderT],
) -> ScheduledReminderT:
    metadata = reminder_record.get("metadata")
    return scheduled_reminder_factory(
        id=int(reminder_record["id"]),
        user_id=int(reminder_record["user_id"]),
        platform=str(reminder_record["platform"]),
        channel_id=int(reminder_record.get("channel_id") or 0),
        surface_id=str(
            reminder_record.get("surface_id")
            or reminder_record.get("channel_id")
            or ""
        ),
        reminder_text=str(reminder_record.get("reminder_text") or ""),
        scheduled_time=float(reminder_record.get("scheduled_time") or 0.0),
        created_time=float(reminder_record.get("created_time") or 0.0),
        is_completed=bool(reminder_record.get("is_completed")),
        kind=str(reminder_record.get("kind") or "reminder"),
        runtime_job_id=str(reminder_record.get("runtime_job_id") or ""),
        runtime_session_key=str(reminder_record.get("runtime_session_key") or ""),
        metadata=metadata if isinstance(metadata, (dict, list)) else {},
    )


def _account_user_id_from_metadata(metadata: Any) -> Optional[int]:
    if not isinstance(metadata, dict):
        return None
    account_user_id = metadata.get("account_user_id")
    if account_user_id is None:
        return None
    try:
        return int(account_user_id)
    except Exception:
        return None


async def should_reduce_reminder_interruptions(
    *,
    user_id: int,
    platform: str,
    action_preferences: Any,
    resolve_user_timezone: Callable[[int, str], Awaitable[Optional[timezone]]],
    datetime_cls: Any,
) -> bool:
    quiet_hours_mode = str(
        policy_value(action_preferences, "quiet_hours_mode", "adaptive")
    ).strip().lower()
    interruption_threshold = str(
        policy_value(action_preferences, "interruption_threshold", "urgent_only")
    ).strip().lower()

    if interruption_threshold == "urgent_only":
        return True
    if quiet_hours_mode == "off":
        return False

    tz = await resolve_user_timezone(user_id, platform)
    current_hour = datetime_cls.now(
        tz or datetime_cls.now().astimezone().tzinfo or timezone.utc
    ).hour
    quiet_start, quiet_end = (22, 9) if quiet_hours_mode == "gentle" else (23, 8)
    return current_hour >= quiet_start or current_hour < quiet_end


REMINDER_MAX_DELIVERY_ATTEMPTS = 3

# R17a: how long after the reminder runtime starts a chat door that is not up
# yet is forgiven (not counted toward the dead-letter cap). Long enough for
# Discord's gateway and Telegram's polling to come up after a restart; after
# it, a closed door is a failed send like any other, so a door that never
# comes up (a revoked token) dead-letters as it always did.
REMINDER_DOOR_BOOT_GRACE_SECONDS = 300.0


class _DoorNotReadyAnswer:
    """``send_reminder``'s third answer, beside True and False: nothing was
    tried, because the chat door is not up yet (final review, Important 3).
    After a restart the Discord adapter is registered before the gateway
    connects, and an overdue timer ticks the loop every second — counted,
    the reminders owed from the downtime were dead-lettered in the few
    seconds Discord took to open. Neither the per-row path nor the late
    digest counts this answer; the row stays open for the next check. It is
    only given inside the boot grace window (``door_wait_exempt``, R17a).
    Falsy, so a caller that reads it as a bool still hears "not sent", never
    "delivered"."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "DOOR_NOT_READY"


DOOR_NOT_READY = _DoorNotReadyAnswer()


def door_wait_exempt(target: Any, now: float) -> bool:
    """R17a: whether a chat door that is not up yet is still forgiven — only
    inside the boot grace window, REMINDER_DOOR_BOOT_GRACE_SECONDS after the
    reminder runtime started (``target.runtime_started_at``, stamped by
    start_runtime). After the window, or on a runtime that never recorded a
    start, a closed door counts like any failed send: the exemption is for the
    seconds a restart takes, never a forever-wait. The one rule the send, the
    per-row cap and the late digest all read."""
    started = getattr(target, "runtime_started_at", None)
    if started is None:
        return False
    try:
        elapsed = float(now) - float(started)
    except (TypeError, ValueError):
        return False
    return 0.0 <= elapsed <= REMINDER_DOOR_BOOT_GRACE_SECONDS


def _note_delivery_failure(target, reminder_id):
    """Increment and return the consecutive delivery-failure count for a
    reminder, cached on the runtime target (in-memory; cleared on a
    successful delivery or a dead-letter). Lazily created so any target
    shape works (real runtime target or a test fake)."""
    failures = getattr(target, "_delivery_failures", None)
    if failures is None:
        failures = {}
        target._delivery_failures = failures
    failures[reminder_id] = failures.get(reminder_id, 0) + 1
    return failures[reminder_id]


def _clear_delivery_failure(target, reminder_id):
    failures = getattr(target, "_delivery_failures", None)
    if failures:
        failures.pop(reminder_id, None)


async def check_due_reminders_for_target(
    *,
    target: ReminderRuntimeTarget,
    list_due_scheduled_reminders: Callable[..., Awaitable[List[Any]]],
    mark_scheduled_reminder_completed: Callable[..., Awaitable[None]],
    send_reminder: Callable[[Any], Awaitable[bool]],
    now: Callable[[], float],
    mark_followthrough_delivered: Optional[Callable[..., Awaitable[None]]] = None,
    logger_override: Optional[logging.Logger] = None,
    merge_scheduled_reminder_metadata: Optional[Callable[..., Awaitable[None]]] = None,
) -> None:
    active_logger = logger_override or logger
    try:
        target.loop_state = "checking"
        target.last_check_started_at = now()
        target.last_error = ""
        target._record_runtime_event("check_started")
        current_time = now()

        due_reminders = await list_due_scheduled_reminders(as_of=current_time)
        if due_reminders:
            target._record_runtime_event("due_batch", count=len(due_reminders))

        for reminder_record in due_reminders:
            target.loop_state = "dispatching"
            success = await send_reminder(reminder_record)
            reminder_id = int(reminder_record_value(reminder_record, "id", 0) or 0)
            if success is DOOR_NOT_READY and door_wait_exempt(target, now()):
                # The door is not up yet, inside the boot grace window:
                # nothing was tried, so nothing is counted. The row stays open
                # and is tried on the next check (send_reminder_for_target
                # already said so in the log). After the window it falls
                # through and counts like any failed send (R17a).
                continue
            if not success and reminder_id > 0:
                # Dead-letter guard (2026-07-13): a reminder that fails
                # delivery every cycle -- e.g. a stale channel binding
                # (channel_id=0) -- would otherwise re-fire forever, flooding
                # the turn context (one such row fired ~180x over 3 hours and
                # bled into an unrelated reply). Cap consecutive failures and
                # retire the row via the normal completion path.
                attempts = _note_delivery_failure(target, reminder_id)
                if attempts >= REMINDER_MAX_DELIVERY_ATTEMPTS:
                    active_logger.warning(
                        "reminder_runtime: reminder %s undeliverable after %s"
                        " attempts; dead-lettering to stop the retry loop",
                        reminder_id,
                        attempts,
                    )
                    target._record_runtime_event(
                        "dead_lettered", reminder_id=reminder_id, attempts=attempts
                    )
                    # Fix plan 5.8: completed alone is what a DELIVERED row
                    # gets, so the store could not tell the two apart. Stamp
                    # ``dead_lettered`` first, then complete — the late
                    # digest's order (scheduler._dead_letter_late_rows): a
                    # row whose stamp failed stays open and is tried again
                    # instead of looking delivered. A caller that hands no
                    # metadata writer still retires the row, as before.
                    try:
                        if merge_scheduled_reminder_metadata is not None:
                            await merge_scheduled_reminder_metadata(
                                reminder_id=reminder_id,
                                patch={"dead_lettered": True},
                            )
                        await mark_scheduled_reminder_completed(reminder_id=reminder_id)
                    except Exception as exc:  # noqa: BLE001 — the row stays open; the next row still runs
                        active_logger.error(
                            "reminder_runtime: could not retire reminder %s as"
                            " dead-lettered (it stays open and will be tried"
                            " again): %s",
                            reminder_id,
                            exc,
                        )
                        continue
                    _clear_delivery_failure(target, reminder_id)
                continue
            if success and reminder_id > 0:
                _clear_delivery_failure(target, reminder_id)
                await mark_scheduled_reminder_completed(reminder_id=reminder_id)
                # QUE-HIGH-1: when a reminder fires whose metadata traces back to
                # an operator workflow_followthrough, close the lifecycle by
                # flipping the originating followthrough to `delivered`. The
                # reminder fire is the load-bearing event; the flip is
                # bookkeeping, so a failure must NOT propagate. But the failure
                # is also NOT recoverable: `mark_scheduled_reminder_completed`
                # already ran above (line 362), so `list_due_scheduled_reminders`
                # filters this row out via `is_completed=TRUE` going forward —
                # the followthrough stays stuck on `converted_to_reminder`
                # forever unless someone notices. Log at ERROR and emit a
                # runtime event so the failure surfaces in dashboards / runtime
                # snapshots and manual recovery is possible.
                if mark_followthrough_delivered is not None:
                    metadata = reminder_record_value(
                        reminder_record, "metadata", {}
                    )
                    origin = str(metadata.get("origin") or "").strip().lower()
                    followthrough_id = str(
                        metadata.get("followthrough_id") or ""
                    ).strip()
                    if (
                        origin == WORKFLOW_FOLLOWTHROUGH_REMINDER_ORIGIN
                        and followthrough_id
                    ):
                        try:
                            await mark_followthrough_delivered(
                                followthrough_id=followthrough_id
                            )
                        except Exception as flip_exc:
                            active_logger.error(
                                "reminder_runtime: failed to flip followthrough"
                                " %s to delivered on reminder %s fire: %s",
                                followthrough_id,
                                reminder_id,
                                flip_exc,
                            )
                            target._record_runtime_event(
                                "followthrough_flip_failed",
                                followthrough_id=followthrough_id,
                                reminder_id=reminder_id,
                                error=str(flip_exc),
                            )

        target.loop_state = "idle"
        target.last_check_completed_at = now()
        target._record_runtime_event("check_completed")
        await target._emit_runtime_update_for_subject()
    except Exception as exc:
        target.loop_state = "error"
        target.last_error = str(exc)
        target.last_check_completed_at = now()
        target._record_runtime_event("error", error=str(exc))
        active_logger.error(f"Error checking due reminders: {exc}")
        await target._emit_runtime_update_for_subject()


async def send_reminder_for_target(
    *,
    target: ReminderRuntimeTarget,
    reminder_record: Any,
    prepare_reminder_delivery_request: Callable[
        [Any], ReminderDeliveryRequest
    ],
    override_delivery_preference: Callable[
        [ReminderDeliveryRequest, str],
        ReminderDeliveryRequest,
    ],
    execute_reminder_delivery: Callable[
        [ReminderDeliveryRequest],
        Awaitable[ReminderDeliveryResult],
    ],
    get_action_policy: Callable[..., Awaitable[Any]],
    should_reduce_interruptions: Callable[..., Awaitable[bool]],
    now: Callable[[], float],
    logger_override: Optional[logging.Logger] = None,
) -> "bool | _DoorNotReadyAnswer":
    active_logger = logger_override or logger
    try:
        delivery_request = prepare_reminder_delivery_request(reminder_record)
        reminder_id = int(delivery_request.reminder_id)
        user_id = int(delivery_request.user_id)
        platform = str(delivery_request.platform)
        account_user_id = delivery_request.account_user_id

        action_policy = await get_action_policy(
            user_id=user_id,
            platform=platform,
            account_user_id=account_user_id,
        )

        if await should_reduce_interruptions(
            user_id=user_id,
            platform=platform,
            action_preferences=action_policy,
        ):
            delivery_request = override_delivery_preference(delivery_request, "dm")
            target._record_runtime_event(
                "delivery_softened",
                reminder_id=reminder_id,
                user_id=user_id,
                platform=platform,
                reason="quiet_hours_or_interruption_policy",
            )

        target.last_dispatch_attempt = {
            "reminder_id": reminder_id,
            "user_id": user_id,
            "platform": platform,
            "delivery_preference": str(delivery_request.delivery_preference),
            "started_at": now(),
        }
        for key in ("identity_delivery_tone", "identity_urgency_style"):
            value = delivery_request.metadata.get(key)
            if value:
                target.last_dispatch_attempt[key] = str(value)
        delivery_result = await execute_reminder_delivery(delivery_request)

        # Which reminders have already said "waiting for the door" at INFO:
        # an overdue timer ticks the loop every second, and one line per
        # tick for a door that stays down would flood the log.
        door_waits = getattr(target, "_door_waits", None)
        if door_waits is None:
            door_waits = set()
            target._door_waits = door_waits
        if (
            not delivery_result.sent
            and getattr(delivery_result, "door_not_ready", False)
            # R17a: forgiven only inside the boot grace window. After it, a
            # closed door takes the ordinary failed-send path below (the
            # warning, False, counted) — no forever-wait, no line per tick.
            and door_wait_exempt(target, now())
        ):
            first_wait = reminder_id not in door_waits
            door_waits.add(reminder_id)
            (active_logger.info if first_wait else active_logger.debug)(
                "reminder_runtime: reminder %s waits: the %s door is not up yet"
                " (not a failed delivery, not counted; tried again next check)",
                reminder_id,
                platform,
            )
            target.last_dispatch_attempt = {
                **dict(target.last_dispatch_attempt or {}),
                "success": False,
                "door_not_ready": True,
                "ended_at": now(),
            }
            if first_wait:
                target._record_runtime_event(
                    "door_not_ready",
                    reminder_id=reminder_id,
                    user_id=user_id,
                    platform=platform,
                )
            return DOOR_NOT_READY
        door_waits.discard(reminder_id)

        if not delivery_result.sent:
            active_logger.warning(
                "Unable to send reminder %s (platform=%s)",
                reminder_id,
                platform,
            )
            target.last_dispatch_attempt = {
                **dict(target.last_dispatch_attempt or {}),
                "success": False,
                "ended_at": now(),
            }
            target._record_runtime_event(
                "delivery_failed",
                reminder_id=reminder_id,
                user_id=user_id,
                platform=platform,
            )
            await target._emit_runtime_update_for_subject(
                user_id=user_id,
                platform=platform,
                account_user_id=(
                    int(account_user_id) if account_user_id is not None else None
                ),
            )
            return False

        active_logger.info(
            "Sent reminder %s for user %s: %s",
            reminder_id,
            user_id,
            delivery_request.reminder_text,
        )
        target.last_dispatch_attempt = {
            **dict(target.last_dispatch_attempt or {}),
            "success": True,
            "delivered_in": str(
                delivery_result.delivered_in or delivery_request.delivery_preference
            ),
            "delivery_channel_id": int(delivery_result.delivery_channel_id or 0),
            "ended_at": now(),
        }
        target._record_runtime_event(
            "delivered",
            reminder_id=reminder_id,
            user_id=user_id,
            platform=platform,
            delivered_in=str(
                delivery_result.delivered_in or delivery_request.delivery_preference
            ),
        )
        await target._emit_runtime_update_for_subject(
            user_id=user_id,
            platform=platform,
            account_user_id=(
                int(account_user_id) if account_user_id is not None else None
            ),
        )
        return True
    except Exception as exc:
        target.last_error = str(exc)
        target._record_runtime_event("error", error=str(exc))
        active_logger.error(f"Error sending reminder: {exc}")
        return False


async def get_user_reminders_for_target(
    *,
    user_id: int,
    platform: str,
    list_user_scheduled_reminders: Callable[..., Awaitable[List[Dict[str, Any]]]],
    build_scheduled_reminder: Callable[[Dict[str, Any]], ScheduledReminderT],
    logger_override: Optional[logging.Logger] = None,
) -> List[ScheduledReminderT]:
    active_logger = logger_override or logger
    try:
        reminder_records = await list_user_scheduled_reminders(
            user_id=int(user_id),
            platform=str(platform),
        )
        return [
            build_scheduled_reminder(reminder_record)
            for reminder_record in reminder_records
        ]
    except Exception as exc:
        active_logger.error(f"Error getting user reminders: {exc}")
        return []


async def get_reminder_by_id_for_target(
    *,
    reminder_id: int,
    get_scheduled_reminder_record: Callable[[int], Awaitable[Optional[Dict[str, Any]]]],
    build_scheduled_reminder: Callable[[Dict[str, Any]], ScheduledReminderT],
    logger_override: Optional[logging.Logger] = None,
) -> Optional[ScheduledReminderT]:
    active_logger = logger_override or logger
    try:
        reminder_record = await get_scheduled_reminder_record(int(reminder_id))
        if reminder_record is None:
            return None
        return build_scheduled_reminder(reminder_record)
    except Exception as exc:
        active_logger.error(f"Error getting reminder {reminder_id}: {exc}")
        return None


async def snooze_reminder_for_target(
    *,
    target: ReminderRuntimeTarget,
    reminder_id: int,
    minutes: int,
    get_reminder_by_id: Callable[[int], Awaitable[Optional[ScheduledReminderT]]],
    update_scheduled_reminder_schedule: Callable[..., Awaitable[None]],
    update_runtime_job: Callable[..., Awaitable[None]],
    wake_scheduler: Callable[[], None],
    now: Callable[[], float],
    logger_override: Optional[logging.Logger] = None,
) -> Optional[ScheduledReminderT]:
    active_logger = logger_override or logger
    try:
        reminder = await get_reminder_by_id(int(reminder_id))
        if reminder is None or bool(getattr(reminder, "is_completed", False)):
            return None

        snooze_minutes = max(int(minutes or 15), 1)
        scheduled_time = now() + (snooze_minutes * 60)

        await update_scheduled_reminder_schedule(
            reminder_id=int(reminder_id),
            scheduled_time=float(scheduled_time),
        )

        updated = await get_reminder_by_id(int(reminder_id))
        runtime_job_id = str(getattr(reminder, "runtime_job_id", "") or "")
        if runtime_job_id:
            try:
                await update_runtime_job(
                    job_id=runtime_job_id,
                    status="scheduled",
                    scheduled_for=float(scheduled_time),
                    last_error="",
                    metadata={
                        "snoozed_at": now(),
                        "snooze_minutes": snooze_minutes,
                    },
                )
            except Exception:
                active_logger.debug(
                    "Failed to update snoozed deferred job %s",
                    runtime_job_id,
                    exc_info=True,
                )
        target._record_runtime_event(
            "snoozed",
            reminder_id=int(reminder_id),
            user_id=int(getattr(reminder, "user_id", 0) or 0),
            platform=str(getattr(reminder, "platform", "") or ""),
            scheduled_time=float(scheduled_time),
        )
        wake_scheduler()
        await target._emit_runtime_update_for_subject(
            user_id=int(getattr(reminder, "user_id", 0) or 0),
            platform=str(getattr(reminder, "platform", "") or ""),
            account_user_id=_account_user_id_from_metadata(
                getattr(reminder, "metadata", None)
            ),
        )
        return updated
    except Exception as exc:
        active_logger.error(f"Error snoozing reminder {reminder_id}: {exc}")
        return None


async def cancel_reminder_for_target(
    *,
    target: ReminderRuntimeTarget,
    reminder_id: int,
    get_reminder_by_id: Callable[[int], Awaitable[Optional[ScheduledReminderT]]],
    mark_scheduled_reminder_completed: Callable[..., Awaitable[None]],
    complete_runtime_job: Callable[..., Awaitable[None]],
    wake_scheduler: Callable[[], None],
    now: Callable[[], float],
    logger_override: Optional[logging.Logger] = None,
) -> bool:
    active_logger = logger_override or logger
    try:
        reminder = await get_reminder_by_id(int(reminder_id))
        # TODO(QUE-HIGH-1 sibling): manually-cancelled workflow_followthrough
        # reminders should also flip the originating followthrough — currently
        # they leak the same way the auto-fire path used to (followthrough
        # stays on `converted_to_reminder` forever). Tracked separately
        # because "fired" vs "cancelled" are distinct lifecycle terminals
        # (delivered vs dismissed/cancelled), and the cancel-side fix needs
        # its own audit-finding ID + scope.
        await mark_scheduled_reminder_completed(reminder_id=int(reminder_id))
        if reminder is not None:
            runtime_job_id = str(getattr(reminder, "runtime_job_id", "") or "")
            if runtime_job_id:
                try:
                    await complete_runtime_job(
                        job_id=runtime_job_id,
                        status="cancelled",
                        last_transition_reason="user_cancelled",
                        metadata={"cancelled_at": now()},
                    )
                except Exception:
                    active_logger.debug(
                        "Failed to cancel deferred job %s",
                        runtime_job_id,
                        exc_info=True,
                    )
            target._record_runtime_event(
                "cancelled",
                reminder_id=int(reminder_id),
                user_id=int(getattr(reminder, "user_id", 0) or 0),
                platform=str(getattr(reminder, "platform", "") or ""),
            )
            wake_scheduler()
            await target._emit_runtime_update_for_subject(
                user_id=int(getattr(reminder, "user_id", 0) or 0),
                platform=str(getattr(reminder, "platform", "") or ""),
                account_user_id=_account_user_id_from_metadata(
                    getattr(reminder, "metadata", None)
                ),
            )
        return True
    except Exception as exc:
        active_logger.error(f"Error cancelling reminder: {exc}")
        return False


# ----------------------------------------------------------------------------
# Delivery transport
# ----------------------------------------------------------------------------


def _reminder_payload_from_request(request: ReminderDeliveryRequest) -> Any:
    """Adapt a ReminderDeliveryRequest into the shape fire_reminder reads.

    fire_reminder expects an object with `.id` + (`.what` or `.text`).
    Wraps the request's reminder_id + reminder_text into a SimpleNamespace
    so we don't need fire_reminder to know about the legacy request type.
    `.platform` + `.channel_id` are the record's door (Batch 4 task 1), so
    the fire turn reads the recent lines of the chat it will post to.
    `.late_digest` marks the one message that carries every reminder that
    came due while she was down (Batch 4 task 2); its fire turn must name
    each of them.
    """
    return SimpleNamespace(
        id=int(request.reminder_id),
        what=str(request.reminder_text or ""),
        platform=request.platform,
        channel_id=request.channel_id,
        late_digest=bool((request.metadata or {}).get("late_digest")),
    )


def normalize_delivery_preference(
    delivery_preference: Any,
    *,
    source_is_dm: Any,
) -> str:
    if (
        isinstance(delivery_preference, str)
        and delivery_preference.strip().lower() in _VALID_DELIVERY_PREFERENCES
    ):
        return delivery_preference.strip().lower()
    if source_is_dm is True:
        return "channel"
    if source_is_dm is False:
        return "dm"
    return "channel"


def build_reminder_message(
    *,
    kind: Any,
    reminder_text: Any,
    metadata: Dict[str, Any],
    format_duration_words: Callable[[float], str],
) -> str:
    normalized_kind = normalize_deferred_kind(kind)
    reminder_body = str(reminder_text or "")
    if normalized_kind == "timer":
        duration_seconds = None
        try:
            duration_seconds = float(metadata.get("duration_seconds") or 0.0)
        except Exception:
            duration_seconds = None
        if duration_seconds:
            return f"Timer finished: {format_duration_words(duration_seconds)}."
        return "Timer finished."
    if normalized_kind == "alarm":
        return f"Alarm: {reminder_body}"
    prefix = _build_reminder_prefix(metadata)
    return f"{prefix}: {reminder_body}"


def prepare_reminder_delivery_request(
    *,
    reminder_id: Any,
    user_id: Any,
    platform: Any,
    channel_id: Any,
    surface_id: Any,
    reminder_text: Any,
    scheduled_time: Any,
    kind: Any,
    metadata: Any,
    runtime_job_id: Any,
    format_duration_words: Callable[[float], str],
) -> ReminderDeliveryRequest:
    normalized_metadata = _normalize_metadata(metadata)
    normalized_kind = normalize_deferred_kind(kind)
    delivery_preference = normalize_delivery_preference(
        normalized_metadata.get("delivery_preference") or normalized_metadata.get("delivery"),
        source_is_dm=normalized_metadata.get("source_is_dm"),
    )
    return ReminderDeliveryRequest(
        reminder_id=int(reminder_id),
        user_id=int(user_id),
        platform=str(platform or ""),
        channel_id=int(channel_id or 0),
        delivery_surface_id=str(surface_id or channel_id or ""),
        reminder_text=str(reminder_text or ""),
        reminder_message=build_reminder_message(
            kind=normalized_kind,
            reminder_text=reminder_text,
            metadata=normalized_metadata,
            format_duration_words=format_duration_words,
        ),
        scheduled_time=float(scheduled_time or 0.0),
        kind=normalized_kind,
        metadata=normalized_metadata,
        delivery_preference=delivery_preference,
        runtime_job_id=str(runtime_job_id or "").strip(),
        account_user_id=_coerce_optional_int(normalized_metadata.get("account_user_id")),
    )


def override_delivery_preference(
    request: ReminderDeliveryRequest,
    delivery_preference: Any,
) -> ReminderDeliveryRequest:
    return replace(
        request,
        delivery_preference=normalize_delivery_preference(
            delivery_preference,
            source_is_dm=request.metadata.get("source_is_dm"),
        ),
    )


async def execute_reminder_delivery(
    request: ReminderDeliveryRequest,
    *,
    bot: Any = None,
) -> ReminderDeliveryResult:
    if request.runtime_job_id:
        await begin_runtime_job(request.runtime_job_id)

    delivery_result = await deliver_reminder(request, bot=bot)
    if not delivery_result.sent:
        if request.runtime_job_id:
            await mark_runtime_job_delivery_failed(request.runtime_job_id)
        return delivery_result

    stored = await log_reminder_delivery(request, delivery_result)
    if str(request.platform or "").strip().lower() == WEB_DOOR and not stored:
        # Fable's review (2026-10-10): on the web page the row IS the
        # delivery. A row that could not be written never reached the page,
        # so it is not sent: the retry and dead-letter rules apply, and
        # nothing goes into her transcript.
        if request.runtime_job_id:
            await mark_runtime_job_delivery_failed(request.runtime_job_id)
        return dataclasses.replace(delivery_result, sent=False, delivered_text="")
    # 2026-08-06 (arbiter build) — custody, NOT arbitration: the delivered
    # words join the discord:<channel> transcript her replies read.
    # Failure-swallowed; a custody hiccup can never block an alarm.
    try:
        await _transcribe_reminder_to_discord(request, delivery_result)
    except Exception:
        logger.debug(
            "reminder transcript leg failed (custody only; alarm unaffected)",
            exc_info=True,
        )
    if request.runtime_job_id:
        await complete_runtime_job_delivery(request.runtime_job_id, delivery_result)
    return delivery_result


async def begin_runtime_job(runtime_job_id: str) -> None:
    try:
        await runtime_control.begin_job(
            job_id=str(runtime_job_id),
            metadata={"dispatch_started_at": time.time()},
        )
    except Exception:
        logger.debug(
            "Failed to mark deferred job running %s",
            runtime_job_id,
            exc_info=True,
        )


async def mark_runtime_job_delivery_failed(runtime_job_id: str) -> None:
    try:
        await runtime_control.update_job(
            job_id=str(runtime_job_id),
            status="queued",
            last_error="delivery_failed",
            metadata={"last_delivery_failed_at": time.time()},
        )
    except Exception:
        logger.debug(
            "Failed to requeue deferred job %s",
            runtime_job_id,
            exc_info=True,
        )


async def complete_runtime_job_delivery(
    runtime_job_id: str,
    delivery_result: ReminderDeliveryResult,
) -> None:
    try:
        await runtime_control.complete_job(
            job_id=str(runtime_job_id),
            status="completed",
            metadata={
                "delivered_in": str(delivery_result.delivered_in or ""),
                "delivery_channel_id": int(delivery_result.delivery_channel_id or 0),
            },
        )
    except Exception:
        logger.debug(
            "Failed to complete deferred job %s",
            runtime_job_id,
            exc_info=True,
        )


async def deliver_reminder(
    request: ReminderDeliveryRequest,
    *,
    bot: Any = None,
) -> ReminderDeliveryResult:
    delivery_channel_id = int(request.channel_id or 0)
    delivered_in = ""
    sent = False
    platform = str(request.platform or "").strip().lower()
    door_not_ready = False

    # Final review, Important 3: a chat door that is not up yet — not
    # registered, or Discord's gateway still connecting after a restart — is
    # asked BEFORE the fire turn. Inside the boot grace window waiting for it
    # is not a failed delivery (send_reminder_for_target decides, R17a), and
    # either way a turn composed for a door that cannot take it would only be
    # thrown away. Only the envelope path is asked
    # (the bot path has its own handle), and only the two chat doors: they
    # are the doors that come up after boot; any other door with no adapter
    # still fails and dead-letters as before.
    _discord_fallback_ok = platform != "discord" or bot is None
    if (
        platform in CHAT_DOORS
        and _discord_fallback_ok
        and request.delivery_surface_id
        and not await channel_runtime.door_ready(platform)
    ):
        return ReminderDeliveryResult(
            sent=False,
            delivered_in="",
            delivery_channel_id=delivery_channel_id,
            delivery_surface_id=str(request.delivery_surface_id or ""),
            delivered_text="",
            door_not_ready=True,
        )

    # Regenerate the reminder message text via the new spine. The
    # synthesized turn flows through process_turn → decide →
    # respond_tier0 so the BehaviorFrame shapes the reminder's tone
    # (calm vs. brisk, with sensitivity to the current emotional
    # state). Delivery still uses the existing channel infrastructure
    # below.
    try:
        # Local import: scheduler.py imports binding.py at top, so resolving
        # fire_reminder lazily here avoids a circular import.
        from core.reminders.scheduler import fire_reminder
        spine_result = await fire_reminder(
            _reminder_payload_from_request(request),
            owner_id=int(request.user_id or 1),
        )
        if spine_result is not None and getattr(spine_result, "response_text", ""):
            request = dataclasses.replace(
                request, reminder_message=spine_result.response_text,
            )
    except Exception:
        logger.debug(
            "spine routing for reminder %s failed; using legacy text",
            request.reminder_id,
            exc_info=True,
        )

    if platform == "discord" and bot is not None:
        sent, delivered_in, delivery_channel_id = await _deliver_discord_reminder(
            request,
            bot=bot,
            delivery_channel_id=delivery_channel_id,
        )

    # Web-page reminders (2026-10-10): the web page has no live connection.
    # Its delivery IS the conversation row log_reminder_delivery writes next
    # (platform "dashboard", channel 0, type "deferred_action"), which the
    # page reads when it opens and every 15 seconds. The in-memory dashboard
    # buffer below lost these (a 5-minute life, under a key the page never
    # drains), so a web reminder never touches it. Blank text is nothing to
    # deliver, as on every other door.
    if platform == WEB_DOOR:
        sent = bool(str(request.reminder_message or "").strip())
        delivered_in = request.delivery_preference if sent else ""

    # 2026-07-10: nothing wires the live Discord client into the scheduler
    # (the Wave-5 registrar deletion took it; ce5c0e4 restored scheduler
    # START, not the bot handle) — so bot is always None in production and
    # the branch above never runs. Discord therefore may use the
    # channel_runtime envelope path below — the same live route proactive
    # outreach uses (core/turn/proactive.py:730). The bot path, when a bot
    # IS provided (tests, future rewiring), still wins and keeps its
    # dm-preference handling; the envelope fallback stays out of its way
    # (``_discord_fallback_ok``, computed above).
    if (
        not sent
        and platform not in ("local", WEB_DOOR)
        and _discord_fallback_ok
        and request.delivery_surface_id
    ):
        try:
            outcome = await channel_runtime.send_envelope(
                platform=str(request.platform),
                surface_id=request.delivery_surface_id,
                # The envelope says what it is (the mobile door, 2026-10-13): the
                # phone app shows a reminder with a bell. Other doors ignore it.
                envelope=ChannelDeliveryEnvelope(text=request.reminder_message, metadata={"reminder": True}),
            )
            # Fix plan 5.8: an adapter that answers ``sent: False`` without
            # raising had nothing to send (the Discord and Telegram rule for
            # blank text) — not a delivery either. A clean return with no
            # verdict (the dashboard buffer) still is.
            if isinstance(outcome, dict) and outcome.get("sent") is False:
                logger.debug(
                    "Channel-runtime reminder delivery sent nothing for %s:%s",
                    request.platform,
                    request.delivery_surface_id,
                )
            else:
                sent = True
                delivered_in = request.delivery_preference
        except DoorNotReady:
            # The door went down between the check above and the send: still
            # not a refusal, and still not counted — on a chat door.
            door_not_ready = platform in CHAT_DOORS
            logger.debug(
                "Channel-runtime reminder delivery: %s door not ready for %s",
                request.platform,
                request.delivery_surface_id,
                exc_info=True,
            )
        except Exception:
            logger.debug(
                "Channel-runtime reminder delivery failed for %s:%s",
                request.platform,
                request.delivery_surface_id,
                exc_info=True,
            )

    return ReminderDeliveryResult(
        sent=bool(sent),
        delivered_in=str(delivered_in or ""),
        delivery_channel_id=int(delivery_channel_id or 0),
        delivery_surface_id=str(request.delivery_surface_id or ""),
        delivered_text=str(request.reminder_message or "") if sent else "",
        door_not_ready=door_not_ready and not sent,
    )


def _recorded_reminder_text(request: "ReminderDeliveryRequest",
                            delivery_result: "ReminderDeliveryResult") -> str:
    """§16: what the conversation record says she said — the delivered text
    first (her composed reply), the stored message only as a fallback."""
    return str(getattr(delivery_result, "delivered_text", "") or request.reminder_message or "")


async def _transcribe_reminder_to_discord(
    request: "ReminderDeliveryRequest",
    delivery_result: "ReminderDeliveryResult",
) -> None:
    """2026-08-06 arbiter build — custody, NOT arbitration. Reminders stay
    exempt from the arbiter forever (never gated, never counted, schedule
    untouched); this only puts what she said into the <door>:<channel>
    transcript her replies read, so she remembers delivering it. Before
    this, deliveries lived only in reminder_fire_1.jsonl — a session key
    her chat turns never load — and she reconstructed her own
    deliveries from DB memory. Assistant-only append (the 07-30 rule).

    Telegram door (2026-09-14, task 1): the hole was never about Discord —
    a reminder delivered on ANY door she talks through has to reach that
    door's transcript. Doors she does not converse on (a local alarm) still
    write nothing here.

    The web page (2026-10-10): a reminder delivered there goes into the web
    chat's own transcript (``dashboard:<owner>``), so her next web reply knows
    she sent it. Not now if a web run is in progress: an append landing
    between a tool call and its result breaks the next model call, so it is
    written by ``_transcribe_when_idle`` once that run has ended, in the
    background, never holding up the reminder loop."""
    platform = str(request.platform or "").strip().lower()
    if platform == WEB_DOOR:
        import os
        from pathlib import Path

        from core.session_keys import session_key_for

        root = Path(os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions")
        task = asyncio.create_task(_transcribe_when_idle(
            session_key_for(WEB_DOOR, None, int(request.user_id)), root,
            str(request.reminder_message or ""),
        ))
        _PENDING_TRANSCRIPTS.add(task)
        task.add_done_callback(_PENDING_TRANSCRIPTS.discard)
        return
    if platform not in CHAT_DOORS:
        return
    sid = str(
        delivery_result.delivery_surface_id
        or delivery_result.delivery_channel_id
        or ""
    ).strip()
    if not sid:
        return
    import os
    from pathlib import Path

    from core.agent.runner import _open_session_with_exchange

    root = Path(
        os.environ.get("PALLI_AGENT_SESSIONS_ROOT") or "data/agent_sessions"
    )
    await _open_session_with_exchange(
        f"{platform}:{sid}", root, "", str(request.reminder_message or "")
    )


# The web transcript writes waiting for a run to end (strong references, so
# a pending write is never collected before it runs).
_PENDING_TRANSCRIPTS: set = set()


async def _transcribe_when_idle(
    session_key: str,
    root: Any,
    text: str,
    *,
    poll_seconds: float = 1.0,
    max_wait_seconds: float = 1800.0,
) -> None:
    """Append her reminder to ``session_key``'s transcript once no run is in
    progress there (the runner sets a run phase for the whole run and clears
    it at the end). A run still going after ``max_wait_seconds`` gets nothing
    written into it: the conversation row already holds the reminder, and
    the page already shows it."""
    from core.agent import steering

    waited = 0.0
    while steering.get_run_phase(session_key):
        if waited >= max_wait_seconds:
            logger.warning(
                "reminder transcript: %s still had a run after %.0fs; not written",
                session_key, waited,
            )
            return
        await asyncio.sleep(poll_seconds)
        waited += poll_seconds
    try:
        from core.agent.runner import _open_session_with_exchange

        await _open_session_with_exchange(session_key, root, "", text)
    except Exception:
        logger.warning("reminder transcript: %s append failed", session_key, exc_info=True)


async def log_reminder_delivery(
    request: ReminderDeliveryRequest,
    delivery_result: ReminderDeliveryResult,
) -> bool:
    """Write the "she said it" conversation row. True when it was written:
    the web page's delivery depends on it (2026-10-10)."""
    try:
        thread_id = await _resolve_reminder_thread_id(
            request=request,
            delivery_result=delivery_result,
        )
        conversation_metadata = {
            "type": "deferred_action",
            "kind": str(request.kind),
            "reminder_id": int(request.reminder_id),
            "scheduled_time": float(request.scheduled_time),
            "delivered_in": str(delivery_result.delivered_in or request.delivery_preference),
            "source_channel_id": int(request.channel_id or 0),
            "source_surface_id": str(request.delivery_surface_id or ""),
        }
        if request.account_user_id is not None:
            conversation_metadata["account_user_id"] = int(request.account_user_id)
        for key in (
            "identity_delivery_bias",
            "identity_delivery_tone",
            "identity_urgency_style",
            "identity_followthrough_style",
            "reminder_identity_posture",
        ):
            value = request.metadata.get(key)
            if value:
                conversation_metadata[key] = value

        await brain.store_conversation(
            user_id=int(request.user_id),
            platform=str(request.platform),
            channel_id=int(delivery_result.delivery_channel_id),
            surface_id=str(delivery_result.delivery_surface_id),
            thread_id=thread_id,
            role="assistant",
            content=_recorded_reminder_text(request, delivery_result),
            metadata=conversation_metadata,
        )
        return True
    except Exception:
        # Delivery is the important part; don't fail the reminder if logging fails.
        logger.debug(
            "Failed to store reminder delivery in conversation history",
            exc_info=True,
        )
        return False


async def _resolve_reminder_thread_id(
    *,
    request: ReminderDeliveryRequest,
    delivery_result: ReminderDeliveryResult,
) -> Optional[str]:
    owner_user_id = _coerce_optional_int(request.account_user_id)
    if owner_user_id is None:
        return None
    try:
        clean_platform = str(request.platform or "local").strip().lower() or "local"
        surface_id = (
            str(delivery_result.delivery_surface_id or request.delivery_surface_id or "")
            .strip()
            or None
        )
        delivered_in = str(delivery_result.delivered_in or request.delivery_preference or "").strip().lower()
        thread = await continuity_service.resolve_surface_thread(
            owner_user_id=int(owner_user_id),
            platform=clean_platform,
            channel_id=int(delivery_result.delivery_channel_id or request.channel_id or 0),
            surface_id=surface_id,
            is_dm=(clean_platform == "local") or (delivered_in == "dm"),
            maintenance=False,
        )
        return str((thread or {}).get("thread_id") or "").strip() or None
    except Exception:
        logger.debug(
            "Failed to resolve reminder continuity thread for owner %s",
            owner_user_id,
            exc_info=True,
        )
        return None


async def _deliver_discord_reminder(
    request: ReminderDeliveryRequest,
    *,
    bot: Any,
    delivery_channel_id: int,
) -> tuple[bool, str, int]:
    if request.delivery_preference == "dm":
        dm_ok, dm_channel_id = await _send_discord_dm(
            bot,
            user_id=request.user_id,
            reminder_message=request.reminder_message,
            delivery_channel_id=delivery_channel_id,
        )
        if dm_ok:
            return True, "dm", dm_channel_id

        channel_ok = await _send_discord_channel(
            bot,
            channel_id=request.channel_id,
            reminder_message=request.reminder_message,
        )
        if channel_ok:
            return True, "channel", int(request.channel_id or 0)
        return False, "", delivery_channel_id

    channel_ok = await _send_discord_channel(
        bot,
        channel_id=request.channel_id,
        reminder_message=request.reminder_message,
    )
    if channel_ok:
        return True, "channel", int(request.channel_id or 0)

    dm_ok, dm_channel_id = await _send_discord_dm(
        bot,
        user_id=request.user_id,
        reminder_message=request.reminder_message,
        delivery_channel_id=delivery_channel_id,
    )
    if dm_ok:
        return True, "dm", dm_channel_id
    return False, "", delivery_channel_id


async def _send_discord_channel(
    bot: Any,
    *,
    channel_id: int,
    reminder_message: str,
) -> bool:
    channel = None
    try:
        channel = bot.get_channel(int(channel_id)) if channel_id else None
    except Exception:
        channel = None

    if channel is None and channel_id:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            channel = None

    if channel is None:
        return False

    try:
        await channel.send(reminder_message)
        return True
    except Exception:
        return False


async def _send_discord_dm(
    bot: Any,
    *,
    user_id: int,
    reminder_message: str,
    delivery_channel_id: int,
) -> tuple[bool, int]:
    user_obj = None
    try:
        user_obj = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
    except Exception:
        user_obj = None
    if user_obj is None:
        return False, delivery_channel_id

    try:
        try:
            dm_channel = getattr(user_obj, "dm_channel", None) or await user_obj.create_dm()
        except Exception:
            dm_channel = None
        await user_obj.send(reminder_message)
        try:
            if dm_channel and getattr(dm_channel, "id", None):
                delivery_channel_id = int(dm_channel.id)
        except Exception:
            pass
        return True, delivery_channel_id
    except Exception:
        return False, delivery_channel_id


def _normalize_metadata(metadata: Any) -> Dict[str, Any]:
    if isinstance(metadata, dict):
        return dict(metadata)
    if metadata is None:
        return {}
    if isinstance(metadata, str):
        try:
            loaded = json.loads(metadata)
        except Exception:
            return {}
        if isinstance(loaded, dict):
            return dict(loaded)
    return {}


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _build_reminder_prefix(metadata: Dict[str, Any]) -> str:
    delivery_tone = str(metadata.get("identity_delivery_tone") or "").strip().lower()
    urgency_style = str(metadata.get("identity_urgency_style") or "").strip().lower()

    if urgency_style == "firm":
        return "Important reminder"
    if delivery_tone == "gentle":
        return "Gentle reminder"
    if delivery_tone == "supportive":
        return "Friendly reminder"
    return "Reminder"
