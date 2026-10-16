"""Paired phones and one-use pair codes (spec §3)."""
from __future__ import annotations

import pytest
import pytest_asyncio

from core.mobile_device_store import MobileDeviceStore, PairCodeRefused
from core.mobile_pairing import sha256_hex


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest_asyncio.fixture
async def db(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "mobile.sqlite3"))
    await brain.initialize()
    try:
        yield brain
    finally:
        await brain.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(db, clock):
    return MobileDeviceStore(db, now=clock)


@pytest.mark.asyncio
async def test_a_code_pairs_once_and_stores_only_hashes(store, db):
    expires = await store.create_pair_code(owner_id=1, code="K7QM2XPA")
    assert expires == 1_000_000.0 + 600
    device = await store.redeem(code="K7QM2XPA", name="Pixel 9", token="pmd_secret")
    assert device["owner_id"] == 1 and device["name"] == "Pixel 9"

    conn = await db.get_connection()
    try:
        stored = await conn.fetchval("SELECT token_hash FROM mobile_devices WHERE id = $1", device["id"])
        code_rows = await conn.fetch("SELECT code_hash FROM mobile_pair_codes")
    finally:
        await conn.close()
    assert stored == sha256_hex("pmd_secret")
    assert [r["code_hash"] for r in code_rows] == [sha256_hex("K7QM2XPA")]

    with pytest.raises(PairCodeRefused) as again:
        await store.redeem(code="K7QM2XPA", name="Other", token="pmd_other")
    assert again.value.status == 410


@pytest.mark.asyncio
async def test_an_unknown_code_is_401_and_an_expired_one_is_410(store, clock):
    with pytest.raises(PairCodeRefused) as unknown:
        await store.redeem(code="ZZZZZZZZ", name="x", token="pmd_a")
    assert unknown.value.status == 401

    await store.create_pair_code(owner_id=1, code="AAAA1111")
    clock.t += 601
    with pytest.raises(PairCodeRefused) as expired:
        await store.redeem(code="AAAA1111", name="x", token="pmd_b")
    assert expired.value.status == 410


@pytest.mark.asyncio
async def test_a_token_finds_its_device_until_it_is_revoked(store):
    await store.create_pair_code(owner_id=1, code="BBBB2222")
    device = await store.redeem(code="BBBB2222", name="Phone", token="pmd_t1")

    found = await store.device_for_token("pmd_t1")
    assert found is not None and found["id"] == device["id"]
    assert await store.device_for_token("pmd_nope") is None

    assert await store.revoke(owner_id=1, device_id=device["id"]) is True
    assert await store.device_for_token("pmd_t1") is None
    assert await store.revoke(owner_id=1, device_id=device["id"]) is False


@pytest.mark.asyncio
async def test_last_seen_is_written_at_most_once_a_minute(store, clock):
    await store.create_pair_code(owner_id=1, code="CCCC3333")
    await store.redeem(code="CCCC3333", name="Phone", token="pmd_t2")
    clock.t += 30
    assert (await store.device_for_token("pmd_t2"))["last_seen_at"] == 1_000_000.0
    clock.t += 31
    assert (await store.device_for_token("pmd_t2"))["last_seen_at"] == 1_000_061.0


@pytest.mark.asyncio
async def test_listing_counting_and_acks(store):
    for i, code in enumerate(("DDDD0001", "DDDD0002")):
        await store.create_pair_code(owner_id=1, code=code)
        await store.redeem(code=code, name=f"Phone {i}", token=f"pmd_{i}")
    devices = await store.list_devices(owner_id=1)
    assert [d["name"] for d in devices] == ["Phone 0", "Phone 1"]
    assert await store.count_devices() == 2
    assert await store.count_devices(owner_id=99) == 0

    await store.set_acked_through(device_id=devices[0]["id"], through=7)
    await store.set_acked_through(device_id=devices[0]["id"], through=3)   # never backwards
    again = await store.list_devices(owner_id=1)
    assert again[0]["acked_through"] == 7

    await store.revoke(owner_id=1, device_id=devices[1]["id"])
    assert await store.count_devices() == 1
