"""Custody, not arbitration (arbiter spec §5): a delivered Discord reminder
joins the discord:<channel> transcript her replies read. Failure-swallowed —
a custody hiccup can never block or fail an alarm. Reminders stay exempt
from the arbiter forever; this touches only the record her memory reads."""
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


@pytest.mark.asyncio
async def test_delivered_reminder_reaches_discord_transcript(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    async def _deliver(request, *, bot=None):
        return binding.ReminderDeliveryResult(
            sent=True, delivered_in="dm",
            delivery_channel_id=123, delivery_surface_id="123",
        )

    async def _log(request, delivery_result):
        pass

    monkeypatch.setattr(binding, "deliver_reminder", _deliver)
    monkeypatch.setattr(binding, "log_reminder_delivery", _log)
    result = await binding.execute_reminder_delivery(_request())
    assert result.sent is True
    lines = (tmp_path / "discord_123.jsonl").read_text().strip().splitlines()
    payload = [json.loads(l) for l in lines if l.strip()]
    assert payload[-1]["role"] == "assistant"
    assert "bread" in json.dumps(payload[-1])


@pytest.mark.asyncio
async def test_unsent_reminder_writes_no_transcript(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    async def _deliver(request, *, bot=None):
        return binding.ReminderDeliveryResult(
            sent=False, delivered_in="", delivery_channel_id=0,
            delivery_surface_id="",
        )

    monkeypatch.setattr(binding, "deliver_reminder", _deliver)
    result = await binding.execute_reminder_delivery(_request())
    assert result.sent is False
    assert not (tmp_path / "discord_123.jsonl").exists()


@pytest.mark.asyncio
async def test_transcript_failure_never_fails_the_reminder(monkeypatch):
    async def _deliver(request, *, bot=None):
        return binding.ReminderDeliveryResult(
            sent=True, delivered_in="dm",
            delivery_channel_id=123, delivery_surface_id="123",
        )

    async def _log(request, delivery_result):
        pass

    async def _boom(*args, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(binding, "deliver_reminder", _deliver)
    monkeypatch.setattr(binding, "log_reminder_delivery", _log)
    monkeypatch.setattr(binding, "_transcribe_reminder_to_discord", _boom)
    result = await binding.execute_reminder_delivery(_request())
    assert result.sent is True                      # never blocks the alarm


@pytest.mark.asyncio
async def test_non_discord_platform_writes_no_transcript(monkeypatch, tmp_path):
    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))

    async def _deliver(request, *, bot=None):
        return binding.ReminderDeliveryResult(
            sent=True, delivered_in="local",
            delivery_channel_id=0, delivery_surface_id="local",
        )

    async def _log(request, delivery_result):
        pass

    monkeypatch.setattr(binding, "deliver_reminder", _deliver)
    monkeypatch.setattr(binding, "log_reminder_delivery", _log)
    await binding.execute_reminder_delivery(_request(platform="local"))
    assert list(tmp_path.iterdir()) == []
