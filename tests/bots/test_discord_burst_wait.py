"""A Discord burst carries when its messages arrived, so the reply's typing
pause is credited with the wait behind it and the real wait is logged
(2026-10-12)."""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace
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


def _msg(*, content: str = "hi", author_id: int = 12345):
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.bot = False
    msg.author.id = author_id
    msg.author.name = "user"
    msg.content = content
    msg.attachments = []
    msg.mentions = []
    msg.channel = _channel()
    return msg


def _client(author_id: int = 12345):
    import discord
    from bots.discord.client import PalliDiscordClient

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return author_id

    client._local_owner_discord_id = owner_did   # this author is the owner
    return client


@pytest.mark.asyncio
async def test_a_burst_reply_is_credited_with_the_wait_and_logged(caplog) -> None:
    from core.turn.types import TurnResult

    client = _client()
    msg = _msg(content="hello")
    client.get_channel = lambda cid: msg.channel
    result = TurnResult(responded=True, response_text="hi back", decision=None,
                        reflection_applied=False, delegation_tier_used=0)
    before = time.monotonic()
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=result)), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)) as send, \
         caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        await client.on_message(msg)
        await client._fire_burst(msg.channel.id)

    send.assert_awaited_once()
    since = send.await_args.kwargs["waiting_since"]
    assert before <= since <= time.monotonic()
    waits = [r.getMessage() for r in caplog.records if r.getMessage().startswith("reply wait: ")]
    assert len(waits) == 1 and waits[0].endswith(f"(discord {msg.channel.id})")
    assert msg.channel.id not in client._burst_times     # the stamp left with its burst


@pytest.mark.asyncio
async def test_the_last_fragment_sets_the_credit_and_the_first_stays(caplog) -> None:
    from core.turn.types import TurnResult

    client = _client()
    first, second = _msg(content="so"), _msg(content="what about tomorrow?")
    second.channel = first.channel
    client.get_channel = lambda cid: first.channel
    result = TurnResult(responded=True, response_text="sure", decision=None,
                        reflection_applied=False, delegation_tier_used=0)
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=result)), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)) as send:
        await client.on_message(first)
        stamped_first = client._burst_times[first.channel.id]
        await client.on_message(second)
        stamped_both = client._burst_times[first.channel.id]
        await client._fire_burst(first.channel.id)

    assert stamped_both[0] == stamped_first[0]          # the first arrival stays
    assert stamped_both[1] >= stamped_first[1]           # the last one moves
    assert send.await_args.kwargs["waiting_since"] == stamped_both[1]


def test_requeued_fragments_keep_their_arrival_times() -> None:
    """The typing rule puts a burst back this way (fix 2, 10-12). The room
    read's "wait" requeues the same way when that read is on (a pass-through
    since 10-04, Fable's review)."""
    client = _client()
    # A message arrived while the room read was out; then the read said "wait".
    client._burst_times[5] = (106.0, 107.0)
    client._requeue_burst(5, ["older fragment"], {12345}, (100.0, 104.0))
    assert client._burst_times[5] == (100.0, 107.0)
    assert client._burst_fragments[5] == ["older fragment"]


def test_a_requeue_without_a_stamp_leaves_none_behind() -> None:
    client = _client()
    client._requeue_burst(6, ["fragment"], {12345})
    assert 6 not in client._burst_times


@pytest.mark.asyncio
async def test_a_result_with_no_burst_behind_it_is_paced_as_before_and_not_logged(caplog) -> None:
    client = _client()
    chan = SimpleNamespace(id=555)
    client.get_channel = lambda cid: chan
    with patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)) as send, \
         caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        await client._deliver_result(555, SimpleNamespace(
            responded=True, response_text="a sidecar's answer", attachments=(), cadenced_reply=None))
    assert send.await_args.kwargs["waiting_since"] is None
    assert not [r for r in caplog.records if r.getMessage().startswith("reply wait: ")]


def _owner_result(text: str = "hi back"):
    from core.turn.types import TurnResult

    return TurnResult(responded=True, response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0)


@pytest.mark.asyncio
async def test_the_typing_actually_shown_reaches_the_wait_line(monkeypatch, caplog) -> None:
    """The real sender, end to end (Fable's review, 10-12): what it showed is
    what the line says. A natural pause under a beat is shown as it is."""
    import asyncio as _asyncio

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr("bots.discord.reply.asyncio", SimpleNamespace(
        sleep=no_sleep, CancelledError=_asyncio.CancelledError))
    monkeypatch.setattr("bots.discord.reply._calculate_typing_delay", lambda text: 0.5)
    client = _client()
    msg = _msg(content="hello")
    client.get_channel = lambda cid: msg.channel
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=_owner_result())), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        await client.on_message(msg)
        await client._fire_burst(msg.channel.id)
    msg.channel.send.assert_awaited_once_with("hi back")
    waits = [r.getMessage() for r in caplog.records if r.getMessage().startswith("reply wait: ")]
    assert len(waits) == 1 and "typing shown 0.5s" in waits[0]


@pytest.mark.asyncio
async def test_a_voice_reply_is_credited_and_logged_too(caplog) -> None:
    client = _client()
    msg = _msg(content="say it out loud")
    client.get_channel = lambda cid: msg.channel
    voiced = AsyncMock(return_value=(1, 1, ""))
    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=_owner_result())), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.voice_reply.deliver_if_voice", new=voiced), \
         caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        await client.on_message(msg)
        await client._fire_burst(msg.channel.id)
    assert isinstance(voiced.await_args.kwargs["waiting_since"], float)
    assert [r for r in caplog.records if r.getMessage().startswith("reply wait: ")]


@pytest.mark.asyncio
async def test_a_run_that_more_messages_came_into_says_so(caplog) -> None:
    client = _client()
    msg = _msg(content="fix the build")

    async def run(turn):
        # The owner writes again while the run works (steered into it).
        client._owner_messages_in[msg.channel.id] = client._owner_messages_in.get(msg.channel.id, 0) + 1
        return _owner_result("done, and the tests too")

    client.get_channel = lambda cid: msg.channel
    with patch("bots.discord.client.dispatch_turn", new=run), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)), \
         caplog.at_level(logging.INFO, logger="bots.common.reply_wait"):
        await client.on_message(msg)
        await client._fire_burst(msg.channel.id)
    waits = [r.getMessage() for r in caplog.records if r.getMessage().startswith("reply wait: ")]
    assert len(waits) == 1 and waits[0].endswith("; 1 more message came in during the run")
