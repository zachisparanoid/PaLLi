from __future__ import annotations

import time
from typing import Any, Dict, Optional

import pytest

import core.reminders.binding as reminder_schedule_coordination
from core.reminders.scheduler import ScheduledReminder
from core.reminders.parser import ReminderTimeParseResult


class _FakeTarget:
    def __init__(self) -> None:
        self.confidence_threshold = 0.6
        self.analysis_result: Dict[str, Any] = {
            "is_deferred_request": True,
            "kind": "reminder",
            "confidence": 0.95,
            "reminder_text": "Call mom",
            "time_expression": "in 1 hour",
        }
        self.parse_result = ReminderTimeParseResult(
            status="ok",
            timestamp=time.time() + 3600,
            reason_code="",
        )
        self.feedback_text = "Please clarify the time."
        self.store_result = 77
        self.analyzed_messages: list[str] = []
        self.parsed_calls: list[Dict[str, Any]] = []
        self.stored_calls: list[Dict[str, Any]] = []
        self.runtime_updates: list[Dict[str, Any]] = []
        self.events: list[Dict[str, Any]] = []

    async def _analyze_deferred_request(self, message: str) -> Dict[str, Any]:
        self.analyzed_messages.append(message)
        return dict(self.analysis_result)

    async def _parse_time_expression_result(
        self,
        time_expression: str,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
    ) -> ReminderTimeParseResult:
        self.parsed_calls.append(
            {
                "time_expression": time_expression,
                "user_id": user_id,
                "platform": platform,
            }
        )
        return self.parse_result

    def _parse_relative_duration_seconds(self, text: str) -> Optional[float]:
        if text == "90 seconds":
            return 90.0
        return None

    def _default_deferred_content(self, kind: str) -> str:
        return "Timer finished." if kind == "timer" else "Reminder"

    def _build_time_parse_feedback(
        self,
        *,
        time_expression: str,
        parse_result: ReminderTimeParseResult,
    ) -> str:
        return f"{self.feedback_text} ({time_expression}/{parse_result.reason_code})"

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
    ) -> int:
        self.stored_calls.append(
            {
                "user_id": user_id,
                "platform": platform,
                "channel_id": channel_id,
                "reminder_text": reminder_text,
                "scheduled_time": scheduled_time,
                "kind": kind,
                "metadata": dict(metadata or {}),
                "surface_id": surface_id,
            }
        )
        return self.store_result

    async def _emit_runtime_update_for_subject(
        self,
        *,
        user_id: Optional[int] = None,
        platform: Optional[str] = None,
        account_user_id: Optional[int] = None,
    ) -> None:
        self.runtime_updates.append(
            {
                "user_id": user_id,
                "platform": platform,
                "account_user_id": account_user_id,
            }
        )

    def _record_runtime_event(self, kind: str, **payload: Any) -> None:
        event = {"kind": kind}
        event.update(payload)
        self.events.append(event)


@pytest.mark.asyncio
async def test_schedule_deferred_action_for_target_attaches_runtime_binding_and_wakes():
    target = _FakeTarget()
    fixed_time = time.time() + 3600
    target.parse_result = ReminderTimeParseResult(
        status="ok",
        timestamp=fixed_time,
        reason_code="",
    )
    captured: Dict[str, Any] = {"wake_count": 0}

    async def fake_ensure_surface_session(**kwargs):
        captured["session_kwargs"] = kwargs
        return {"session_key": "sess-1"}

    async def fake_create_job(**kwargs):
        captured["job_kwargs"] = kwargs
        return {"job_id": "job-9"}

    async def fake_update_runtime_link(**kwargs):
        captured["runtime_link_kwargs"] = kwargs

    result = await reminder_schedule_coordination.schedule_deferred_action_for_target(
        target=target,
        request=reminder_schedule_coordination.DeferredActionScheduleRequest(
            user_id=8,
            platform="discord",
            channel_id=11,
            message="remind me to call mom in 1 hour",
            analysis={
                "is_deferred_request": True,
                "kind": "reminder",
                "confidence": 0.99,
                "reminder_text": "Call mom",
                "time_expression": "in 1 hour",
            },
            source_is_dm=False,
            account_user_id=19,
            surface_id="voice:test",
        ),
        scheduled_reminder_factory=ScheduledReminder,
        wake_scheduler=lambda: captured.__setitem__(
            "wake_count", int(captured["wake_count"]) + 1
        ),
        ensure_surface_session=fake_ensure_surface_session,
        create_job=fake_create_job,
        update_scheduled_reminder_runtime_link=fake_update_runtime_link,
        now=lambda: 1234.0,
    )

    assert result is not None
    assert result == ScheduledReminder(
        id=77,
        user_id=8,
        platform="discord",
        channel_id=11,
        surface_id="voice:test",
        reminder_text="Call mom",
        scheduled_time=fixed_time,
        created_time=1234.0,
        is_completed=False,
        kind="reminder",
        runtime_job_id="job-9",
        runtime_session_key="sess-1",
        metadata={
            "is_deferred_request": True,
            "kind": "reminder",
            "confidence": 0.99,
            "content_text": "Call mom",
            "reminder_text": "Call mom",
            "time_expression": "in 1 hour",
            "source_is_dm": False,
            "account_user_id": 19,
            "delivery_preference": "dm",
            "parse_status": "ok",
            "parse_reason_code": "",
            "normalized_scheduled_time": fixed_time,
            "runtime_job_id": "job-9",
            "runtime_session_key": "sess-1",
        },
    )
    assert captured["session_kwargs"] == {
        "owner_user_id": 19,
        "platform": "discord",
        "channel_id": 11,
        "is_dm": False,
        "status": "ready",
        "metadata": {"origin": "deferred_action", "kind": "reminder"},
        "surface_id": "voice:test",
    }
    assert captured["job_kwargs"] == {
        "runtime_session_key": "sess-1",
        "owner_user_id": 19,
        "job_type": "deferred_action",
        "subtype": "reminder",
        "status": "scheduled",
        "request_text": "Call mom",
        "scheduled_for": fixed_time,
        "source_table": "scheduled_reminders",
        "source_id": "77",
        "metadata": {
            "platform": "discord",
            "channel_id": 11,
            "surface_id": "voice:test",
            "delivery_preference": "dm",
        },
    }
    assert captured["runtime_link_kwargs"] == {
        "reminder_id": 77,
        "runtime_job_id": "job-9",
        "runtime_session_key": "sess-1",
    }
    assert captured["wake_count"] == 1
    assert target.runtime_updates == [
        {"user_id": 8, "platform": "discord", "account_user_id": 19}
    ]
    assert any(event["kind"] == "scheduled" and event["reminder_id"] == 77 for event in target.events)


@pytest.mark.asyncio
async def test_schedule_deferred_action_for_target_uses_analysis_hook_and_stops_on_parse_failure():
    target = _FakeTarget()
    target.analysis_result = {
        "is_deferred_request": True,
        "kind": "reminder",
        "confidence": 0.91,
        "reminder_text": "Call mom",
        "time_expression": "monday at 8",
    }
    target.parse_result = ReminderTimeParseResult(
        status="ambiguous",
        timestamp=None,
        reason_code="ambiguous_bare_hour",
    )
    captured = {"wake_count": 0}

    async def should_not_run(**kwargs):
        raise AssertionError("runtime binding should not run when time parsing fails")

    result = await reminder_schedule_coordination.schedule_deferred_action_for_target(
        target=target,
        request=reminder_schedule_coordination.DeferredActionScheduleRequest(
            user_id=5,
            platform="discord",
            channel_id=9,
            message="remind me monday at 8 to call mom",
        ),
        scheduled_reminder_factory=ScheduledReminder,
        wake_scheduler=lambda: captured.__setitem__(
            "wake_count", int(captured["wake_count"]) + 1
        ),
        ensure_surface_session=should_not_run,
        create_job=should_not_run,
        update_scheduled_reminder_runtime_link=should_not_run,
    )

    assert result is None
    assert target.analyzed_messages == ["remind me monday at 8 to call mom"]
    assert target.parsed_calls == [
        {
            "time_expression": "monday at 8",
            "user_id": 5,
            "platform": "discord",
        }
    ]
    assert target.stored_calls == []
    assert target.runtime_updates == []
    assert captured["wake_count"] == 0


