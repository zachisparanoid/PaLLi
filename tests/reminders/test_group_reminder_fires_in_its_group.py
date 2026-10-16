"""A reminder made in a group fires in that group (the owner, 2026-10-13).

Her own messages to the owner go to a private chat with him, never a group.
Work started in a group is the other kind: it goes back to that group. A
reminder made in a Discord server channel or a Telegram group is filed to
that chat (tests/tools/test_create_reminder_door.py), and its fire is sent to
the chat it was filed to; "the private chat he used last" is never asked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.reminders.binding as binding
import core.reminders.scheduler as scheduler
from core.channel_runtime import ChannelRuntimeRegistry
from core.reminders.binding import ReminderDeliveryRequest, deliver_reminder

SERVER_CHANNEL = 9004
TELEGRAM_GROUP = -100555


def _request(platform: str, channel_id: int) -> ReminderDeliveryRequest:
    return ReminderDeliveryRequest(
        reminder_id=63, user_id=1, platform=platform, channel_id=channel_id,
        delivery_surface_id=str(channel_id), reminder_text="order the pizza",
        reminder_message="Reminder: order the pizza", scheduled_time=1783739146.0,
        kind="reminder", metadata={}, delivery_preference="channel",
        runtime_job_id="", account_user_id=None,
    )


class _Door:
    def __init__(self):
        self.sent: list[str] = []

    async def send_envelope(self, *, surface_id, envelope):
        self.sent.append(surface_id)
        return {"sent": True}


@pytest.mark.parametrize("platform,group", [
    ("discord", SERVER_CHANNEL), ("telegram", TELEGRAM_GROUP),
])
@pytest.mark.asyncio
async def test_a_group_reminder_fires_in_its_group(monkeypatch, platform, group):
    import core.turn.proactive as proactive

    async def _never(**kw):
        raise AssertionError("a reminder's fire never looks for his private chat")

    monkeypatch.setattr(proactive, "_pick_proactive_surface", _never)
    monkeypatch.setattr(scheduler, "fire_reminder", AsyncMock(return_value=None))
    door = _Door()
    registry = ChannelRuntimeRegistry()
    await registry.register_adapter(platform, door)
    monkeypatch.setattr(binding, "channel_runtime", registry)

    result = await deliver_reminder(_request(platform, group), bot=None)

    assert result.sent is True
    assert door.sent == [str(group)]
