"""The owner's private-chat lane, shared by the Telegram and phone doors
(the mobile door, 2026-10-13). Telegram's own suite proves the move changed
nothing there; this pins the hooks contract both doors build on."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bots.common import steering as steering_fork
from bots.common.owner_lane import LaneHooks, OwnerLane
from core.turn.types import Turn, TurnMetadata


def _turn(text: str) -> Turn:
    return Turn(surface="telegram_text", owner_id=1, content=text,
                metadata=TurnMetadata(channel_id=5, is_dm=True))


def _hooks(log: list, tmp_path: Path, *, slow: asyncio.Event | None = None) -> LaneHooks:
    async def dispatch(turn):
        log.append(("dispatch", turn.content))
        if slow is not None and turn.content == "one":
            await slow.wait()
        return SimpleNamespace(terminated_by="", responded=True, response_text=f"re: {turn.content}")

    async def record(turn, *, sessions_root, transcript=True):
        log.append(("record", turn.content, transcript))

    async def send_text(chat_id, text):
        log.append(("send", text))

    async def deliver(chat_id, result, **kwargs):
        log.append(("deliver", result.response_text))

    class typing:
        def __init__(self, chat_id):
            pass

        async def __aenter__(self):
            log.append(("typing", True))

        async def __aexit__(self, *exc):
            log.append(("typing", False))
            return False

    async def owner_ok(chat_id, authors):
        return authors == {7}

    async def owner_burst(turn, **kwargs):
        log.append(("owner_burst",))
        return turn

    return LaneHooks(door="test", log_name="test lane", dispatch=dispatch, record=record,
                     send_text=send_text, deliver=deliver, typing=typing, owner_ok=owner_ok,
                     owner_burst=owner_burst, sessions_root=lambda: tmp_path)


async def _same(turn):
    return turn


async def _settled():
    for _ in range(50):
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.setattr(steering_fork, "_flag_on", lambda: False)


@pytest.mark.asyncio
async def test_a_burst_is_recorded_on_arrival_then_one_dispatch_inside_typing(tmp_path):
    log: list = []
    lane = OwnerLane(_hooks(log, tmp_path), settle=0.05, typing_window=0.0, max_hold=1.0)
    await lane.arrive(5, _turn("a"), author_id=7, has_attachment=False, resolve=_same)
    await lane.arrive(5, _turn("b"), author_id=7, has_attachment=False, resolve=_same)
    await _settled()
    assert log == [("record", "a", True), ("record", "b", True), ("owner_burst",),
                   ("typing", True), ("dispatch", "a\nb"), ("typing", False), ("deliver", "re: a\nb")]


@pytest.mark.asyncio
async def test_with_the_flag_off_a_new_message_cancels_the_reply_in_flight(tmp_path):
    log: list = []
    slow = asyncio.Event()
    lane = OwnerLane(_hooks(log, tmp_path, slow=slow), settle=0.0, typing_window=0.0, max_hold=1.0)
    await lane.arrive(5, _turn("one"), author_id=7, has_attachment=False, resolve=_same)
    await _settled()
    await lane.arrive(5, _turn("two"), author_id=7, has_attachment=False, resolve=_same)
    await _settled()
    assert ("dispatch", "one") in log and ("dispatch", "two") in log
    assert [e for e in log if e[0] == "deliver"] == [("deliver", "re: two")]


@pytest.mark.asyncio
async def test_a_door_that_hears_typing_ends_it_with_the_message(tmp_path):
    log: list = []
    lane = OwnerLane(_hooks(log, tmp_path), settle=0.0, typing_window=5.0, max_hold=10.0,
                     end_typing_on_message=True)
    lane.note_typing(5, 7)
    await lane.arrive(5, _turn("sent"), author_id=7, has_attachment=False, resolve=_same)
    await _settled()
    assert ("dispatch", "sent") in log           # did not wait out the 5 s typing window


def test_the_telegram_client_reads_and_writes_the_lane_s_state():
    from bots.telegram.client import PalliTelegramClient

    client = PalliTelegramClient(application=SimpleNamespace(bot=None), owner_id=1)
    assert client._inflight_tasks is client._lane.inflight_tasks
    assert client._coalescer is client._lane.coalescer
    replacement: dict = {}
    client._burst_fragments = replacement
    assert client._lane.burst_fragments is replacement
