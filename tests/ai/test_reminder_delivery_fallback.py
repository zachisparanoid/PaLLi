"""Discord reminder delivery without a bot handle (2026-07-10 live bug).

``deliver_reminder`` only attempted Discord delivery through a ``bot``
reference — but nothing has wired the live Discord client into the scheduler
since the Wave-5 registrar deletion (ce5c0e4 restored scheduler *start*, not
the bot handle). The channel_runtime fallback then explicitly excluded
discord, so every Discord reminder fired its spine turn, composed its text,
and died with "Unable to send reminder N (platform=discord)" — retrying
every ~60s forever. Live evidence: probe reminder #63, 2026-07-10 23:07.

Fix under test: when no bot handle is available, Discord reminders deliver
through ``channel_runtime.send_envelope`` — the same live path the proactive
rail uses (core/turn/proactive.py:730).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import core.reminders.binding as binding
from core.reminders.binding import ReminderDeliveryRequest, deliver_reminder


def _request(**overrides) -> ReminderDeliveryRequest:
    base = dict(
        reminder_id=63,
        user_id=1,
        platform="discord",
        channel_id=100000000000000001,
        delivery_surface_id="100000000000000001",
        reminder_text="test probe",
        reminder_message="⏱️ test probe message",
        scheduled_time=1783739146.0,
        kind="reminder",
        metadata={},
        delivery_preference="channel",
        runtime_job_id="",
        account_user_id=None,
    )
    base.update(overrides)
    return ReminderDeliveryRequest(**base)


@pytest.fixture
def no_spine(monkeypatch):
    """Keep the spine regeneration out of the way (lazy-imported in
    deliver_reminder from core.reminders.scheduler)."""
    import core.reminders.scheduler as scheduler
    monkeypatch.setattr(scheduler, "fire_reminder", AsyncMock(return_value=None))


@pytest.fixture
def envelope_spy(monkeypatch):
    spy = AsyncMock(return_value=None)
    monkeypatch.setattr(binding.channel_runtime, "send_envelope", spy)
    # The door is up (final review, Important 3: a chat door that is not is
    # asked before the fire turn and never reaches the send).
    monkeypatch.setattr(binding.channel_runtime, "door_ready", AsyncMock(return_value=True))
    return spy


def test_discord_without_bot_delivers_via_channel_runtime(no_spine, envelope_spy):
    """bot=None (the live reality) must fall back to channel_runtime, the
    same delivery path proactive outreach uses."""
    result = asyncio.run(deliver_reminder(_request(), bot=None))

    assert result.sent is True, "discord reminder must deliver without a bot handle"
    envelope_spy.assert_awaited_once()
    kwargs = envelope_spy.await_args.kwargs
    assert kwargs["platform"] == "discord"
    assert kwargs["surface_id"] == "100000000000000001"
    assert "test probe message" in kwargs["envelope"].text


def test_discord_with_bot_prefers_bot_path(no_spine, envelope_spy, monkeypatch):
    """When a bot handle exists and succeeds, the legacy path wins and the
    envelope fallback stays untouched."""
    monkeypatch.setattr(
        binding,
        "_deliver_discord_reminder",
        AsyncMock(return_value=(True, "channel", 42)),
    )
    result = asyncio.run(deliver_reminder(_request(), bot=object()))

    assert result.sent is True
    envelope_spy.assert_not_awaited()


def test_local_platform_never_uses_channel_runtime(no_spine, envelope_spy):
    result = asyncio.run(deliver_reminder(_request(platform="local"), bot=None))

    assert result.sent is False
    envelope_spy.assert_not_awaited()


# ── DEFECT_RECORD §16 (2026-09-01): the reminder from the windowless room ────

@pytest.mark.asyncio
async def test_delivered_text_is_her_composed_reply_and_the_record_uses_it(monkeypatch):
    from types import SimpleNamespace
    import core.reminders.scheduler as scheduler
    composed = "Heads up — the budget talk never came up tonight. Still want to knock it out?"
    monkeypatch.setattr(scheduler, "fire_reminder", AsyncMock(return_value=SimpleNamespace(response_text=composed)))
    spy = AsyncMock()
    monkeypatch.setattr(binding.channel_runtime, "send_envelope", spy)
    monkeypatch.setattr(binding.channel_runtime, "door_ready", AsyncMock(return_value=True))
    result = await deliver_reminder(_request(), bot=None)
    assert result.sent is True
    assert spy.call_args.kwargs["envelope"].text == composed
    assert result.delivered_text == composed
    assert binding._recorded_reminder_text(_request(), result) == composed          # not "⏱️ test probe message"


@pytest.mark.asyncio
async def test_record_falls_back_to_the_stored_message_when_nothing_was_delivered():
    from core.reminders.binding import ReminderDeliveryResult
    r = ReminderDeliveryResult(sent=False, delivered_in="", delivery_channel_id=0, delivery_surface_id="")
    assert binding._recorded_reminder_text(_request(), r) == "⏱️ test probe message"


@pytest.mark.asyncio
async def test_fire_turn_sees_the_recent_conversation(monkeypatch):
    import core.reminders.scheduler as scheduler
    from core.database import brain
    rows = [
        {"role": "assistant", "content": "Locked in — if the budget talk hasn't happened by 10, I'll come knocking.", "timestamp": 1.0},
        {"role": "user", "content": "aight, what else you need to know regarding budget stuff?", "timestamp": 2.0},
    ]
    monkeypatch.setattr(brain, "get_conversation_history", AsyncMock(return_value=rows))
    seen = {}

    async def fake_dispatch(turn):
        seen["content"] = turn.content
        return None
    import core.agent.dispatch as dispatch_mod
    monkeypatch.setattr(dispatch_mod, "dispatch_turn", fake_dispatch)
    await scheduler.fire_reminder(type("R", (), {"id": 143, "what": "Heads up — budgeting talk never came up tonight."})(), owner_id=1)
    assert seen["content"].startswith("[REMINDER FIRE] Heads up")
    assert "they: aight, what else you need to know regarding budget stuff?" in seen["content"]
    assert "beat you to it" in seen["content"]


@pytest.mark.asyncio
async def test_fire_turn_survives_a_dead_history_api(monkeypatch):
    import core.reminders.scheduler as scheduler
    from core.database import brain
    monkeypatch.setattr(brain, "get_conversation_history", AsyncMock(side_effect=RuntimeError("db locked")))
    seen = {}

    async def fake_dispatch(turn):
        seen["content"] = turn.content
        return None
    import core.agent.dispatch as dispatch_mod
    monkeypatch.setattr(dispatch_mod, "dispatch_turn", fake_dispatch)
    await scheduler.fire_reminder(type("R", (), {"id": 1, "what": "call the shop"})(), owner_id=1)
    assert seen["content"] == "[REMINDER FIRE] call the shop"


# ── a reminder that never landed is not marked sent (telegram door, final wave)
@pytest.mark.asyncio
async def test_a_telegram_reminder_that_never_landed_is_not_marked_sent(
        no_spine, monkeypatch):
    """The envelope branch marks the reminder delivered on a clean return
    from ``send_envelope`` — and then it is transcribed as something she
    said and never retried. The Telegram adapter used to return that clean
    result for a send that had raised inside bots/telegram/reply.py, so a
    reminder into a chat that had blocked her was filed as delivered. With
    the adapter raising, this branch does what it always did with a refusal:
    sent stays False and the reminder is still owed."""
    from bots.telegram.adapter import TelegramChannelAdapter
    from core.channel_runtime import ChannelRuntimeRegistry

    class _BlockedBot:
        async def send_message(self, *, chat_id, text, **kw):
            raise RuntimeError("Forbidden: bot was blocked by the user")

    registry = ChannelRuntimeRegistry()
    await registry.register_adapter(
        "telegram", TelegramChannelAdapter(_BlockedBot()))
    monkeypatch.setattr(binding, "channel_runtime", registry)

    result = await deliver_reminder(
        _request(platform="telegram", channel_id=424242,
                 delivery_surface_id="424242"),
        bot=None,
    )

    assert result.sent is False
    assert result.delivered_text == ""
