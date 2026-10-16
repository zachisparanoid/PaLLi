"""The owner typing again while their reply is still being prepared cancels the
preparation and waits for the next message (2026-10-12, the owner: "yes, add
the typing rule to fix 2").

With the pause after a message down to 3 s, a second message often starts
after PaLLi has begun preparing (two weeks of logs: Discord reports the
owner typing again a median 4.3 s after their message). Nothing has been
written or shown during preparation, so stopping there costs nothing the
owner sees, and the reply answers both messages. A reply being written, a
tool at work, or a reply on its way out is never cancelled by typing.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent import steering as _steering

OWNER, FRIEND, CHANNEL = 12345, 777, 67890


def _msg(content: str, author_id: int = OWNER):
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.bot = False
    msg.author.id = author_id
    msg.author.name = "user"
    msg.content = content
    msg.attachments = []
    msg.mentions = []
    msg.channel = MagicMock()
    msg.channel.id = CHANNEL
    return msg


def _typist(user_id: int):
    user = MagicMock()
    user.id = user_id
    user.bot = False
    return user


def _channel():
    channel = MagicMock()
    channel.id = CHANNEL
    return channel


def _client(clock):
    import discord
    from bots.discord.client import PalliDiscordClient

    client = PalliDiscordClient(bot_user_id=99999, owner_id=1, intents=discord.Intents.default())

    async def owner_did():
        return OWNER

    client._local_owner_discord_id = owner_did
    client._coalescer._clock = lambda: clock["now"]
    client.get_channel = lambda cid: _channel()   # no gateway in tests
    return client


def _result(text: str = ""):
    from core.turn.types import TurnResult

    return TurnResult(responded=bool(text), response_text=text, decision=None,
                      reflection_applied=False, delegation_tier_used=0)


class _Runs:
    """A stand-in for dispatch_turn: the runs numbered in ``park`` (1 = the
    first) park in the given phase until released; the others return at once.
    ``ended``: the run has finished (its phase cleared, as runner.py:1555 does)
    and parks while dispatch saves the reply, before it is sent.
    ``clear_on_exit=False``: a cancel leaves the phase behind, as a cancel in
    the runner's pre-loop does. Records what each run got."""

    def __init__(self, phase: str, *, park=(1,), ended: bool = False,
                 clear_on_exit: bool = True):
        self.phase = phase
        self.park = set(park)
        self.ended = ended
        self.clear_on_exit = clear_on_exit
        self.contents: list[str] = []
        self.keys: list[str] = []
        self.parked = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, turn):
        from core.agent.runner import _derive_session_key

        key = _derive_session_key(turn)
        self.contents.append(turn.content)
        self.keys.append(key)
        if len(self.contents) in self.park:
            if self.phase:
                _steering.set_run_phase(key, self.phase)
            if self.ended:
                _steering.clear_run_state(key)
            self.parked.set()
            try:
                await self.release.wait()
            finally:
                self.parked.clear()           # released or cancelled: no longer parked
                if self.clear_on_exit:
                    _steering.clear_run_state(key)
            self.release.clear()
        return _result("ok")


async def _until(predicate, turns: int = 100) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0.02)


async def _settle_and_fire(client, clock, runs: _Runs) -> asyncio.Task:
    """The first message is in; let the 3 s pause pass so the burst fires."""
    clock["now"] += 3.5
    await _until(lambda: runs.parked.is_set())
    assert runs.parked.is_set(), "the first run never started"
    task = client._inflight_tasks.get(CHANNEL)
    assert task is not None
    return task


@pytest.mark.asyncio
async def test_typing_while_preparing_cancels_and_one_reply_answers_both() -> None:
    clock = {"now": 1000.0}
    client = _client(clock)
    runs = _Runs(phase="prep")
    with patch("bots.discord.client.dispatch_turn", new=runs), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)):
        await client.on_message(_msg("can you check the build"))
        first = await _settle_and_fire(client, clock, runs)

        await client.on_typing(_channel(), _typist(OWNER), None)   # a second line on its way
        await _until(lambda: first.done())
        assert first.cancelled()
        assert [f.content for f in client._burst_fragments[CHANNEL]] == ["can you check the build"]
        assert CHANNEL not in client._preparing
        # One timer waits for the burst; nothing is queued for the dead run.
        assert not client._coalescer._tasks[CHANNEL].done()
        assert _steering.peek(runs.keys[0]) == []

        clock["now"] += 2.0
        await client.on_message(_msg("the arm64 one"))
        clock["now"] += 3.5
        await _until(lambda: len(runs.contents) == 2)
    assert runs.contents == ["can you check the build", "can you check the build\nthe arm64 one"]
    client._coalescer.mark_replied(CHANNEL)


@pytest.mark.asyncio
async def test_with_no_second_message_the_burst_fires_again_once_typing_lapses() -> None:
    clock = {"now": 1000.0}
    client = _client(clock)
    runs = _Runs(phase="prep")
    with patch("bots.discord.client.dispatch_turn", new=runs), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)):
        await client.on_message(_msg("can you check the build"))
        first = await _settle_and_fire(client, clock, runs)
        await client.on_typing(_channel(), _typist(OWNER), None)   # typed, then deleted it
        await _until(lambda: first.done())

        clock["now"] += 5.0                                          # typing still showing
        for _ in range(10):
            await asyncio.sleep(0.02)
        assert len(runs.contents) == 1
        clock["now"] += 6.0                                          # past the 10 s window
        await _until(lambda: len(runs.contents) == 2)
    assert runs.contents == ["can you check the build", "can you check the build"]
    client._coalescer.mark_replied(CHANNEL)


@pytest.mark.asyncio
async def test_a_reply_being_written_is_not_cancelled() -> None:
    clock = {"now": 1000.0}
    client = _client(clock)
    runs = _Runs(phase="generating")
    with patch("bots.discord.client.dispatch_turn", new=runs), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)) as send:
        await client.on_message(_msg("can you check the build"))
        first = await _settle_and_fire(client, clock, runs)
        await client.on_typing(_channel(), _typist(OWNER), None)
        for _ in range(5):
            await asyncio.sleep(0)
        assert not first.done()
        runs.release.set()
        await _until(lambda: first.done())
    assert not first.cancelled()
    send.assert_awaited_once()
    client._coalescer.mark_replied(CHANNEL)


@pytest.mark.asyncio
async def test_a_tool_at_work_is_not_cancelled(monkeypatch) -> None:
    clock = {"now": 1000.0}
    client = _client(clock)
    runs = _Runs(phase="prep")
    monkeypatch.setattr(_steering, "is_working", lambda key: True)
    with patch("bots.discord.client.dispatch_turn", new=runs), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)):
        await client.on_message(_msg("can you check the build"))
        first = await _settle_and_fire(client, clock, runs)
        await client.on_typing(_channel(), _typist(OWNER), None)
        for _ in range(5):
            await asyncio.sleep(0)
        assert not first.done()
        runs.release.set()
        await _until(lambda: first.done())
    assert not first.cancelled()
    client._coalescer.mark_replied(CHANNEL)


@pytest.mark.asyncio
async def test_a_friend_typing_does_not_cancel_the_owners_reply() -> None:
    clock = {"now": 1000.0}
    client = _client(clock)
    runs = _Runs(phase="prep")
    with patch("bots.discord.client.dispatch_turn", new=runs), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)):
        await client.on_message(_msg("can you check the build"))
        first = await _settle_and_fire(client, clock, runs)
        await client.on_typing(_channel(), _typist(FRIEND), None)
        for _ in range(5):
            await asyncio.sleep(0)
        assert not first.done()
        runs.release.set()
        await _until(lambda: first.done())
    assert not first.cancelled()
    client._coalescer.mark_replied(CHANNEL)


@pytest.mark.asyncio
async def test_a_reply_on_its_way_out_is_not_cancelled() -> None:
    clock = {"now": 1000.0}
    client = _client(clock)
    sending, release = asyncio.Event(), asyncio.Event()

    async def slow_send(*args, **kwargs):
        sending.set()
        await release.wait()
        return True

    with patch("bots.discord.client.dispatch_turn", new=AsyncMock(return_value=_result("on it"))), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=slow_send):
        await client.on_message(_msg("can you check the build"))
        clock["now"] += 3.5
        await _until(lambda: sending.is_set())
        assert sending.is_set()
        task = client._inflight_tasks[CHANNEL]
        assert CHANNEL not in client._preparing                   # written: no longer preparing
        await client.on_typing(_channel(), _typist(OWNER), None)
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done()
        release.set()
        await _until(lambda: task.done())
    assert not task.cancelled()
    client._coalescer.mark_replied(CHANNEL)


@pytest.mark.asyncio
async def test_a_finished_reply_not_yet_sent_is_not_cancelled() -> None:
    """Fable's review (10-12): after the run ends its phase is empty while
    dispatch still saves the reply; typing then must not cost the reply."""
    clock = {"now": 1000.0}
    client = _client(clock)
    runs = _Runs(phase="prep", ended=True)
    with patch("bots.discord.client.dispatch_turn", new=runs), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)) as send:
        await client.on_message(_msg("can you check the build"))
        first = await _settle_and_fire(client, clock, runs)
        await client.on_typing(_channel(), _typist(OWNER), None)
        for _ in range(5):
            await asyncio.sleep(0)
        assert not first.done()
        runs.release.set()
        await _until(lambda: first.done())
    assert not first.cancelled()
    send.assert_awaited_once()
    assert runs.contents == ["can you check the build"]
    client._coalescer.mark_replied(CHANNEL)


@pytest.mark.asyncio
async def test_a_cancel_leaves_no_stale_prep_behind() -> None:
    clock = {"now": 1000.0}
    client = _client(clock)
    runs = _Runs(phase="prep", clear_on_exit=False)    # the runner's pre-loop has no finally
    with patch("bots.discord.client.dispatch_turn", new=runs), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)):
        await client.on_message(_msg("can you check the build"))
        first = await _settle_and_fire(client, clock, runs)
        await client.on_typing(_channel(), _typist(OWNER), None)
        await _until(lambda: first.done())
    assert first.cancelled()
    assert _steering.get_run_phase(runs.keys[0]) == ""
    client._coalescer.mark_replied(CHANNEL)


@pytest.mark.asyncio
async def test_typing_on_and_on_still_gets_an_answer_by_the_max_hold() -> None:
    """Fable's review (10-12): a burst put back keeps its first message's time,
    and is not put back again once it has waited the max hold (45 s), so
    someone typing one long message still gets the first one answered."""
    clock = {"now": 1000.0}
    client = _client(clock)
    runs = _Runs(phase="prep", park=(1, 2))
    with patch("bots.discord.client.dispatch_turn", new=runs), \
         patch("bots.discord.client.record_user_fragment", new=AsyncMock()), \
         patch("bots.discord.client.send_turn_reply", new=AsyncMock(return_value=True)) as send:
        await client.on_message(_msg("can you check the build"))        # 1000.0
        first = await _settle_and_fire(client, clock, runs)               # fires at 1003.5
        await client.on_typing(_channel(), _typist(OWNER), None)
        await _until(lambda: first.done())
        assert first.cancelled()
        assert client._coalescer._state[CHANNEL].first_unanswered_at == 1000.0

        for _ in range(4):                                                # typing every 9 s
            clock["now"] += 9.0
            await client.on_typing(_channel(), _typist(OWNER), None)
            for _ in range(5):
                await asyncio.sleep(0.02)
        assert len(runs.contents) == 1                                    # 1039.5: still waiting

        clock["now"] = 1045.5                                             # the max hold
        await _until(lambda: runs.parked.is_set())
        second = client._inflight_tasks[CHANNEL]
        clock["now"] = 1048.5
        await client.on_typing(_channel(), _typist(OWNER), None)          # past the max hold
        for _ in range(5):
            await asyncio.sleep(0)
        assert not second.done()
        runs.release.set()
        await _until(lambda: second.done())
    assert not second.cancelled()
    assert runs.contents == ["can you check the build", "can you check the build"]
    send.assert_awaited_once()
    client._coalescer.mark_replied(CHANNEL)
