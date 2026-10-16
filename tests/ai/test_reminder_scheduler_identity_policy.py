from __future__ import annotations

from unittest.mock import create_autospec, patch

import pytest

import core.reminders.scheduler as reminder_scheduler_module
from core.reminders.scheduler import ReminderDeliveryRequest, ReminderDeliveryResult, ReminderScheduler
from core.database import PalliBrain

# Legacy aliases preserved so existing tests stay readable. The canonical
# home is core.reminders.scheduler after C1.3.
reminder_delivery = reminder_scheduler_module


@pytest.mark.asyncio
async def test_urgent_only_threshold_prefers_quiet_delivery():
    scheduler = ReminderScheduler()

    should_reduce = await scheduler._should_reduce_reminder_interruptions(
        user_id=1,
        platform="discord",
        action_preferences={"interruption_threshold": "urgent_only", "quiet_hours_mode": "off"},
    )

    assert should_reduce is True


@pytest.mark.asyncio
async def test_send_reminder_softens_delivery_to_dm_before_dispatch(monkeypatch):
    scheduler = ReminderScheduler()
    captured: dict[str, object] = {}

    async def fake_resolve_local_owner_user_id(*args, **kwargs):
        return 55

    async def fake_get_compiled_action_policy(*args, **kwargs):
        return {"interruption_threshold": "urgent_only", "quiet_hours_mode": "off"}

    async def fake_execute_reminder_delivery(request):
        captured["delivery_request"] = request
        return reminder_delivery.ReminderDeliveryResult(
            sent=True,
            delivered_in=request.delivery_preference,
            delivery_channel_id=request.channel_id,
            delivery_surface_id=request.delivery_surface_id,
        )

    async def fake_emit_runtime_update(**kwargs):
        captured["emit_kwargs"] = kwargs

    monkeypatch.setattr(
        reminder_scheduler_module.identity_service,
        "resolve_local_owner_user_id",
        fake_resolve_local_owner_user_id,
    )
    monkeypatch.setattr(
        reminder_scheduler_module.identity_service,
        "get_compiled_action_policy",
        fake_get_compiled_action_policy,
    )
    monkeypatch.setattr(scheduler, "_execute_reminder_delivery", fake_execute_reminder_delivery)
    monkeypatch.setattr(scheduler, "_emit_runtime_update_for_subject", fake_emit_runtime_update)

    reminder_row = (
        41,
        14,
        "discord",
        88,
        "discord:channel:88",
        "start the laundry",
        1234.0,
        1200.0,
        False,
        "reminder",
        "",
        "",
        {"delivery_preference": "channel", "source_is_dm": False, "account_user_id": 55},
    )

    sent = await scheduler._send_reminder(reminder_row)

    assert sent is True
    delivery_request = captured["delivery_request"]
    assert delivery_request.delivery_preference == "dm"
    assert scheduler.last_dispatch_attempt["delivery_preference"] == "dm"
    assert scheduler.last_dispatch_attempt["delivered_in"] == "dm"
    assert any(
        event.get("kind") == "delivery_softened" and event.get("reminder_id") == 41
        for event in scheduler.recent_events
    )
    assert captured["emit_kwargs"] == {"user_id": 14, "platform": "discord", "account_user_id": 55}


