"""A door that is not up yet is not a failed delivery (final review, Important 3).

After a restart the Discord adapter is registered BEFORE ``client.start()`` and
answers "client not ready yet" until the gateway connects. An overdue timer
makes the reminder loop tick every second, so the reminders owed from the
downtime — one by one, or bundled in the late digest — could spend all three
of the dead-letter cap's attempts in a few seconds against a door that was
still opening, and be retired as undeliverable: the very reminders the digest
exists to deliver.

"Not ready" now has its own name — ``DoorNotReady`` at the raise sites, a
``door_not_ready`` field on the delivery result, ``DOOR_NOT_READY`` from the
send — and neither the per-row path nor the digest counts it. The row stays
open and is tried on the next check. The door is asked BEFORE the fire turn,
so a door that is down costs no LLM turn per tick. A send that really fails
still counts, exactly as before. Reminders never go through the arbiter.

R17a: the exemption is bounded. It holds only inside the boot grace window
(REMINDER_DOOR_BOOT_GRACE_SECONDS, 300 s after the reminder runtime starts).
After it, a closed door counts like any failed send, so a door that never
comes up (a revoked Telegram token) dead-letters as before.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.reminders.binding as binding
import core.reminders.scheduler as scheduler
from core.channel_runtime import ChannelDeliveryEnvelope, ChannelRuntimeRegistry, DoorNotReady
from core.reminders.binding import (
    DOOR_NOT_READY,
    REMINDER_DOOR_BOOT_GRACE_SECONDS,
    REMINDER_MAX_DELIVERY_ATTEMPTS,
    ReminderDeliveryRequest,
    ReminderDeliveryResult,
    check_due_reminders_for_target,
    deliver_reminder,
    send_reminder_for_target,
)

CHANNEL = 100000000000000001


def _request(**overrides) -> ReminderDeliveryRequest:
    base = dict(
        reminder_id=63, user_id=1, platform="discord", channel_id=CHANNEL,
        delivery_surface_id=str(CHANNEL), reminder_text="call the shop",
        reminder_message="Reminder: call the shop", scheduled_time=1783739146.0,
        kind="reminder", metadata={}, delivery_preference="channel",
        runtime_job_id="", account_user_id=None,
    )
    base.update(overrides)
    return ReminderDeliveryRequest(**base)


@pytest.fixture
def spine(monkeypatch):
    """The fire turn, as a spy: a door that is down must not cost one."""
    fire = AsyncMock(return_value=None)
    monkeypatch.setattr(scheduler, "fire_reminder", fire)
    return fire


def _discord_client(*, ready: bool, send=None):
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=send or (lambda *a, **k: None))
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    client = MagicMock()
    client.is_ready = MagicMock(return_value=ready)
    client.get_channel = MagicMock(return_value=channel)
    return client, channel


async def _registry(monkeypatch, *adapters):
    registry = ChannelRuntimeRegistry()
    for platform, adapter in adapters:
        await registry.register_adapter(platform, adapter)
    monkeypatch.setattr(binding, "channel_runtime", registry)
    return registry


def _discord_adapter(monkeypatch, client):
    from bots.discord import reply as discord_reply
    from bots.discord.client import DiscordChannelAdapter

    monkeypatch.setattr(discord_reply, "_calculate_typing_delay", lambda t: 0.0)
    monkeypatch.setattr(discord_reply, "_delivery_notice", lambda kind, **f: None)
    return DiscordChannelAdapter(client)


# ── the raise sites say "not ready" in a type of their own ──────────────────


@pytest.mark.asyncio
async def test_the_registry_names_a_door_with_no_adapter_not_ready():
    registry = ChannelRuntimeRegistry()
    assert issubclass(DoorNotReady, RuntimeError)          # every old catch still holds
    with pytest.raises(DoorNotReady, match="No channel adapter registered for telegram"):
        await registry.send_envelope(platform="telegram", surface_id="5",
                                     envelope=ChannelDeliveryEnvelope(text="hi"))
    assert await registry.door_ready("telegram") is False


@pytest.mark.asyncio
async def test_the_registry_asks_an_adapter_that_can_say_whether_it_is_ready():
    class _Plain:                                          # says nothing: ready once registered
        async def send_envelope(self, *, surface_id, envelope):
            return {"sent": True}

    class _Opening(_Plain):
        def __init__(self):
            self.ready = False

        def is_ready(self):
            return self.ready

    opening = _Opening()
    registry = ChannelRuntimeRegistry()
    await registry.register_adapter("telegram", _Plain())
    await registry.register_adapter("discord", opening)
    assert await registry.door_ready("telegram") is True
    assert await registry.door_ready("discord") is False
    opening.ready = True
    assert await registry.door_ready("DISCORD ") is True


@pytest.mark.asyncio
async def test_the_discord_adapter_names_a_gateway_still_connecting_not_ready(monkeypatch):
    client, channel = _discord_client(ready=False)
    adapter = _discord_adapter(monkeypatch, client)
    assert adapter.is_ready() is False
    with pytest.raises(DoorNotReady, match="client not ready yet"):
        await adapter.send_envelope(surface_id=str(CHANNEL),
                                    envelope=ChannelDeliveryEnvelope(text="stretch"))
    channel.send.assert_not_called()
    client.is_ready.return_value = True
    assert adapter.is_ready() is True


# ── deliver_reminder: asked before the fire turn, answered on the result ────


@pytest.mark.asyncio
async def test_a_discord_door_still_connecting_costs_no_fire_turn_and_says_not_ready(
        spine, monkeypatch):
    client, channel = _discord_client(ready=False)
    await _registry(monkeypatch, ("discord", _discord_adapter(monkeypatch, client)))

    result = await deliver_reminder(_request(), bot=None)

    assert result.sent is False and result.door_not_ready is True
    assert result.delivered_text == ""
    spine.assert_not_awaited()
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_a_chat_door_not_registered_yet_is_not_ready(spine, monkeypatch):
    await _registry(monkeypatch)                            # nothing registered yet

    result = await deliver_reminder(
        _request(platform="telegram", channel_id=5222, delivery_surface_id="5222"), bot=None)

    assert result.sent is False and result.door_not_ready is True
    spine.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_door_that_closes_between_the_check_and_the_send_is_not_ready(
        spine, monkeypatch):
    class _Closing:
        async def send_envelope(self, *, surface_id, envelope):
            raise DoorNotReady("DiscordChannelAdapter: client not ready yet")

    await _registry(monkeypatch, ("discord", _Closing()))

    result = await deliver_reminder(_request(), bot=None)

    assert result.sent is False and result.door_not_ready is True


@pytest.mark.asyncio
async def test_a_send_that_really_fails_is_still_a_failure(spine, monkeypatch):
    async def _refused(*args, **kwargs):
        raise RuntimeError("403 Forbidden")

    client, channel = _discord_client(ready=True, send=_refused)
    await _registry(monkeypatch, ("discord", _discord_adapter(monkeypatch, client)))

    result = await deliver_reminder(_request(), bot=None)

    channel.send.assert_awaited()
    spine.assert_awaited_once()
    assert result.sent is False and result.door_not_ready is False


@pytest.mark.asyncio
async def test_a_door_that_is_not_a_chat_keeps_the_old_failure(spine, monkeypatch):
    """Only the two chat doors come up after boot. An old voice row with a
    channel still meets no adapter, and still dead-letters as before instead
    of waiting forever."""
    await _registry(monkeypatch)

    result = await deliver_reminder(
        _request(platform="voice", channel_id=77, delivery_surface_id="77"), bot=None)

    assert result.sent is False and result.door_not_ready is False


# ── send_reminder_for_target: a third answer, falsy, never "delivered" ──────


@dataclass
class _Target:
    loop_state: str = "idle"
    last_check_started_at: float | None = None
    last_check_completed_at: float | None = None
    last_error: str = ""
    last_dispatch_attempt: dict | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    # Inside the boot grace window unless a test says otherwise (every send
    # and tick below runs at now=1000.0).
    runtime_started_at: float | None = 990.0

    def _record_runtime_event(self, kind: str, **payload: Any) -> None:
        self.recent_events.append({"kind": kind, **payload})

    async def _emit_runtime_update_for_subject(self, **kwargs: Any) -> None:
        return None


async def _send_for(target, result: ReminderDeliveryResult, logger=None):
    async def _execute(request):
        return result

    async def _policy(**kw):
        return {}

    async def _no_softening(**kw):
        return False

    return await send_reminder_for_target(
        target=target, reminder_record={"id": 63},
        prepare_reminder_delivery_request=lambda record: _request(),
        override_delivery_preference=binding.override_delivery_preference,
        execute_reminder_delivery=_execute, get_action_policy=_policy,
        should_reduce_interruptions=_no_softening, now=lambda: 1000.0,
        logger_override=logger,
    )


def _not_ready_result():
    return ReminderDeliveryResult(sent=False, delivered_in="", delivery_channel_id=CHANNEL,
                                  delivery_surface_id=str(CHANNEL), door_not_ready=True)


@pytest.mark.asyncio
async def test_the_send_answers_door_not_ready_and_says_so_once_at_info(caplog):
    target = _Target()
    log = logging.getLogger("tests.door_not_ready")
    with caplog.at_level(logging.DEBUG, logger=log.name):
        first = await _send_for(target, _not_ready_result(), log)
        second = await _send_for(target, _not_ready_result(), log)

    assert first is DOOR_NOT_READY and second is DOOR_NOT_READY
    assert not first                                        # falsy: never read as delivered
    infos = [r for r in caplog.records if r.levelno == logging.INFO and "not up yet" in r.getMessage()]
    assert len(infos) == 1                                  # a 1 s tick does not flood the log
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert target.last_dispatch_attempt["door_not_ready"] is True
    assert [e["kind"] for e in target.recent_events].count("door_not_ready") == 1


@pytest.mark.asyncio
async def test_a_real_failure_still_answers_false():
    failed = ReminderDeliveryResult(sent=False, delivered_in="", delivery_channel_id=CHANNEL,
                                    delivery_surface_id=str(CHANNEL))
    assert await _send_for(_Target(), failed) is False


# ── the per-row path: not counted, the row stays open ───────────────────────


class _Book:
    def __init__(self):
        self.calls: list[tuple] = []

    async def mark_completed(self, *, reminder_id):
        self.calls.append(("completed", reminder_id))

    async def merge_metadata(self, *, reminder_id, patch):
        self.calls.append(("merged", reminder_id, dict(patch)))


async def _tick(target, book, answer):
    async def _list_due(*, as_of, limit=None):
        return [] if ("completed", 58) in book.calls else [{"id": 58, "user_id": 1, "platform": "discord"}]

    async def _send(record):
        return answer

    await check_due_reminders_for_target(
        target=target, list_due_scheduled_reminders=_list_due,
        mark_scheduled_reminder_completed=book.mark_completed, send_reminder=_send,
        now=lambda: 1000.0, merge_scheduled_reminder_metadata=book.merge_metadata,
    )


@pytest.mark.asyncio
async def test_the_per_row_path_never_counts_a_door_that_is_not_ready():
    target, book = _Target(), _Book()
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS * 3):
        await _tick(target, book, DOOR_NOT_READY)

    assert book.calls == []                                 # still owed, never dead-lettered
    assert getattr(target, "_delivery_failures", {}) == {}

    # A send that really fails counts exactly as before.
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await _tick(target, book, False)
    assert book.calls == [("merged", 58, {"dead_lettered": True}), ("completed", 58)]


# ── the late digest: not counted, every row stays open ──────────────────────


def _late_row(rid, *, late_by):
    return {
        "id": rid, "user_id": 1, "platform": "discord", "channel_id": 111,
        "surface_id": "111", "reminder_text": f"reminder {rid}",
        "scheduled_time": time.time() - late_by, "kind": "reminder",
        "runtime_job_id": "", "metadata": {"delivery_preference": "channel"},
    }


def _store(monkeypatch, rows):
    book = _Book()

    async def _list_due(*, as_of=None, limit=None):
        done = {c[1] for c in book.calls if c[0] == "completed"}
        return [r for r in rows if r["id"] not in done]

    monkeypatch.setattr(scheduler.brain, "list_due_scheduled_reminders", _list_due)
    monkeypatch.setattr(scheduler.brain, "mark_scheduled_reminder_completed", book.mark_completed)
    monkeypatch.setattr(scheduler.brain, "merge_scheduled_reminder_metadata", book.merge_metadata)
    return book


@pytest.mark.asyncio
async def test_the_digest_never_counts_a_door_that_is_not_ready(monkeypatch):
    book = _store(monkeypatch, [_late_row(1, late_by=3 * 3600), _late_row(2, late_by=2 * 3600)])
    target = scheduler.ReminderScheduler()
    target.runtime_started_at = time.time()                 # inside the boot grace window
    answers: list[Any] = [DOOR_NOT_READY] * (REMINDER_MAX_DELIVERY_ATTEMPTS * 3)
    monkeypatch.setattr(target, "_send_reminder", AsyncMock(side_effect=lambda row: answers.pop(0)))

    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS * 3):
        await target._check_due_reminders()

    assert book.calls == []                                 # nothing retired, nothing stamped
    assert getattr(target, "_delivery_failures", {}) == {}

    answers.extend([False] * REMINDER_MAX_DELIVERY_ATTEMPTS)
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await target._check_due_reminders()
    assert sorted(c for c in book.calls if c[0] == "completed") == [("completed", 1), ("completed", 2)]


# ── the boot window, end to end through the real send path ──────────────────


@pytest.mark.asyncio
async def test_after_a_restart_the_owed_reminders_wait_for_discord_and_then_go(
        spine, monkeypatch):
    """The review's scenario: an overdue on-time row and two late rows, a
    Discord adapter registered but not ready. Many ticks later nothing is
    retired and no fire turn ran; once the gateway is up, all of it goes."""
    rows = [
        {**_late_row(1, late_by=3 * 3600)},
        {**_late_row(2, late_by=2 * 3600)},
        {**_late_row(3, late_by=60), "kind": "timer"},
    ]
    book = _store(monkeypatch, rows)
    client, channel = _discord_client(ready=False)
    await _registry(monkeypatch, ("discord", _discord_adapter(monkeypatch, client)))
    monkeypatch.setattr(binding.brain, "store_conversation", AsyncMock(return_value=None))
    import core.agent.runner as runner
    monkeypatch.setattr(runner, "_open_session_with_exchange", AsyncMock(return_value=None))

    target = scheduler.ReminderScheduler()
    target.runtime_started_at = time.time()                 # the restart, a moment ago
    monkeypatch.setattr(target, "_get_reminder_action_policy", AsyncMock(return_value={}))
    monkeypatch.setattr(target, "_should_reduce_reminder_interruptions", AsyncMock(return_value=False))

    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS * 2):
        await target._check_due_reminders()

    assert book.calls == []
    spine.assert_not_awaited()
    channel.send.assert_not_called()

    client.is_ready.return_value = True                     # the gateway is up
    await target._check_due_reminders()

    completed = sorted(c[1] for c in book.calls if c[0] == "completed")
    assert completed == [1, 2, 3]
    assert not [c for c in book.calls if c[0] == "merged" and c[2].get("dead_lettered")]
    assert ("merged", 1, {"late_fired": True}) in book.calls
    assert channel.send.await_count == 2                    # the on-time timer, then one digest



# ── R17a: the exemption is bounded by the boot grace window ─────────────────


def test_the_grace_window_is_five_minutes_from_the_runtime_start():
    assert REMINDER_DOOR_BOOT_GRACE_SECONDS == 300.0


@pytest.mark.asyncio
async def test_start_runtime_records_when_the_runtime_began():
    target = _Target()
    target.runtime_started_at = None
    target.is_running = False
    target.check_task = None
    started = await binding.start_runtime(
        target=target, create_check_task=lambda: None,
        wake_scheduler=lambda: None, now=lambda: 1234.0,
    )
    assert started is True
    assert target.runtime_started_at == 1234.0


@pytest.mark.asyncio
async def test_the_scheduler_records_its_start(monkeypatch):
    target = scheduler.ReminderScheduler()
    assert target.runtime_started_at is None                # never started: no grace at all

    async def _no_loop():
        return None

    monkeypatch.setattr(target, "_reminder_check_loop", _no_loop)
    before = time.time()
    await target.start()
    try:
        assert before <= target.runtime_started_at <= time.time()
    finally:
        await target.stop()


@pytest.mark.parametrize("started_at, answer", [
    (1000.0 - REMINDER_DOOR_BOOT_GRACE_SECONDS, "inside"),        # the window's last second
    (1000.0 - REMINDER_DOOR_BOOT_GRACE_SECONDS - 1.0, "after"),
    (None, "after"),                                              # a runtime that never started
])
@pytest.mark.asyncio
async def test_the_send_forgives_a_closed_door_only_inside_the_window(caplog, started_at, answer):
    target = _Target(runtime_started_at=started_at)
    log = logging.getLogger("tests.door_not_ready.window")
    with caplog.at_level(logging.DEBUG, logger=log.name):
        result = await _send_for(target, _not_ready_result(), log)

    if answer == "inside":
        assert result is DOOR_NOT_READY
    else:
        # After the window a closed door is a failed send: False, the
        # ordinary warning, and no "waits … not counted" line.
        assert result is False
        assert not [r for r in caplog.records if "not up yet" in r.getMessage()]
        assert any("Unable to send reminder" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_after_the_window_the_per_row_path_counts_a_closed_door():
    target, book = _Target(runtime_started_at=1000.0 - REMINDER_DOOR_BOOT_GRACE_SECONDS - 1.0), _Book()
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await _tick(target, book, DOOR_NOT_READY)
    assert book.calls == [("merged", 58, {"dead_lettered": True}), ("completed", 58)]


@pytest.mark.asyncio
async def test_inside_the_window_a_real_failure_still_counts_on_the_per_row_path():
    target, book = _Target(), _Book()                       # started 10 s ago
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await _tick(target, book, False)
    assert book.calls == [("merged", 58, {"dead_lettered": True}), ("completed", 58)]


def _digest_target(monkeypatch, answer, *, started_ago):
    target = scheduler.ReminderScheduler()
    target.runtime_started_at = time.time() - started_ago
    monkeypatch.setattr(target, "_send_reminder", AsyncMock(return_value=answer))
    return target


@pytest.mark.asyncio
async def test_after_the_window_the_digest_counts_a_closed_door(monkeypatch):
    book = _store(monkeypatch, [_late_row(1, late_by=3 * 3600), _late_row(2, late_by=2 * 3600)])
    target = _digest_target(monkeypatch, DOOR_NOT_READY,
                            started_ago=REMINDER_DOOR_BOOT_GRACE_SECONDS + 60.0)
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await target._check_due_reminders()
    assert sorted(c for c in book.calls if c[0] == "completed") == [("completed", 1), ("completed", 2)]
    assert ("merged", 1, {"dead_lettered": True}) in book.calls


@pytest.mark.asyncio
async def test_inside_the_window_a_real_failure_still_counts_on_the_digest(monkeypatch):
    book = _store(monkeypatch, [_late_row(1, late_by=3 * 3600), _late_row(2, late_by=2 * 3600)])
    target = _digest_target(monkeypatch, False, started_ago=5.0)
    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await target._check_due_reminders()
    assert sorted(c for c in book.calls if c[0] == "completed") == [("completed", 1), ("completed", 2)]
    assert ("merged", 2, {"dead_lettered": True}) in book.calls


@pytest.mark.asyncio
async def test_a_revoked_door_dead_letters_end_to_end_once_the_window_is_over(spine, monkeypatch):
    """The concern R17a closes: a Telegram door that never registers. After
    the window its reminder is tried three times (no fire turn, the door is
    asked first) and dead-lettered, as before — no forever-wait."""
    row = {**_late_row(9, late_by=60), "platform": "telegram", "channel_id": 5222,
           "surface_id": "5222"}
    book = _store(monkeypatch, [row])
    await _registry(monkeypatch)                             # Telegram never came up
    target = scheduler.ReminderScheduler()
    target.runtime_started_at = time.time() - REMINDER_DOOR_BOOT_GRACE_SECONDS - 60.0
    monkeypatch.setattr(target, "_get_reminder_action_policy", AsyncMock(return_value={}))
    monkeypatch.setattr(target, "_should_reduce_reminder_interruptions", AsyncMock(return_value=False))

    for _ in range(REMINDER_MAX_DELIVERY_ATTEMPTS):
        await target._check_due_reminders()

    assert book.calls == [("merged", 9, {"dead_lettered": True}), ("completed", 9)]
    spine.assert_not_awaited()
