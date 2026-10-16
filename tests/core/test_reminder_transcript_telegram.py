"""A delivered reminder joins the transcript of the door it was delivered on
(telegram door, task 1).

Custody, not arbitration: reminders stay exempt from the arbiter forever.
The transcript leg exists so she REMEMBERS delivering one — before it, a
delivery lived only in reminder_fire_1.jsonl, a file her replies never read,
and she reconstructed her own deliveries from DB memory.

The leg returned early unless the platform was "discord", so a reminder
delivered on any other door left her with the same hole. Discord is the pin
here; every other platform except the doors she actually talks through is
still skipped.
"""
from __future__ import annotations

import json

import pytest

import core.reminders.binding as binding


def _request(**kw):
    d = dict(
        reminder_id=7, user_id=1, platform="discord", channel_id=123,
        delivery_surface_id="123", reminder_text="bread",
        reminder_message="take the bread out", scheduled_time=0.0,
        kind="reminder", metadata={}, delivery_preference="dm",
        runtime_job_id="", account_user_id=1,
    )
    d.update(kw)
    return binding.ReminderDeliveryRequest(**d)


@pytest.fixture
def delivered(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    async def _log(request, delivery_result):
        pass

    monkeypatch.setattr(binding, "log_reminder_delivery", _log)

    def _wire(*, channel_id: int, surface_id: str):
        async def _deliver(request, *, bot=None):
            return binding.ReminderDeliveryResult(
                sent=True, delivered_in="dm",
                delivery_channel_id=channel_id, delivery_surface_id=surface_id,
            )

        monkeypatch.setattr(binding, "deliver_reminder", _deliver)

    return _wire, tmp_path


def _last_role(path):
    lines = path.read_text().strip().splitlines()
    return json.loads([line for line in lines if line.strip()][-1])


@pytest.mark.asyncio
async def test_a_discord_reminder_still_lands_in_the_discord_transcript(delivered):
    wire, tmp_path = delivered
    wire(channel_id=123, surface_id="123")

    result = await binding.execute_reminder_delivery(_request())

    assert result.sent is True
    payload = _last_role(tmp_path / "discord_123.jsonl")
    assert payload["role"] == "assistant"
    assert "bread" in json.dumps(payload)


@pytest.mark.asyncio
async def test_a_telegram_reminder_lands_in_the_telegram_transcript(delivered):
    wire, tmp_path = delivered
    wire(channel_id=424242, surface_id="424242")

    result = await binding.execute_reminder_delivery(
        _request(platform="telegram", channel_id=424242, delivery_surface_id="424242")
    )

    assert result.sent is True
    assert (tmp_path / "telegram_424242.jsonl").exists()
    assert not (tmp_path / "discord_424242.jsonl").exists()
    payload = _last_role(tmp_path / "telegram_424242.jsonl")
    assert payload["role"] == "assistant"
    assert "bread" in json.dumps(payload)


@pytest.mark.asyncio
async def test_a_door_she_does_not_talk_through_still_writes_nothing(delivered):
    wire, tmp_path = delivered
    wire(channel_id=0, surface_id="local")

    await binding.execute_reminder_delivery(_request(platform="local"))

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_an_unsent_telegram_reminder_writes_nothing(delivered, monkeypatch):
    """Record on SEND, never on intent — on every door."""
    wire, tmp_path = delivered

    async def _unsent(request, *, bot=None):
        return binding.ReminderDeliveryResult(
            sent=False, delivered_in="", delivery_channel_id=0,
            delivery_surface_id="",
        )

    monkeypatch.setattr(binding, "deliver_reminder", _unsent)

    result = await binding.execute_reminder_delivery(
        _request(platform="telegram", channel_id=424242, delivery_surface_id="424242")
    )

    assert result.sent is False
    assert list(tmp_path.iterdir()) == []
