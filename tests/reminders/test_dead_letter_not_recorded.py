"""An undeliverable reminder is never recorded as sent (fix plan 5.8).

Custody: her record and what reached him must agree. ``execute_reminder_delivery``
already writes the "she said it" conversation row and the chat transcript only
after ``sent=True`` — the plan's guard test below pins that. The real holes
were two:

(a) The Discord channel adapter answered ``{"sent": True}`` for any non-empty
    text, even when ``send_turn_reply`` had swallowed a failed send. Every
    Discord reminder takes that path in production (the scheduler has no bot
    handle), so a reminder that never reached him was logged as said,
    transcribed into his chat as said, and retired as delivered. It now
    raises when nothing landed, the way the Telegram adapter does.
(b) The dead-letter cap retired an undeliverable row with the same
    ``is_completed`` flag a delivered one gets, so the store could not tell
    them apart. The row is now stamped ``dead_lettered`` first, then
    completed — the order the late digest uses: a row whose stamp failed
    stays open and is tried again instead of looking delivered.

The fire turn's own ``reminder_fire`` transcript is her composing, not a claim
of delivery, and is left alone. Reminders never go through the arbiter.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.reminders.binding as binding
import core.reminders.scheduler as scheduler
from core.channel_runtime import ChannelDeliveryEnvelope, ChannelRuntimeRegistry
from core.reminders.binding import (
    REMINDER_MAX_DELIVERY_ATTEMPTS,
    ReminderDeliveryRequest,
    check_due_reminders_for_target,
    execute_reminder_delivery,
)

CHANNEL = 100000000000000001


def _request(**overrides) -> ReminderDeliveryRequest:
    base = dict(
        reminder_id=63,
        user_id=1,
        platform="discord",
        channel_id=CHANNEL,
        delivery_surface_id=str(CHANNEL),
        reminder_text="call the shop",
        reminder_message="Reminder: call the shop",
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
    """The fire turn is her composing; keep it out of the way (deliver_reminder
    imports it lazily from the scheduler)."""
    monkeypatch.setattr(scheduler, "fire_reminder", AsyncMock(return_value=None))


@pytest.fixture
def record(monkeypatch, tmp_path):
    """Spies on both custody legs: the conversation row (brain) and the chat
    transcript (the runner's session writer the transcript leg calls)."""
    import core.agent.runner as runner

    monkeypatch.setenv("PALLI_AGENT_SESSIONS_ROOT", str(tmp_path))
    spies = {
        "store_conversation": AsyncMock(return_value=None),
        "transcript": AsyncMock(return_value=None),
    }
    monkeypatch.setattr(binding.brain, "store_conversation", spies["store_conversation"])
    monkeypatch.setattr(runner, "_open_session_with_exchange", spies["transcript"])
    return spies


def _discord_channel(*, send):
    """A discord channel: ``async with .typing():`` + ``await .send()``.
    __aexit__ returns False so an exception inside the block propagates."""
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=send)
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    return channel


def _discord_adapter(monkeypatch, channel):
    from bots.discord import reply as discord_reply
    from bots.discord.client import DiscordChannelAdapter

    monkeypatch.setattr(discord_reply, "_calculate_typing_delay", lambda t: 0.0)
    monkeypatch.setattr(discord_reply, "_delivery_notice", lambda kind, **f: None)
    client = MagicMock()
    client.is_ready = MagicMock(return_value=True)
    client.get_channel = MagicMock(return_value=channel)
    return DiscordChannelAdapter(client)


async def _registry_with(monkeypatch, platform, adapter):
    registry = ChannelRuntimeRegistry()
    await registry.register_adapter(platform, adapter)
    monkeypatch.setattr(binding, "channel_runtime", registry)
    return registry


# --- the plan's guard: a send that raises is never recorded ----------------


@pytest.mark.asyncio
async def test_a_channel_runtime_whose_send_raises_records_nothing(no_spine, record, monkeypatch):
    class _Refusing:
        async def send_envelope(self, *, surface_id, envelope):
            raise RuntimeError("the door is shut")

    await _registry_with(monkeypatch, "discord", _Refusing())

    result = await execute_reminder_delivery(_request(), bot=None)

    assert result.sent is False
    assert result.delivered_text == ""
    record["store_conversation"].assert_not_called()
    record["transcript"].assert_not_called()


# --- (a) the Discord adapter -------------------------------------------------


@pytest.mark.asyncio
async def test_a_discord_reminder_whose_send_raises_is_not_recorded_as_sent(
        no_spine, record, monkeypatch):
    """The live path: bot=None, the Discord adapter, and a send that dies
    inside send_turn_reply (which swallows it and returns False)."""
    async def _send(*args, **kwargs):
        raise RuntimeError("403 Forbidden")

    channel = _discord_channel(send=_send)
    await _registry_with(monkeypatch, "discord", _discord_adapter(monkeypatch, channel))

    result = await execute_reminder_delivery(_request(), bot=None)

    channel.send.assert_awaited()  # the send was really tried, and died
    assert result.sent is False
    assert result.delivered_text == ""
    record["store_conversation"].assert_not_called()
    record["transcript"].assert_not_called()


@pytest.mark.asyncio
async def test_a_discord_reminder_that_landed_is_recorded(no_spine, record, monkeypatch):
    """The other half: a send that landed is logged and transcribed, once."""
    channel = _discord_channel(send=lambda *a, **k: None)
    await _registry_with(monkeypatch, "discord", _discord_adapter(monkeypatch, channel))

    result = await execute_reminder_delivery(_request(), bot=None)

    assert result.sent is True
    record["store_conversation"].assert_awaited_once()
    record["transcript"].assert_awaited_once()


@pytest.mark.asyncio
async def test_the_discord_adapter_raises_when_nothing_landed(monkeypatch):
    async def _send(*args, **kwargs):
        raise RuntimeError("503 Service Unavailable")

    adapter = _discord_adapter(monkeypatch, _discord_channel(send=_send))
    with pytest.raises(RuntimeError, match="nothing was delivered"):
        await adapter.send_envelope(
            surface_id=str(CHANNEL), envelope=ChannelDeliveryEnvelope(text="stretch"),
        )


@pytest.mark.asyncio
async def test_the_discord_adapter_says_sent_when_it_landed(monkeypatch):
    channel = _discord_channel(send=lambda *a, **k: None)
    adapter = _discord_adapter(monkeypatch, channel)
    result = await adapter.send_envelope(
        surface_id=str(CHANNEL), envelope=ChannelDeliveryEnvelope(text="stretch"),
    )
    assert result == {"sent": True, "channel_id": CHANNEL}


@pytest.mark.parametrize("text", ["", "   \n\t "])
@pytest.mark.asyncio
async def test_nothing_to_send_is_not_a_failed_send_and_not_a_sent_one(monkeypatch, text):
    """Whitespace is nothing to send (Telegram's adapter, the same rule): no
    raise, no send, and never ``sent: True``."""
    channel = _discord_channel(send=lambda *a, **k: None)
    adapter = _discord_adapter(monkeypatch, channel)
    result = await adapter.send_envelope(
        surface_id=str(CHANNEL), envelope=ChannelDeliveryEnvelope(text=text),
    )
    assert result == {"sent": False, "channel_id": CHANNEL}
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_an_adapter_that_says_not_sent_is_not_recorded_as_sent(no_spine, record, monkeypatch):
    """An adapter that returns ``sent: False`` without raising (nothing to
    send) must not be counted as a delivery either."""
    class _NothingSent:
        async def send_envelope(self, *, surface_id, envelope):
            return {"sent": False, "chat_id": 5222}

    await _registry_with(monkeypatch, "telegram", _NothingSent())

    result = await execute_reminder_delivery(
        _request(platform="telegram", channel_id=5222, delivery_surface_id="5222"), bot=None,
    )

    assert result.sent is False
    record["store_conversation"].assert_not_called()
    record["transcript"].assert_not_called()


@pytest.mark.asyncio
async def test_an_adapter_that_returns_no_verdict_still_counts_as_sent(no_spine, record, monkeypatch):
    """An adapter can answer without a ``sent`` key (the dashboard buffer did),
    and older fakes answer None: a clean return is still a delivery. Since
    2026-10-10 a web-page reminder never reaches an adapter (its delivery is
    its conversation row, tests/reminders/test_web_page_reminders.py), so a
    stand-in door carries this rule."""
    class _Buffer:
        async def send_envelope(self, *, surface_id, envelope):
            return {"delivered": True, "transport": "buffer"}

    await _registry_with(monkeypatch, "voice", _Buffer())

    result = await execute_reminder_delivery(
        _request(platform="voice", channel_id=0, delivery_surface_id="room"), bot=None,
    )

    assert result.sent is True
    record["store_conversation"].assert_awaited_once()


# --- (b) the dead-letter cap -------------------------------------------------


@dataclass
class _Target:
    loop_state: str = "idle"
    last_check_started_at: float | None = None
    last_check_completed_at: float | None = None
    last_error: str = ""
    recent_events: list[dict[str, Any]] = field(default_factory=list)

    def _record_runtime_event(self, kind: str, **payload: Any) -> None:
        self.recent_events.append({"kind": kind, **payload})

    async def _emit_runtime_update_for_subject(self, **kwargs: Any) -> None:
        return None


class _Book:
    """The store's two writers, in the order they were called."""

    def __init__(self, *, fail_merge: bool = False):
        self.calls: list[tuple] = []
        self.fail_merge = fail_merge

    async def mark_completed(self, *, reminder_id):
        self.calls.append(("completed", reminder_id))

    async def merge_metadata(self, *, reminder_id, patch):
        if self.fail_merge:
            raise RuntimeError("database is locked")
        self.calls.append(("merged", reminder_id, dict(patch)))


async def _run(target, book, *, send_ok: bool, rid: int = 58, with_writer: bool = True):
    async def _list_due(*, as_of, limit=None):
        return [{"id": rid, "user_id": 1, "platform": "discord"}]

    async def _send(record):
        return send_ok

    kwargs = {}
    if with_writer:
        kwargs["merge_scheduled_reminder_metadata"] = book.merge_metadata
    await check_due_reminders_for_target(
        target=target,
        list_due_scheduled_reminders=_list_due,
        mark_scheduled_reminder_completed=book.mark_completed,
        send_reminder=_send,
        now=lambda: 1000.0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_a_dead_lettered_row_is_stamped_before_it_is_completed():
    target, book = _Target(), _Book()
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS - 1):
        await _run(target, book, send_ok=False)
    assert book.calls == []  # below the cap: still owed, untouched

    await _run(target, book, send_ok=False)

    assert book.calls == [("merged", 58, {"dead_lettered": True}), ("completed", 58)]
    assert any(e.get("kind") == "dead_lettered" and e.get("reminder_id") == 58
               for e in target.recent_events)


@pytest.mark.asyncio
async def test_a_failed_stamp_leaves_the_row_open_and_it_is_tried_again(caplog):
    target, book = _Target(), _Book(fail_merge=True)
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await _run(target, book, send_ok=False)

    assert ("completed", 58) not in book.calls  # never looks delivered
    assert target.loop_state == "idle"           # one row's stamp does not stop the loop
    assert any("dead-lettered" in r.getMessage() for r in caplog.records)

    # The next cycle it is still due, fails again, and the retirement is retried.
    book.fail_merge = False
    await _run(target, book, send_ok=False)
    assert book.calls == [("merged", 58, {"dead_lettered": True}), ("completed", 58)]


@pytest.mark.asyncio
async def test_a_delivered_row_is_never_stamped_dead_lettered():
    target, book = _Target(), _Book()
    await _run(target, book, send_ok=False)
    await _run(target, book, send_ok=True)
    assert book.calls == [("completed", 58)]


@pytest.mark.asyncio
async def test_a_caller_without_the_writer_still_dead_letters():
    """Older callers (and tests/ai/test_reminder_runtime.py's fakes) pass no
    metadata writer; the cap still retires the row rather than loop forever."""
    target, book = _Target(), _Book()
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await _run(target, book, send_ok=False, with_writer=False)
    assert book.calls == [("completed", 58)]


@pytest.mark.asyncio
async def test_the_scheduler_hands_the_cap_its_metadata_writer(monkeypatch):
    """The live wiring: an on-time row that never lands is stamped through the
    brain's merge_scheduled_reminder_metadata."""
    book = _Book()
    row = {
        "id": 71, "user_id": 1, "platform": "discord", "channel_id": 111,
        "surface_id": "111", "reminder_text": "call the shop",
        "scheduled_time": time.time() - 60, "kind": "reminder",
        "runtime_job_id": "", "metadata": {},
    }

    async def _list_due(*, as_of=None, limit=None):
        return [] if ("completed", 71) in book.calls else [row]

    monkeypatch.setattr(scheduler.brain, "list_due_scheduled_reminders", _list_due)
    monkeypatch.setattr(scheduler.brain, "mark_scheduled_reminder_completed", book.mark_completed)
    monkeypatch.setattr(scheduler.brain, "merge_scheduled_reminder_metadata", book.merge_metadata)
    target = scheduler.ReminderScheduler()
    monkeypatch.setattr(target, "_send_reminder", AsyncMock(return_value=False))

    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await target._check_due_reminders()

    assert book.calls == [("merged", 71, {"dead_lettered": True}), ("completed", 71)]
