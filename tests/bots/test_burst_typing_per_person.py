"""Typing is tracked per person, and a person's own message ends their typing
(2026-10-12).

The 10-10 21:42 reply: Discord reported the owner typing at 21:42:20.5, the
message arrived at 21:42:21.97, and PaLLi kept waiting until 21:42:30.5 for
typing that had already ended in the message: 8.5 s of a 26.8 s wait. Discord
takes the typing indicator down the moment the message arrives; now PaLLi
does too, keeping the 3 s pause for a second line (the owner, 10-12: "3 is
good"). Anyone else still typing still holds the burst, as before.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bots.common import burst as mod

OWNER, FRIEND = 12345, 777


def _coalescer(clock):
    return mod.BurstCoalescer(on_fire=AsyncMock(), settle=3.0, typing_window=10.0,
                              max_hold=45.0, clock=lambda: clock["now"])


def _ready_at(c, channel, now):
    decision, wake = mod.evaluate(c._state[channel], now, settle=3.0,
                                  typing_window=10.0, max_hold=45.0)
    return None if decision == "fire" else wake


@pytest.mark.asyncio
async def test_a_message_ends_its_authors_typing() -> None:
    clock = {"now": 100.0}
    c = _coalescer(clock)
    c.note_typing(1, OWNER)
    clock["now"] = 101.5
    c.note_message(1, OWNER)
    assert _ready_at(c, 1, 101.5) == 104.5        # the 3 s pause only, not 110.0
    c.mark_replied(1)


@pytest.mark.asyncio
async def test_someone_else_still_typing_still_holds() -> None:
    clock = {"now": 100.0}
    c = _coalescer(clock)
    c.note_typing(1, FRIEND)
    clock["now"] = 101.5
    c.note_message(1, OWNER)
    assert _ready_at(c, 1, 101.5) == 110.0
    c.mark_replied(1)


@pytest.mark.asyncio
async def test_typing_after_the_message_still_holds() -> None:
    clock = {"now": 100.0}
    c = _coalescer(clock)
    c.note_message(1, OWNER)
    clock["now"] = 101.0
    c.note_typing(1, OWNER)                        # a second line on its way
    assert _ready_at(c, 1, 101.0) == 111.0
    c.mark_replied(1)


@pytest.mark.asyncio
async def test_a_line_that_does_not_join_the_burst_ends_its_authors_typing() -> None:
    clock = {"now": 100.0}
    c = _coalescer(clock)
    c.note_typing(1, FRIEND)
    clock["now"] = 101.0
    c.note_message(1, OWNER)
    assert _ready_at(c, 1, 101.0) == 110.0
    clock["now"] = 102.0
    c.note_sent(1, FRIEND)                         # their line arrived
    assert _ready_at(c, 1, 102.0) == 104.0
    c.note_sent(1, 4242)                           # nobody typing under that id: nothing
    c.note_sent(2, FRIEND)                         # no burst in that channel: nothing
    assert 2 not in c._state
    c.mark_replied(1)


@pytest.mark.asyncio
async def test_callers_that_name_nobody_keep_the_old_single_stamp() -> None:
    clock = {"now": 100.0}
    c = _coalescer(clock)
    c.note_typing(1)
    clock["now"] = 101.5
    c.note_message(1)
    assert _ready_at(c, 1, 101.5) == 110.0
    c.mark_replied(1)


def _msg(content: str, author_id: int):
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.bot = False
    msg.author.id = author_id
    msg.author.name = "user"
    msg.content = content
    msg.attachments = []
    msg.mentions = []
    msg.channel = MagicMock()
    msg.channel.id = 67890
    return msg


def _typist(user_id: int):
    user = MagicMock()
    user.id = user_id
    user.bot = False
    return user


def _client():
    import discord
    from bots.discord.client import PalliDiscordClient

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return OWNER

    client._local_owner_discord_id = owner_did
    return client


@pytest.mark.asyncio
async def test_the_traced_reply_waits_three_seconds_after_the_message_not_ten() -> None:
    clock = {"now": 1000.0}
    client = _client()
    client._coalescer._clock = lambda: clock["now"]
    dispatched = []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        from core.turn.types import TurnResult
        return TurnResult(responded=False, response_text="", decision=None,
                          reflection_applied=False, delegation_tier_used=0)

    channel = MagicMock()
    channel.id = 67890
    with patch("bots.discord.client.dispatch_turn", new=fake_dispatch), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()):
        await client.on_typing(channel, _typist(OWNER), None)        # 21:42:20.5
        clock["now"] += 1.5
        await client.on_message(_msg("how's the build going", OWNER))  # 21:42:21.97
        clock["now"] += 2.9                                             # inside the pause
        for _ in range(10):
            await asyncio.sleep(0.02)
        assert dispatched == []
        clock["now"] += 0.2                                             # 3.1 s: not 10
        for _ in range(50):
            await asyncio.sleep(0.02)
            if dispatched:
                break
    assert [t.content for t in dispatched] == ["how's the build going"]


@pytest.mark.asyncio
async def test_a_guests_line_ends_the_guests_typing_in_the_room() -> None:
    client = _client()
    channel = MagicMock()
    channel.id = 67890
    await client.on_typing(channel, _typist(FRIEND), None)
    assert FRIEND in client._coalescer._state[67890].typing_until
    with patch.object(client, "_handle_guest_message", new=AsyncMock()) as guest:
        await client.on_message(_msg("hey all", FRIEND))
    guest.assert_awaited_once()
    assert FRIEND not in client._coalescer._state[67890].typing_until
    client._coalescer.mark_replied(67890)
