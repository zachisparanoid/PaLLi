"""Guests wait their turn (Batch 8, task 2b — B8-2, ruling T2b-6).

The owner gate (2026-08-19) decided WHAT a guest gets: a plain reply, no
tools, none of the owner's memory. Stranger-proofing week 1 added HOW MANY: a
per-guest rate limit and a global in-flight cap, with one canned "at
capacity" line over either. On 09-30 the owner dropped the rate limit
("Drop the limit.") and kept the flood protection without the canned line:
``GUEST_MAX_IN_FLIGHT`` is now a queue. A guest turn over the cap waits for a
free slot — inside its own task, never holding the door — and then she reads
it like any other. Nothing is said for her; no message is dropped.

Rewritten under the kickoff's exception (a) for the queue: the two window
tests went with the limit (``test_a_guest_gets_back_in_once_the_window_has_passed``,
``test_a_guest_who_never_comes_back_leaves_no_entry_behind``); the others
keep their premise with the refusal turned into a wait.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _channel():
    channel = MagicMock()
    channel.id = 67890
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock()
    return channel


def _msg(channel, *, author_id: int = 12345, name: str = "Dana", content: str = "hey"):
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.bot = False
    msg.author.id = author_id
    msg.author.name = name
    msg.author.display_name = name
    msg.content = content
    msg.attachments = []
    msg.mentions = []
    msg.channel = channel
    return msg


def _client(channel):
    import discord
    from bots.discord.client import PalliDiscordClient

    client = PalliDiscordClient(
        bot_user_id=99999, owner_id=1, intents=discord.Intents.default(),
    )

    async def owner_did():
        return 777                       # HIS id; every author below is a guest

    client._local_owner_discord_id = owner_did
    client.get_channel = lambda cid: channel
    return client


def _result(text: str = "hi there"):
    from core.turn.types import TurnResult
    return TurnResult(responded=True, response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0)


@pytest.mark.asyncio
async def test_a_busy_guest_is_never_rate_limited_and_never_answered_with_a_line(monkeypatch):
    from bots.common.guest_gate import GuestGate
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    assert not hasattr(config, "GUEST_RATE_LIMIT_PER_10MIN")
    channel = _channel()
    client = _client(channel)

    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=_result())) as disp, \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()) as send, \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()) as rec:
        for _ in range(6):
            await client._handle_guest_message(_msg(channel), channel.id)

    assert disp.await_count == 6                 # the sixth reached her too
    assert send.await_count == 6
    rec.assert_not_awaited()                     # never his history
    assert all(c.args[1] != GuestGate.AT_CAPACITY_LINE for c in send.await_args_list)


@pytest.mark.asyncio
async def test_every_guest_still_gets_through(monkeypatch):
    """No per-guest budget any more: a loud stranger and a quiet one are both read."""
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 5, raising=False)
    channel = _channel()
    client = _client(channel)

    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=_result())) as disp, \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()):
        for _ in range(3):
            await client._handle_guest_message(_msg(channel, author_id=111), channel.id)
        await client._handle_guest_message(
            _msg(channel, author_id=222, name="Rae"), channel.id,
        )

    assert disp.await_count == 4
    assert disp.await_args_list[-1].args[0].owner_id == 222


@pytest.mark.asyncio
async def test_a_third_concurrent_guest_turn_waits_its_turn_while_two_are_in_flight(monkeypatch):
    from bots.common.guest_gate import GuestGate
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    channel = _channel()
    client = _client(channel)

    release = asyncio.Event()
    started: list = []

    async def slow_dispatch(turn):
        started.append(turn)
        await release.wait()
        return _result()

    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(side_effect=slow_dispatch)), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()) as send:
        tasks = [
            asyncio.create_task(
                client._handle_guest_message(_msg(channel, author_id=aid), channel.id)
            )
            for aid in (301, 302, 303)
        ]
        for _ in range(50):                      # let two reach the (slow) model
            if len(started) == 2 and client._guest_gate.waiting == 1:
                break
            await asyncio.sleep(0)
        assert len(started) == 2                 # the third is queued, not refused
        assert client._guest_gate.waiting == 1
        assert client._guests_in_flight == 2
        send.assert_not_awaited()                # and nothing is said for her

        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)

    assert len(started) == 3                     # the third had its turn
    assert [t.owner_id for t in started] == [301, 302, 303]
    assert send.await_count == 3
    assert all(c.args[1] != GuestGate.AT_CAPACITY_LINE for c in send.await_args_list)
    assert client._guests_in_flight == 0         # every slot came back


@pytest.mark.asyncio
async def test_a_cap_of_zero_is_floored_at_one(monkeypatch):
    """A cap of 0 used to mean "refuse everyone"; a queue of zero would wait
    forever, so the floor is one turn at a time."""
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 0, raising=False)
    channel = _channel()
    client = _client(channel)

    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=_result())) as disp, \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()):
        await asyncio.wait_for(
            client._handle_guest_message(_msg(channel, author_id=601), channel.id), timeout=5)

    disp.assert_awaited_once()
    assert client._guests_in_flight == 0


@pytest.mark.asyncio
async def test_a_channel_she_cannot_reach_does_not_break_the_owners_lane(monkeypatch):
    """fetch_channel raises Forbidden/NotFound/HTTPException: the reply's send
    fails inside the guest's own turn, and the slot still comes back."""
    channel = _channel()
    client = _client(channel)
    client.get_channel = lambda cid: None

    async def boom(cid):
        raise RuntimeError("Forbidden")

    client.fetch_channel = boom

    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(return_value=_result())) as disp:
        await client._handle_guest_message(_msg(channel, author_id=701), channel.id)

    disp.assert_awaited_once()
    assert client._guests_in_flight == 0


@pytest.mark.asyncio
async def test_a_failed_guest_turn_still_gives_back_its_in_flight_slot(monkeypatch):
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 2, raising=False)
    channel = _channel()
    client = _client(channel)

    with patch("bots.discord.client.dispatch_turn",
               new=AsyncMock(side_effect=RuntimeError("provider down"))), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock()):
        await client._handle_guest_message(_msg(channel, author_id=401), channel.id)

    assert client._guests_in_flight == 0


# ── the gate itself ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_queue_is_first_come_first_served_and_hands_the_slot_over(monkeypatch):
    from bots.common.guest_gate import GuestGate
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 1, raising=False)
    gate = GuestGate()
    order: list[str] = []
    hold = asyncio.Event()

    async def turn(name: str) -> None:
        async with gate.slot():
            order.append(name)
            if name == "first":
                await hold.wait()

    first = asyncio.create_task(turn("first"))
    await asyncio.sleep(0)
    later = [asyncio.create_task(turn(n)) for n in ("second", "third")]
    for _ in range(5):
        await asyncio.sleep(0)
    assert order == ["first"] and gate.in_flight == 1 and gate.waiting == 2
    hold.set()
    await asyncio.gather(first, *later)
    assert order == ["first", "second", "third"]
    assert gate.in_flight == 0 and gate.waiting == 0


@pytest.mark.asyncio
async def test_a_turn_cancelled_while_it_waits_leaves_no_slot_taken(monkeypatch):
    from bots.common.guest_gate import GuestGate
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 1, raising=False)
    gate = GuestGate()
    await gate.acquire()
    waiter = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0)
    assert gate.waiting == 1
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.release()
    assert gate.in_flight == 0 and gate.waiting == 0
    await asyncio.wait_for(gate.acquire(), timeout=1)   # the door is open
    gate.release()


def test_commands_keep_the_non_waiting_admit_and_its_line(monkeypatch):
    """A stranger's /command is a cheap read, not a message she decides on:
    it never waits — over the cap it gets the one line, as before."""
    from bots.common.guest_gate import GuestGate
    from core.config import config

    monkeypatch.setattr(config, "GUEST_MAX_IN_FLIGHT", 1, raising=False)
    gate = GuestGate()
    assert gate.admit(1) is True
    assert gate.admit(2) is False
    gate.release(1)
    assert gate.admit(2) is True
    assert "limit" in GuestGate.AT_CAPACITY_LINE.lower()
