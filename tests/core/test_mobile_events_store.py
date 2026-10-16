"""The mobile door's timeline and the files its events point at (spec §4)."""
from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio

from core.mobile_events_store import MobileEventsStore


@pytest_asyncio.fixture
async def db(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "events.sqlite3"))
    await brain.initialize()
    try:
        yield brain
    finally:
        await brain.close()


@pytest.fixture
def store(db):
    ticks = iter(float(t) for t in range(100, 10_000))
    return MobileEventsStore(db, now=lambda: next(ticks))


@pytest.mark.asyncio
async def test_an_event_comes_back_flat_with_its_id_kind_and_time(store):
    event = await store.append(owner_id=1, kind="message", payload={"text": "hi", "reminder": False})
    assert event == {"id": event["id"], "kind": "message", "at": 100.0, "text": "hi", "reminder": False}

    mine = await store.append(owner_id=1, kind="user_message", payload={"text": "yo", "files": []},
                              client_id="9f1b2c3d-0000-4000-8000-000000000001")
    assert mine["client_id"] == "9f1b2c3d-0000-4000-8000-000000000001"
    assert await store.has_client_id("9f1b2c3d-0000-4000-8000-000000000001") is True
    assert await store.has_client_id("nope") is False


@pytest.mark.asyncio
async def test_an_unknown_kind_is_refused(store):
    with pytest.raises(ValueError):
        await store.append(owner_id=1, kind="shout", payload={})


@pytest.mark.asyncio
async def test_a_client_id_is_stored_once(store):
    await store.append(owner_id=1, kind="user_message", payload={"text": "a"}, client_id="same")
    with pytest.raises(sqlite3.IntegrityError):
        await store.append(owner_id=1, kind="user_message", payload={"text": "b"}, client_id="same")


@pytest.mark.asyncio
async def test_paging_after_is_ascending_and_before_is_descending(store):
    ids = [(await store.append(owner_id=1, kind="message", payload={"text": str(i)}))["id"]
           for i in range(5)]
    await store.append(owner_id=2, kind="message", payload={"text": "someone else"})

    page, more = await store.after(owner_id=1, after=ids[0], limit=2)
    assert [e["id"] for e in page] == ids[1:3] and more is True
    page, more = await store.after(owner_id=1, after=ids[2], limit=10)
    assert [e["id"] for e in page] == ids[3:] and more is False

    page, more = await store.before(owner_id=1, before=ids[4], limit=3)
    assert [e["id"] for e in page] == [ids[3], ids[2], ids[1]] and more is True


@pytest.mark.asyncio
async def test_a_registered_file_is_found_by_id_only_for_its_owner(store, tmp_path):
    path = tmp_path / "plot.png"
    path.write_bytes(b"\x89PNG....")
    ref = await store.register_file(owner_id=1, path=str(path), filename="plot.png",
                                    content_type="image/png")
    assert ref["id"].startswith("f_") and len(ref["id"]) == 18
    assert ref == {"id": ref["id"], "filename": "plot.png", "content_type": "image/png", "size": 8}

    found = await store.file(owner_id=1, file_id=ref["id"])
    assert found["path"] == str(path)
    assert await store.file(owner_id=2, file_id=ref["id"]) is None
    assert await store.file(owner_id=1, file_id="f_nothing") is None


@pytest.mark.asyncio
async def test_a_voice_file_carries_its_duration(store, tmp_path):
    path = tmp_path / "note.ogg"
    path.write_bytes(b"OggS")
    ref = await store.register_file(owner_id=1, path=str(path), filename="note.ogg",
                                    content_type="audio/ogg", duration_s=7.4)
    assert ref["duration_s"] == 7.4


@pytest.mark.asyncio
async def test_the_newest_id_is_the_owners_own_and_zero_when_empty(store):
    assert await store.latest_id(owner_id=1) == 0
    first = await store.append(owner_id=1, kind="message", payload={"text": "a"})
    second = await store.append(owner_id=1, kind="message", payload={"text": "b"})
    other = await store.append(owner_id=2, kind="message", payload={"text": "not his"})
    assert await store.latest_id(owner_id=1) == second["id"] > first["id"]
    assert await store.latest_id(owner_id=2) == other["id"]
