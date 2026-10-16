"""The typing pause is credited with the wait already behind a reply (2026-10-12).

The 10-10 21:42 reply: the owner waited 18.6 s for PaLLi to settle, read and
write, then watched 8.2 s of typing on top. Now the time already spent since
their last message comes off the pause, down to a one-second beat (the
owner: "keep a 1 second beat if needed"), and a reply with nothing behind it
is paced exactly as before.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# A fixed clock for reply.py (Fable's review, 10-12: real-clock windows
# are flake-prone on a loaded machine). Waits are given as NOW - seconds.
NOW = 5000.0


def _channel():
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock()
    return channel


def _record_pauses(monkeypatch, natural: float) -> list[float]:
    """Every pause the sender takes, without sleeping, at a fixed natural pace.

    reply.py's own ``asyncio`` name is swapped (sleep only, plus the
    CancelledError the cadenced sender catches), so the event loop's sleep
    is untouched."""
    pauses: list[float] = []

    async def fake_sleep(seconds):
        pauses.append(round(seconds, 3))

    monkeypatch.setattr("bots.discord.reply.asyncio", SimpleNamespace(
        sleep=fake_sleep, CancelledError=asyncio.CancelledError))
    monkeypatch.setattr("bots.discord.reply._calculate_typing_delay", lambda text: natural)
    monkeypatch.setattr("bots.discord.reply.time", SimpleNamespace(monotonic=lambda: NOW))
    return pauses


@pytest.mark.parametrize("natural, credit, shown, left", [
    (6.0, 10.0, 1.0, 5.0),    # waited longer than the typing: one beat, the rest carries on
    (6.0, 2.0, 4.0, 0.0),     # waited a little: the pause shrinks by it
    (6.0, 0.0, 6.0, 0.0),     # nothing behind it: paced as before
    (6.0, -3.0, 6.0, 0.0),    # a clock that ran backwards gives no credit
    (0.6, 10.0, 0.6, 10.0),   # a pause already under a beat keeps its length and spends nothing
    (1.0, 5.0, 1.0, 5.0),     # exactly a beat: nothing to give up, nothing spent
])
def test_credited_delay(natural, credit, shown, left) -> None:
    from bots.discord.reply import _credited_delay

    got_shown, got_left = _credited_delay(natural, credit)
    assert got_shown == pytest.approx(shown)
    assert got_left == pytest.approx(left)


@pytest.mark.asyncio
async def test_a_reply_after_a_long_wait_shows_one_beat(monkeypatch) -> None:
    from bots.discord.reply import last_send_typing, send_turn_reply

    pauses = _record_pauses(monkeypatch, natural=6.0)
    channel = _channel()
    delivered = await send_turn_reply(channel, "hi back", waiting_since=NOW - 10.0)
    assert delivered is True
    assert pauses == [1.0]
    assert last_send_typing() == pytest.approx(1.0)
    channel.send.assert_awaited_once_with("hi back")


@pytest.mark.asyncio
async def test_a_short_wait_shortens_the_pause_by_that_much(monkeypatch) -> None:
    from bots.discord.reply import send_turn_reply

    pauses = _record_pauses(monkeypatch, natural=6.0)
    await send_turn_reply(_channel(), "hi back", waiting_since=NOW - 2.0)
    assert pauses == [4.0]              # 6 - 2


@pytest.mark.asyncio
async def test_without_a_wait_the_pace_is_unchanged(monkeypatch) -> None:
    from bots.discord.reply import last_send_typing, send_turn_reply

    pauses = _record_pauses(monkeypatch, natural=6.0)
    await send_turn_reply(_channel(), "a reminder of hers")
    assert pauses == [6.0]
    assert last_send_typing() == pytest.approx(6.0)


@pytest.mark.asyncio
async def test_the_credit_carries_across_the_pieces_of_a_long_reply(monkeypatch) -> None:
    from bots.discord.reply import send_turn_reply

    pauses = _record_pauses(monkeypatch, natural=6.0)
    channel = _channel()
    await send_turn_reply(channel, "a" * 4500, waiting_since=NOW - 10.0)
    # 10 s of credit: the first piece spends 5 of it, the second the other 5,
    # the third is paced as it always was.
    assert pauses == [1.0, 1.0, 6.0]
    assert channel.send.await_count == 3


@pytest.mark.asyncio
async def test_cadenced_beats_are_credited_and_the_gaps_between_them_stay(monkeypatch) -> None:
    from bots.discord.reply import last_send_typing, send_turn_reply_cadenced

    pauses = _record_pauses(monkeypatch, natural=4.0)    # a beat's own pause: 4 * 0.5 = 2.0
    beats = [SimpleNamespace(text="one", delay_after_previous_seconds=0.0),
             SimpleNamespace(text="two", delay_after_previous_seconds=1.5)]
    channel = _channel()
    delivered = await send_turn_reply_cadenced(
        channel, SimpleNamespace(beats=beats, attachments=()),
        waiting_since=NOW - 10.0)
    assert delivered is True
    # typing (beat), the cadence's own 1.5 s gap, typing (beat)
    assert pauses == [1.0, 1.5, 1.0]
    assert last_send_typing() == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_a_single_beat_cadenced_reply_passes_the_wait_on(monkeypatch) -> None:
    from bots.discord.reply import send_turn_reply_cadenced

    pauses = _record_pauses(monkeypatch, natural=6.0)
    beats = [SimpleNamespace(text="only one", delay_after_previous_seconds=0.0)]
    await send_turn_reply_cadenced(
        _channel(), SimpleNamespace(beats=beats, attachments=()),
        waiting_since=NOW - 10.0)
    assert pauses == [1.0]
