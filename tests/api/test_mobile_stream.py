"""WS /api/mobile/stream (spec §5): replay then live, acks move the cursor,
typing reaches the burst, and a revoked phone is closed with 4401."""
from __future__ import annotations

import asyncio
import functools
import time
import types

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api import mobile
from bots.mobile import client as door_mod
from bots.mobile.hub import hub
from core.mobile_device_store import MobileDeviceStore
from core.mobile_events_store import MobileEventsStore


class FakeDoor:
    owner_id = 1

    def __init__(self) -> None:
        self.typing = 0

    async def accept(self, turn):
        pass

    def note_typing(self):
        self.typing += 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "stream.sqlite3"))
    asyncio.run(brain.initialize())
    monkeypatch.setattr("core.database.brain", brain)
    door = FakeDoor()
    monkeypatch.setattr(door_mod, "_door", door)

    async def pair():
        devices = MobileDeviceStore(brain)
        await devices.create_pair_code(owner_id=1, code="SSSS0001")
        return await devices.redeem(code="SSSS0001", name="Phone", token="pmd_stream")

    device = asyncio.run(pair())
    return brain, door, device


async def _until_typed(door) -> None:
    for _ in range(1500):
        if door.typing:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the server never handled the typing frame")


async def _until_acked(brain, owner_id: int, through: int) -> None:
    for _ in range(1500):
        devices = await MobileDeviceStore(brain).list_devices(owner_id=owner_id)
        if devices[0]["acked_through"] >= through:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the server never recorded the ack")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/mobile")
    return TestClient(app)


AUTH = {"Authorization": "Bearer pmd_stream"}


def test_the_stream_replays_then_goes_live_and_an_ack_moves_the_cursor(env):
    brain, door, device = env
    store = MobileEventsStore(brain)
    before = asyncio.run(store.append(owner_id=1, kind="message", payload={"text": "while you were away"}))
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        assert ws.receive_json() == {"type": "event", "event": before}
        live = ws.portal.call(functools.partial(store.append, owner_id=1, kind="message", payload={"text": "now"}))
        ws.portal.call(functools.partial(hub.publish, live, owner_id=1))
        assert ws.receive_json() == {"type": "event", "event": live}
        ws.send_json({"type": "ack", "through": live["id"]})
        ws.send_json({"type": "typing"})
        # The test client cancels the app the moment this block exits, so wait
        # until the server has handled both frames (they are read in order).
        ws.portal.call(_until_typed, door)
    devices = asyncio.run(MobileDeviceStore(brain).list_devices(owner_id=1))
    assert devices[0]["acked_through"] == live["id"]
    assert door.typing == 1


def test_a_cursor_ahead_of_the_timeline_still_hears_live_events(env, monkeypatch, caplog):
    """Review M1: a phone whose cursor is past the newest id (a database
    restored from a backup, a corrupted cursor) is clamped at connect, so a
    live event appended afterwards still reaches it, and the clamp is logged
    once with both numbers."""
    brain, door, device = env
    monkeypatch.setattr(mobile, "PING_EVERY_S", 0.05)
    store = MobileEventsStore(brain)
    newest = asyncio.run(store.append(owner_id=1, kind="message", payload={"text": "before the restore"}))
    far = 10**9
    with caplog.at_level("INFO", logger="api.mobile"):
        with _client().websocket_connect(f"/api/mobile/stream?after={far}", headers=AUTH) as ws:
            assert ws.receive_json() == {"type": "ping"}          # replayed nothing; the stream is up
            live = ws.portal.call(functools.partial(store.append, owner_id=1, kind="message",
                                                    payload={"text": "after it"}))
            ws.portal.call(functools.partial(hub.publish, live, owner_id=1))
            # Pings sent while the append ran come first; one lands every 50 ms,
            # so this never blocks for long, and a deaf stream fails in 5 s.
            deadline = time.monotonic() + 5.0
            frame = ws.receive_json()
            while frame["type"] == "ping" and time.monotonic() < deadline:
                frame = ws.receive_json()
            assert frame == {"type": "event", "event": live}
    clamps = [r for r in caplog.records if r.name == "api.mobile" and str(far) in r.getMessage()]
    assert len(clamps) == 1 and clamps[0].levelname == "INFO"
    assert str(newest["id"]) in clamps[0].getMessage()


def test_no_token_is_closed_with_4401(env):
    with _client().websocket_connect("/api/mobile/stream") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


def test_a_revoked_device_is_closed_at_the_next_ping(env, monkeypatch):
    brain, door, device = env
    monkeypatch.setattr(mobile, "PING_EVERY_S", 0.05)
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        asyncio.run(MobileDeviceStore(brain).revoke(owner_id=1, device_id=device["id"]))
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                assert ws.receive_json()["type"] == "ping"
    assert closed.value.code == 4401


def test_a_failed_token_check_does_not_end_the_pings(env, monkeypatch):
    brain, door, device = env
    monkeypatch.setattr(mobile, "PING_EVERY_S", 0.05)
    real = mobile.device_store
    lookups = []

    class Flaky:
        def __init__(self) -> None:
            self._real = real()

        async def device_for_token(self, token):
            lookups.append(token)
            if len(lookups) == 1:
                raise OSError("database is locked")
            return await self._real.device_for_token(token)

    monkeypatch.setattr(mobile, "device_store", Flaky)
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        assert ws.receive_json() == {"type": "ping"}          # the first lookup failed: not a revoke
        assert ws.receive_json() == {"type": "ping"}          # and the loop went on to the next
    assert len(lookups) >= 2


def test_a_garbled_ack_is_ignored_and_the_socket_stays_open(env):
    brain, door, device = env
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        ws.send_json({"type": "ack", "through": "soon"})
        ws.send_json({"type": "ack", "through": None})
        ws.send_json({"type": "typing"})
        ws.portal.call(_until_typed, door)
    assert door.typing == 1
    assert asyncio.run(MobileDeviceStore(brain).list_devices(owner_id=1))[0]["acked_through"] == 0


class StuckSocket:
    """Just enough of a WebSocket to drive stream() by hand: it takes a token,
    and blocks wherever it is told to (so a cancel can be aimed)."""

    def __init__(self, token: str, *, stuck_in: str) -> None:
        self.headers = {"authorization": f"Bearer {token}"}
        self.state = types.SimpleNamespace()
        self.stuck_in = stuck_in
        self.stuck = asyncio.Event()
        self.closed = None

    async def accept(self) -> None:
        pass

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    async def send_json(self, data) -> None:
        if self.stuck_in == "replay":
            self.stuck.set()
            await asyncio.Event().wait()

    async def receive_json(self):
        self.stuck.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("stuck_in", ["replay", "steady"])
async def test_a_cancelled_handler_leaves_the_hub_at_once_and_awaits_nothing_on_the_way_out(env, stuck_in):
    brain, door, device = env
    await MobileEventsStore(brain).append(owner_id=1, kind="message", payload={"text": "to replay"})
    sock = StuckSocket("pmd_stream", stuck_in=stuck_in)
    before = set(asyncio.all_tasks())
    handler = asyncio.create_task(mobile.stream(sock, after=0))
    await asyncio.wait_for(sock.stuck.wait(), 15)
    assert len(hub._for(1)) == 1

    handler.cancel()
    await asyncio.sleep(0)                 # ONE turn of the loop: the finally has nothing to wait for
    assert handler.cancelled() and hub._for(1) == []
    for _ in range(20):                    # and the siblings it started wind down by themselves
        if set(asyncio.all_tasks()) == before:
            break
        await asyncio.sleep(0)
    assert set(asyncio.all_tasks()) == before


def test_the_hub_forgets_a_socket_when_the_phone_goes(env):
    brain, door, device = env
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        ws.send_json({"type": "typing"})
        ws.portal.call(_until_typed, door)                    # past the replay: the socket is on the hub
        assert len(hub._for(1)) == 1
    assert hub._for(1) == []


def test_live_events_published_out_of_id_order_all_reach_the_phone(env):
    brain, door, device = env
    store = MobileEventsStore(brain)
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        ws.send_json({"type": "typing"})
        ws.portal.call(_until_typed, door)                    # past the (empty) replay
        a = ws.portal.call(functools.partial(store.append, owner_id=1, kind="message", payload={"text": "a"}))
        b = ws.portal.call(functools.partial(store.append, owner_id=1, kind="message", payload={"text": "b"}))
        assert a["id"] < b["id"]
        ws.portal.call(functools.partial(hub.publish, b, owner_id=1))      # two emits racing: b's push first
        ws.portal.call(functools.partial(hub.publish, a, owner_id=1))
        assert ws.receive_json() == {"type": "event", "event": b}
        assert ws.receive_json() == {"type": "event", "event": a}          # a is not dropped for being lower


def test_an_event_the_replay_already_sent_is_not_sent_twice(env):
    brain, door, device = env
    store = MobileEventsStore(brain)
    first = asyncio.run(store.append(owner_id=1, kind="message", payload={"text": "replayed"}))
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        assert ws.receive_json() == {"type": "event", "event": first}
        ws.send_json({"type": "typing"})
        ws.portal.call(_until_typed, door)
        ws.portal.call(functools.partial(hub.publish, first, owner_id=1))   # a push of what was replayed
        second = ws.portal.call(functools.partial(store.append, owner_id=1, kind="message", payload={"text": "new"}))
        ws.portal.call(functools.partial(hub.publish, second, owner_id=1))
        assert ws.receive_json() == {"type": "event", "event": second}


def test_a_token_that_cannot_be_rechecked_three_pings_running_closes_the_socket(env, monkeypatch):
    brain, door, device = env
    monkeypatch.setattr(mobile, "PING_EVERY_S", 0.05)

    class Down:
        async def device_for_token(self, token):
            raise OSError("database is locked")

    monkeypatch.setattr(mobile, "device_store", Down)
    pings = 0
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                assert ws.receive_json()["type"] == "ping"
                pings += 1
    assert closed.value.code == mobile.CLOSE_TRY_AGAIN == 1013
    assert pings == mobile.RECHECKS_BEFORE_CLOSE - 1             # the blips kept it; the third ended it


def test_a_good_recheck_between_failures_starts_the_count_again(env, monkeypatch):
    brain, door, device = env
    monkeypatch.setattr(mobile, "PING_EVERY_S", 0.05)
    real = mobile.device_store
    calls = []

    class EveryThird:
        def __init__(self) -> None:
            self._real = real()

        async def device_for_token(self, token):
            calls.append(token)
            if len(calls) % 3:
                raise OSError("database is locked")
            return await self._real.device_for_token(token)

    monkeypatch.setattr(mobile, "device_store", EveryThird)
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        for _ in range(6):                                       # two failures then a good one, twice over
            assert ws.receive_json() == {"type": "ping"}
    assert len(calls) >= 6


def test_an_ack_never_passes_the_newest_event_and_a_garbled_one_moves_nothing(env):
    brain, door, device = env
    store = MobileEventsStore(brain)
    asyncio.run(store.append(owner_id=1, kind="message", payload={"text": "one"}))
    newest = asyncio.run(store.append(owner_id=1, kind="message", payload={"text": "two"}))
    with _client().websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
        ws.receive_json()
        ws.receive_json()
        for through in (float("inf"), float("-inf"), float("nan"), -7, 0, "soon", [3], {"n": 3}, None, 1e400):
            ws.send_json({"type": "ack", "through": through})
        ws.send_json({"type": "ack", "through": 5000})              # a lie about the future: clamped
        ws.send_json({"type": "ack", "through": 10 ** 30})          # and one SQLite could not even hold
        ws.portal.call(_until_acked, brain, 1, newest["id"])
        ws.send_json({"type": "typing"})
        ws.portal.call(_until_typed, door)                         # the loop survived all of it
    devices = asyncio.run(MobileDeviceStore(brain).list_devices(owner_id=1))
    assert devices[0]["acked_through"] == newest["id"]
    assert door.typing == 1


def test_revoking_through_the_route_closes_the_open_socket_at_once(env):
    brain, door, device = env
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/mobile")
    assert mobile.PING_EVERY_S >= 25.0                            # the close below is not a ping's doing
    with TestClient(app) as client:                               # one loop for the socket and the DELETE
        with client.websocket_connect("/api/mobile/stream?after=0", headers=AUTH) as ws:
            ws.send_json({"type": "typing"})
            ws.portal.call(_until_typed, door)                    # the socket is on the hub
            gone = client.delete(f"/api/mobile/devices/{device['id']}", headers=AUTH)
            assert gone.status_code == 200
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
    assert closed.value.code == 4401


def test_another_owners_typing_does_not_hold_his_burst(env):
    brain, door, device = env

    async def pair_a_stranger():
        devices = MobileDeviceStore(brain)
        await devices.create_pair_code(owner_id=2, code="SSSS0002")
        await devices.redeem(code="SSSS0002", name="Stranger", token="pmd_stranger")
        return await MobileEventsStore(brain).append(owner_id=2, kind="message", payload={"text": "not his"})

    theirs = asyncio.run(pair_a_stranger())
    with _client().websocket_connect("/api/mobile/stream?after=0",
                                     headers={"Authorization": "Bearer pmd_stranger"}) as ws:
        assert ws.receive_json() == {"type": "event", "event": theirs}
        ws.send_json({"type": "typing"})
        ws.send_json({"type": "ack", "through": theirs["id"]})     # read in order: once this is recorded, typing was seen
        ws.portal.call(_until_acked, brain, 2, theirs["id"])
    assert door.typing == 0
