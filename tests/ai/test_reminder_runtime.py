from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime as real_datetime
from typing import Any

import pytest
import pytz

import core.reminders.binding as reminder_delivery
import core.reminders.binding as reminder_runtime
from core.reminders.scheduler import ScheduledReminder

NY_TZ = pytz.timezone("America/New_York")


@dataclass
class _FakeTarget:
    loop_state: str = "idle"
    last_check_started_at: float | None = None
    last_check_completed_at: float | None = None
    last_error: str = ""
    last_dispatch_attempt: dict[str, Any] | None = None
    emitted_updates: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)

    def _record_runtime_event(self, kind: str, **payload: Any) -> None:
        event = {"kind": kind}
        event.update(payload)
        self.recent_events.append(event)

    async def _emit_runtime_update_for_subject(self, **kwargs: Any) -> None:
        self.emitted_updates.append(kwargs)


@dataclass
class _FakeLoopTarget:
    is_running: bool = False
    loop_state: str = "idle"
    last_check_started_at: float | None = None
    last_check_completed_at: float | None = None
    last_error: str = ""
    last_dispatch_attempt: dict[str, Any] | None = None
    recent_events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=20)
    )
    check_task: asyncio.Task[None] | None = None

    def _record_runtime_event(self, kind: str, **payload: Any) -> None:
        reminder_runtime.record_runtime_event(self.recent_events, kind, **payload)

    async def _emit_runtime_update_for_subject(self, **kwargs: Any) -> None:
        return None


class _FakeReminderConn:
    def __init__(self, row: Any = None, *, fail_fetch: bool = False):
        self.row = row
        self.fail_fetch = fail_fetch
        self.closed = False
        self.query = ""

    async def fetchrow(self, query: str):
        self.query = query
        if self.fail_fetch:
            raise RuntimeError("fetch failed")
        return self.row

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_should_reduce_reminder_interruptions_respects_quiet_hours_window():
    async def fake_resolve_user_timezone(user_id: int, platform: str):
        assert user_id == 7
        assert platform == "discord"
        return NY_TZ

    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = NY_TZ.localize(real_datetime(2026, 3, 22, 23, 30, 0))
            if tz is None:
                return base.replace(tzinfo=None)
            return base.astimezone(tz)

    should_reduce = await reminder_runtime.should_reduce_reminder_interruptions(
        user_id=7,
        platform="discord",
        action_preferences={
            "interruption_threshold": "normal",
            "quiet_hours_mode": "adaptive",
        },
        resolve_user_timezone=fake_resolve_user_timezone,
        datetime_cls=FixedDateTime,
    )

    assert should_reduce is True


@pytest.mark.asyncio
async def test_send_reminder_for_target_softens_delivery_and_updates_dispatch_state():
    target = _FakeTarget()
    captured: dict[str, Any] = {}

    def fake_prepare_reminder_delivery_request(reminder_record: Any):
        captured["reminder_record"] = reminder_record
        return reminder_delivery.ReminderDeliveryRequest(
            reminder_id=41,
            user_id=14,
            platform="discord",
            channel_id=88,
            delivery_surface_id="discord:channel:88",
            reminder_text="start the laundry",
            reminder_message="Reminder: start the laundry",
            scheduled_time=1234.0,
            kind="reminder",
            metadata={"delivery_preference": "channel", "account_user_id": 55},
            delivery_preference="channel",
            runtime_job_id="",
            account_user_id=55,
        )

    def fake_override_delivery_preference(request, delivery_preference: str):
        captured["override_delivery_preference"] = delivery_preference
        return reminder_delivery.override_delivery_preference(request, delivery_preference)

    async def fake_execute_reminder_delivery(request):
        captured["delivery_request"] = request
        return reminder_delivery.ReminderDeliveryResult(
            sent=True,
            delivered_in=request.delivery_preference,
            delivery_channel_id=request.channel_id,
            delivery_surface_id=request.delivery_surface_id,
        )

    async def fake_get_action_policy(**kwargs):
        captured["action_policy_kwargs"] = kwargs
        return {"interruption_threshold": "urgent_only", "quiet_hours_mode": "off"}

    async def fake_should_reduce_interruptions(**kwargs):
        captured["should_reduce_kwargs"] = kwargs
        return True

    sent = await reminder_runtime.send_reminder_for_target(
        target=target,
        reminder_record={"id": 41},
        prepare_reminder_delivery_request=fake_prepare_reminder_delivery_request,
        override_delivery_preference=fake_override_delivery_preference,
        execute_reminder_delivery=fake_execute_reminder_delivery,
        get_action_policy=fake_get_action_policy,
        should_reduce_interruptions=fake_should_reduce_interruptions,
        now=lambda: 2000.0,
    )

    assert sent is True
    assert captured["action_policy_kwargs"] == {
        "user_id": 14,
        "platform": "discord",
        "account_user_id": 55,
    }
    assert captured["should_reduce_kwargs"]["action_preferences"] == {
        "interruption_threshold": "urgent_only",
        "quiet_hours_mode": "off",
    }
    assert captured["override_delivery_preference"] == "dm"
    assert captured["delivery_request"].delivery_preference == "dm"
    assert target.last_dispatch_attempt == {
        "reminder_id": 41,
        "user_id": 14,
        "platform": "discord",
        "delivery_preference": "dm",
        "started_at": 2000.0,
        "success": True,
        "delivered_in": "dm",
        "delivery_channel_id": 88,
        "ended_at": 2000.0,
    }
    assert target.emitted_updates == [
        {"user_id": 14, "platform": "discord", "account_user_id": 55}
    ]
    assert any(
        event.get("kind") == "delivery_softened" and event.get("reminder_id") == 41
        for event in target.recent_events
    )
    assert any(
        event.get("kind") == "delivered" and event.get("delivered_in") == "dm"
        for event in target.recent_events
    )


@pytest.mark.asyncio
async def test_check_due_reminders_for_target_marks_only_successful_deliveries():
    target = _FakeTarget()
    captured: dict[str, Any] = {"completed_ids": [], "sent_ids": []}
    due_records = [
        {"id": 17, "user_id": 8, "platform": "discord"},
        {"id": 18, "user_id": 8, "platform": "discord"},
    ]

    async def fake_list_due_scheduled_reminders(*, as_of: float, limit=None):
        captured["list_due_kwargs"] = {"as_of": as_of, "limit": limit}
        return due_records

    async def fake_mark_scheduled_reminder_completed(*, reminder_id: int):
        captured["completed_ids"].append(reminder_id)

    async def fake_send_reminder(reminder_record):
        reminder_id = int(reminder_record["id"])
        captured["sent_ids"].append(reminder_id)
        return reminder_id == 17

    await reminder_runtime.check_due_reminders_for_target(
        target=target,
        list_due_scheduled_reminders=fake_list_due_scheduled_reminders,
        mark_scheduled_reminder_completed=fake_mark_scheduled_reminder_completed,
        send_reminder=fake_send_reminder,
        now=lambda: 2000.0,
    )

    assert captured["list_due_kwargs"] == {"as_of": 2000.0, "limit": None}
    assert captured["sent_ids"] == [17, 18]
    assert captured["completed_ids"] == [17]
    assert target.loop_state == "idle"
    assert target.last_check_started_at == 2000.0
    assert target.last_check_completed_at == 2000.0
    assert target.emitted_updates == [{}]
    assert any(
        event.get("kind") == "due_batch" and event.get("count") == 2
        for event in target.recent_events
    )


@pytest.mark.asyncio
async def test_check_due_reminders_flips_followthrough_for_workflow_origin():
    """QUE-HIGH-1 acceptance criterion 1: a fired reminder whose metadata traces
    back to a workflow_followthrough must mark the originating followthrough as
    'delivered' so the lifecycle closes."""
    target = _FakeTarget()
    captured: dict[str, Any] = {
        "completed_ids": [],
        "sent_ids": [],
        "delivered_followthrough_kwargs": [],
    }
    due_records = [
        {
            "id": 51,
            "user_id": 8,
            "platform": "local",
            "metadata": {
                "origin": "workflow_followthrough",
                "followthrough_id": "wff_alpha",
            },
        }
    ]

    async def fake_list_due_scheduled_reminders(*, as_of: float, limit=None):
        return due_records

    async def fake_mark_scheduled_reminder_completed(*, reminder_id: int):
        captured["completed_ids"].append(reminder_id)

    async def fake_send_reminder(reminder_record):
        captured["sent_ids"].append(int(reminder_record["id"]))
        return True

    async def fake_mark_followthrough_delivered(**kwargs):
        captured["delivered_followthrough_kwargs"].append(dict(kwargs))

    await reminder_runtime.check_due_reminders_for_target(
        target=target,
        list_due_scheduled_reminders=fake_list_due_scheduled_reminders,
        mark_scheduled_reminder_completed=fake_mark_scheduled_reminder_completed,
        send_reminder=fake_send_reminder,
        mark_followthrough_delivered=fake_mark_followthrough_delivered,
        now=lambda: 3000.0,
    )

    assert captured["sent_ids"] == [51]
    assert captured["completed_ids"] == [51]
    assert captured["delivered_followthrough_kwargs"] == [
        {"followthrough_id": "wff_alpha"}
    ]


@pytest.mark.asyncio
async def test_check_due_reminders_skips_followthrough_flip_for_non_followthrough_origin():
    """QUE-HIGH-1 acceptance criterion 2: reminders that did NOT originate from a
    followthrough must not write to operator_followthrough_items."""
    target = _FakeTarget()
    captured: dict[str, Any] = {"delivered_followthrough_kwargs": []}
    due_records = [
        {"id": 60, "user_id": 8, "platform": "local", "metadata": {}},
        {
            "id": 61,
            "user_id": 8,
            "platform": "local",
            "metadata": {"origin": "create_reminder"},
        },
        {
            "id": 62,
            "user_id": 8,
            "platform": "local",
            "metadata": {
                "origin": "workflow_followthrough",
                # followthrough_id missing — must not flip anything
            },
        },
        # Code-review #6: explicit None metadata — exercises the
        # `normalize_reminder_metadata(None) -> {}` path through
        # reminder_record_value. Must not raise and must not flip.
        {"id": 63, "user_id": 8, "platform": "local", "metadata": None},
        # Code-review #6: missing metadata key entirely — exercises the
        # default-fallback path through reminder_record_value. Same
        # contract: must not raise and must not flip.
        {"id": 64, "user_id": 8, "platform": "local"},
    ]

    async def fake_list_due_scheduled_reminders(*, as_of: float, limit=None):
        return due_records

    async def fake_mark_scheduled_reminder_completed(*, reminder_id: int):
        return None

    async def fake_send_reminder(reminder_record):
        return True

    async def fake_mark_followthrough_delivered(**kwargs):
        captured["delivered_followthrough_kwargs"].append(dict(kwargs))

    await reminder_runtime.check_due_reminders_for_target(
        target=target,
        list_due_scheduled_reminders=fake_list_due_scheduled_reminders,
        mark_scheduled_reminder_completed=fake_mark_scheduled_reminder_completed,
        send_reminder=fake_send_reminder,
        mark_followthrough_delivered=fake_mark_followthrough_delivered,
        now=lambda: 3100.0,
    )

    assert captured["delivered_followthrough_kwargs"] == []


@pytest.mark.asyncio
async def test_check_due_reminders_followthrough_flip_is_idempotent():
    """QUE-HIGH-1 acceptance criterion 3: firing twice (or otherwise re-invoking
    the flip) must be a safe no-op. The runtime hands the call to the brain;
    each call must succeed and the second call's argument must equal the first."""
    target = _FakeTarget()
    captured: dict[str, Any] = {"calls": []}

    async def fake_list_due_scheduled_reminders(*, as_of: float, limit=None):
        return [
            {
                "id": 71,
                "user_id": 8,
                "platform": "local",
                "metadata": {
                    "origin": "workflow_followthrough",
                    "followthrough_id": "wff_idem",
                },
            }
        ]

    async def fake_mark_scheduled_reminder_completed(*, reminder_id: int):
        return None

    async def fake_send_reminder(reminder_record):
        return True

    async def fake_mark_followthrough_delivered(**kwargs):
        captured["calls"].append(dict(kwargs))

    for _ in range(2):
        await reminder_runtime.check_due_reminders_for_target(
            target=target,
            list_due_scheduled_reminders=fake_list_due_scheduled_reminders,
            mark_scheduled_reminder_completed=fake_mark_scheduled_reminder_completed,
            send_reminder=fake_send_reminder,
            mark_followthrough_delivered=fake_mark_followthrough_delivered,
            now=lambda: 3200.0,
        )

    assert captured["calls"] == [
        {"followthrough_id": "wff_idem"},
        {"followthrough_id": "wff_idem"},
    ]
    # Loop must end in idle state both times — no exception leaked through.
    assert target.loop_state == "idle"


@pytest.mark.asyncio
async def test_check_due_reminders_followthrough_flip_failure_is_isolated():
    """QUE-HIGH-1 acceptance criterion 4: if the followthrough flip fails (e.g.
    DB hiccup), the reminder must still complete cleanly. The reminder firing is
    the load-bearing event; the flip is bookkeeping."""
    target = _FakeTarget()
    captured: dict[str, Any] = {
        "completed_ids": [],
        "sent_ids": [],
        "flip_attempts": 0,
    }

    async def fake_list_due_scheduled_reminders(*, as_of: float, limit=None):
        return [
            {
                "id": 81,
                "user_id": 8,
                "platform": "local",
                "metadata": {
                    "origin": "workflow_followthrough",
                    "followthrough_id": "wff_db_hiccup",
                },
            }
        ]

    async def fake_mark_scheduled_reminder_completed(*, reminder_id: int):
        captured["completed_ids"].append(reminder_id)

    async def fake_send_reminder(reminder_record):
        captured["sent_ids"].append(int(reminder_record["id"]))
        return True

    async def fake_mark_followthrough_delivered(**kwargs):
        captured["flip_attempts"] += 1
        raise RuntimeError("simulated DB hiccup")

    await reminder_runtime.check_due_reminders_for_target(
        target=target,
        list_due_scheduled_reminders=fake_list_due_scheduled_reminders,
        mark_scheduled_reminder_completed=fake_mark_scheduled_reminder_completed,
        send_reminder=fake_send_reminder,
        mark_followthrough_delivered=fake_mark_followthrough_delivered,
        now=lambda: 3300.0,
    )

    # Reminder still fired and was marked completed.
    assert captured["sent_ids"] == [81]
    assert captured["completed_ids"] == [81]
    # Flip was attempted exactly once but the exception was swallowed.
    assert captured["flip_attempts"] == 1
    # Loop reaches idle state (the failure didn't propagate to the loop).
    assert target.loop_state == "idle"
    assert target.last_error == ""
    # Code-review #1: the failure is unrecoverable (the reminder is already
    # marked completed and won't re-fire), so it MUST surface as a runtime
    # event so dashboards can spot the leak. Silent WARNING isn't enough.
    flip_failed_events = [
        event for event in target.recent_events
        if event.get("kind") == "followthrough_flip_failed"
    ]
    assert len(flip_failed_events) == 1
    flip_failed = flip_failed_events[0]
    assert flip_failed["followthrough_id"] == "wff_db_hiccup"
    assert flip_failed["reminder_id"] == 81
    assert "simulated DB hiccup" in flip_failed["error"]


@pytest.mark.asyncio
async def test_check_due_reminders_without_followthrough_callback_is_backward_compatible():
    """The flip callback must be optional so existing callers keep working."""
    target = _FakeTarget()
    captured: dict[str, Any] = {"sent_ids": [], "completed_ids": []}

    async def fake_list_due_scheduled_reminders(*, as_of: float, limit=None):
        return [
            {
                "id": 91,
                "user_id": 8,
                "platform": "local",
                "metadata": {
                    "origin": "workflow_followthrough",
                    "followthrough_id": "wff_no_callback",
                },
            }
        ]

    async def fake_mark_scheduled_reminder_completed(*, reminder_id: int):
        captured["completed_ids"].append(reminder_id)

    async def fake_send_reminder(reminder_record):
        captured["sent_ids"].append(int(reminder_record["id"]))
        return True

    # Note: no mark_followthrough_delivered passed.
    await reminder_runtime.check_due_reminders_for_target(
        target=target,
        list_due_scheduled_reminders=fake_list_due_scheduled_reminders,
        mark_scheduled_reminder_completed=fake_mark_scheduled_reminder_completed,
        send_reminder=fake_send_reminder,
        now=lambda: 3400.0,
    )

    assert captured["sent_ids"] == [91]
    assert captured["completed_ids"] == [91]
    assert target.loop_state == "idle"


@pytest.mark.asyncio
async def test_start_and_stop_runtime_manage_scheduler_task():
    target = _FakeLoopTarget()
    wake_calls: list[str] = []
    loop_started = asyncio.Event()

    async def fake_loop() -> None:
        loop_started.set()
        await asyncio.Future()

    started = await reminder_runtime.start_runtime(
        target=target,
        create_check_task=lambda: asyncio.create_task(fake_loop()),
        wake_scheduler=lambda: wake_calls.append("wake"),
    )

    assert started is True
    assert target.is_running is True
    assert target.loop_state == "idle"
    assert wake_calls == ["wake"]
    assert target.check_task is not None
    assert any(event.get("kind") == "start" for event in target.recent_events)

    await loop_started.wait()

    stopped = await reminder_runtime.stop_runtime(
        target=target,
        wake_scheduler=lambda: wake_calls.append("wake"),
    )

    assert stopped is True
    assert target.is_running is False
    assert target.loop_state == "stopped"
    assert target.check_task is None
    assert wake_calls == ["wake", "wake"]
    assert any(event.get("kind") == "stop" for event in target.recent_events)


@pytest.mark.asyncio
async def test_run_check_loop_dispatches_due_checks_after_timeout():
    target = _FakeLoopTarget(is_running=True)
    wake_event = asyncio.Event()
    calls: list[str] = []

    async def fake_compute() -> float:
        return 0.01

    async def fake_check() -> None:
        calls.append("check")
        target.is_running = False

    await reminder_runtime.run_check_loop(
        target=target,
        wake_event=wake_event,
        compute_next_check_delay_seconds=fake_compute,
        check_due_reminders=fake_check,
    )

    assert calls == ["check"]


@pytest.mark.asyncio
async def test_compute_next_check_delay_seconds_uses_near_timer_window():
    conn = _FakeReminderConn(row=(1003.0, "timer"))

    async def fake_connection():
        return conn

    sleep_seconds = await reminder_runtime.compute_next_check_delay_seconds(
        is_running=True,
        connection_factory=fake_connection,
        now=lambda: 1000.0,
    )

    assert sleep_seconds == 3.0
    assert "FROM scheduled_reminders" in conn.query
    assert conn.closed is True


@pytest.mark.asyncio
async def test_compute_next_check_delay_seconds_falls_back_on_lookup_failure():
    async def fake_connection():
        raise RuntimeError("db unavailable")

    sleep_seconds = await reminder_runtime.compute_next_check_delay_seconds(
        is_running=True,
        connection_factory=fake_connection,
        now=lambda: 1000.0,
    )

    assert sleep_seconds == 60.0


@pytest.mark.asyncio
async def test_get_user_reminders_for_target_maps_records():
    records = [
        {
            "id": 91,
            "user_id": 7,
            "platform": "discord",
            "channel_id": 1234,
            "surface_id": "discord-dm-1234",
            "reminder_text": "Check the draft",
            "scheduled_time": 4567.0,
            "created_time": 4000.0,
            "is_completed": False,
            "kind": "reminder",
            "runtime_job_id": "job_91",
            "runtime_session_key": "owner:7:surface:discord:dm:1234",
            "metadata": {"delivery_preference": "dm"},
        }
    ]

    async def fake_list_user_scheduled_reminders(*, user_id: int, platform: str):
        assert user_id == 7
        assert platform == "discord"
        return records

    reminders = await reminder_runtime.get_user_reminders_for_target(
        user_id=7,
        platform="discord",
        list_user_scheduled_reminders=fake_list_user_scheduled_reminders,
        build_scheduled_reminder=lambda record: reminder_runtime.scheduled_reminder_from_record(
            record,
            scheduled_reminder_factory=ScheduledReminder,
        ),
    )

    assert reminders == [
        ScheduledReminder(
            id=91,
            user_id=7,
            platform="discord",
            channel_id=1234,
            surface_id="discord-dm-1234",
            reminder_text="Check the draft",
            scheduled_time=4567.0,
            created_time=4000.0,
            is_completed=False,
            kind="reminder",
            runtime_job_id="job_91",
            runtime_session_key="owner:7:surface:discord:dm:1234",
            metadata={"delivery_preference": "dm"},
        )
    ]


@pytest.mark.asyncio
async def test_snooze_reminder_for_target_updates_runtime_job_and_emits():
    target = _FakeTarget()
    captured: dict[str, Any] = {}
    reminders = [
        ScheduledReminder(
            id=44,
            user_id=8,
            platform="discord",
            channel_id=99,
            surface_id="discord:channel:99",
            reminder_text="reminder 44",
            scheduled_time=1800.0,
            created_time=1700.0,
            is_completed=False,
            kind="reminder",
            runtime_job_id="job-44",
            runtime_session_key="",
            metadata={"account_user_id": 41},
        ),
        ScheduledReminder(
            id=44,
            user_id=8,
            platform="discord",
            channel_id=99,
            surface_id="discord:channel:99",
            reminder_text="reminder 44",
            scheduled_time=2900.0,
            created_time=1700.0,
            is_completed=False,
            kind="reminder",
            runtime_job_id="job-44",
            runtime_session_key="",
            metadata={"account_user_id": 41},
        ),
    ]

    async def fake_get_reminder_by_id(reminder_id: int):
        assert reminder_id == 44
        return reminders.pop(0)

    async def fake_update_scheduled_reminder_schedule(*, reminder_id: int, scheduled_time: float):
        captured["schedule_kwargs"] = {
            "reminder_id": reminder_id,
            "scheduled_time": scheduled_time,
        }

    async def fake_update_runtime_job(**kwargs):
        captured["update_job_kwargs"] = kwargs

    wake_calls: list[str] = []

    updated = await reminder_runtime.snooze_reminder_for_target(
        target=target,
        reminder_id=44,
        minutes=15,
        get_reminder_by_id=fake_get_reminder_by_id,
        update_scheduled_reminder_schedule=fake_update_scheduled_reminder_schedule,
        update_runtime_job=fake_update_runtime_job,
        wake_scheduler=lambda: wake_calls.append("wake"),
        now=lambda: 2000.0,
    )

    assert updated is not None
    assert updated.scheduled_time == 2900.0
    assert captured["schedule_kwargs"] == {
        "reminder_id": 44,
        "scheduled_time": 2900.0,
    }
    assert captured["update_job_kwargs"] == {
        "job_id": "job-44",
        "status": "scheduled",
        "scheduled_for": 2900.0,
        "last_error": "",
        "metadata": {"snoozed_at": 2000.0, "snooze_minutes": 15},
    }
    assert wake_calls == ["wake"]
    assert target.emitted_updates == [
        {"user_id": 8, "platform": "discord", "account_user_id": 41}
    ]
    assert any(
        event.get("kind") == "snoozed" and event.get("scheduled_time") == 2900.0
        for event in target.recent_events
    )


@pytest.mark.asyncio
async def test_cancel_reminder_for_target_completes_runtime_job_and_emits():
    target = _FakeTarget()
    captured: dict[str, Any] = {}

    async def fake_get_reminder_by_id(reminder_id: int):
        assert reminder_id == 44
        return ScheduledReminder(
            id=44,
            user_id=8,
            platform="discord",
            channel_id=99,
            surface_id="discord:channel:99",
            reminder_text="reminder 44",
            scheduled_time=1800.0,
            created_time=1700.0,
            is_completed=False,
            kind="reminder",
            runtime_job_id="job-44",
            runtime_session_key="",
            metadata={"account_user_id": 41},
        )

    async def fake_mark_scheduled_reminder_completed(*, reminder_id: int):
        captured["complete_kwargs"] = {"reminder_id": reminder_id}

    async def fake_complete_runtime_job(**kwargs):
        captured["complete_job_kwargs"] = kwargs

    wake_calls: list[str] = []

    cancelled = await reminder_runtime.cancel_reminder_for_target(
        target=target,
        reminder_id=44,
        get_reminder_by_id=fake_get_reminder_by_id,
        mark_scheduled_reminder_completed=fake_mark_scheduled_reminder_completed,
        complete_runtime_job=fake_complete_runtime_job,
        wake_scheduler=lambda: wake_calls.append("wake"),
        now=lambda: 2100.0,
    )

    assert cancelled is True
    assert captured["complete_kwargs"] == {"reminder_id": 44}
    assert captured["complete_job_kwargs"] == {
        "job_id": "job-44",
        "status": "cancelled",
        "last_transition_reason": "user_cancelled",
        "metadata": {"cancelled_at": 2100.0},
    }
    assert wake_calls == ["wake"]
    assert target.emitted_updates == [
        {"user_id": 8, "platform": "discord", "account_user_id": 41}
    ]
    assert any(
        event.get("kind") == "cancelled" and event.get("reminder_id") == 44
        for event in target.recent_events
    )


@pytest.mark.asyncio
async def test_check_due_reminders_dead_letters_undeliverable_after_cap():
    """An undeliverable reminder (delivery always fails) must not loop forever.
    After REMINDER_MAX_DELIVERY_ATTEMPTS consecutive failures it is retired via
    mark_completed so the scheduler stops re-firing it. (2026-07-13: a stale
    channel_id=0 binding fired ~180x over 3 hours and bled into an unrelated
    reply.)"""
    target = _FakeTarget()
    completed: list[int] = []
    due = [{"id": 58, "user_id": 1, "platform": "cli"}]

    async def fake_list_due(*, as_of, limit=None):
        return list(due)

    async def fake_mark_completed(*, reminder_id):
        completed.append(reminder_id)

    async def always_fail_send(reminder_record):
        return False

    cap = reminder_runtime.REMINDER_MAX_DELIVERY_ATTEMPTS
    for _ in range(cap - 1):
        await reminder_runtime.check_due_reminders_for_target(
            target=target,
            list_due_scheduled_reminders=fake_list_due,
            mark_scheduled_reminder_completed=fake_mark_completed,
            send_reminder=always_fail_send,
            now=lambda: 1000.0,
        )
    assert completed == []  # below the cap: still retried, not retired

    await reminder_runtime.check_due_reminders_for_target(
        target=target,
        list_due_scheduled_reminders=fake_list_due,
        mark_scheduled_reminder_completed=fake_mark_completed,
        send_reminder=always_fail_send,
        now=lambda: 1000.0,
    )
    assert completed == [58]  # cap reached: retired exactly once
    assert any(
        e.get("kind") == "dead_lettered" and e.get("reminder_id") == 58
        for e in target.recent_events
    )
