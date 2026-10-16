"""The real MobileHub: which open phones get which frame (spec §5). No mocks."""
from __future__ import annotations

import pytest

from bots.mobile.hub import CLOSE_REVOKED, MobileHub

D1, D2, D3 = 11, 12, 21                          # two phones of owner 1, one of owner 2


def _drain(conn) -> list[dict]:
    frames = []
    while not conn.queue.empty():
        frames.append(conn.queue.get_nowait())
    return frames


def _phones():
    hub = MobileHub()
    return (hub, hub.register(device_id=D1, owner_id=1), hub.register(device_id=D2, owner_id=1),
            hub.register(device_id=D3, owner_id=2))


@pytest.mark.asyncio
async def test_an_event_reaches_only_that_owners_open_phones_and_counts_them():
    hub, c1, c2, other = _phones()
    event = {"id": 7, "kind": "message", "at": 1.0, "text": "hi"}
    assert await hub.publish(event, owner_id=1) == 2
    assert _drain(c1) == [{"type": "event", "event": event}]
    assert _drain(c2) == [{"type": "event", "event": event}]
    assert _drain(other) == []
    assert await hub.publish(event, owner_id=3) == 0             # no phone open: counted as none


@pytest.mark.asyncio
async def test_typing_reaches_only_that_owners_open_phones():
    hub, c1, c2, other = _phones()
    await hub.set_typing(True, owner_id=1)
    assert _drain(c1) == [{"type": "typing", "on": True}]
    assert _drain(c2) == [{"type": "typing", "on": True}]
    assert _drain(other) == []
    await hub.set_typing(False, owner_id=1)
    assert _drain(c1) == [{"type": "typing", "on": False}]


@pytest.mark.asyncio
async def test_revoking_a_phone_closes_only_its_own_sockets():
    hub, c1, c2, other = _phones()
    assert hub.revoke(D1) == 1
    assert _drain(c1) == [{"type": "_close", "code": CLOSE_REVOKED}] and CLOSE_REVOKED == 4401
    assert _drain(c2) == [] and _drain(other) == []
    assert hub.revoke(999) == 0                                   # a phone with nothing open


@pytest.mark.asyncio
async def test_a_phone_with_two_sockets_has_both_closed():
    hub = MobileHub()
    first, second = hub.register(device_id=D1, owner_id=1), hub.register(device_id=D1, owner_id=1)
    assert hub.revoke(D1) == 2
    assert _drain(first) == [{"type": "_close", "code": CLOSE_REVOKED}]
    assert _drain(second) == [{"type": "_close", "code": CLOSE_REVOKED}]


@pytest.mark.asyncio
async def test_an_unregistered_connection_gets_no_further_frames():
    hub, c1, c2, other = _phones()
    hub.unregister(c1)
    assert await hub.publish({"id": 1, "kind": "message", "at": 1.0, "text": "x"}, owner_id=1) == 1
    await hub.set_typing(True, owner_id=1)
    assert hub.revoke(D1) == 0
    assert _drain(c1) == []
    assert [f["type"] for f in _drain(c2)] == ["event", "typing"]
    hub.unregister(c1)                                            # twice is harmless
