"""The reminders panel (spec §5): open reminders soonest first; snooze and
cancel through the same store calls her own tools make."""
from __future__ import annotations

import time

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from api import mobile, mobile_reminders
from api.mobile_reminders import REMINDER_PLATFORMS
from core.doors import CHAT_DOORS, WEB_DOOR
from core.mobile_device_store import MobileDeviceStore

AUTH = {"Authorization": "Bearer pmd_reminders"}


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "reminders.sqlite3"))
    await brain.initialize()
    monkeypatch.setattr("core.database.brain", brain)
    devices = MobileDeviceStore(brain)
    await devices.create_pair_code(owner_id=1, code="RRRR0001")
    await devices.redeem(code="RRRR0001", name="Phone", token="pmd_reminders")
    try:
        yield brain
    finally:
        await brain.close()


async def _reminder(brain, *, user_id: int, platform: str, text: str, due: float) -> int:
    conn = await brain.get_connection()
    try:
        return int(await conn.fetchval(
            "INSERT INTO scheduled_reminders (user_id, platform, channel_id, surface_id, reminder_text, "
            "scheduled_time, created_time, is_completed, kind) VALUES ($1, $2, 0, '0', $3, $4, $5, FALSE, "
            "'reminder') RETURNING id",
            user_id, platform, text, due, due - 3600))
    finally:
        await conn.close()


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/mobile")
    app.include_router(mobile_reminders.router, prefix="/api/mobile")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
async def test_open_reminders_soonest_first_across_doors_and_only_his(db):
    later = await _reminder(db, user_id=1, platform="telegram", text="call the co-op", due=5000.0)
    sooner = await _reminder(db, user_id=1, platform="mobile", text="stretch", due=4000.0)
    dashboard = await _reminder(db, user_id=1, platform="dashboard", text="water the plants", due=4500.0)
    discord = await _reminder(db, user_id=1, platform="discord", text="reply to Ana", due=4800.0)
    voice = await _reminder(db, user_id=1, platform="voice", text="ring the vet", due=3500.0)
    await _reminder(db, user_id=2, platform="mobile", text="not his", due=3000.0)
    async with _client() as http:
        body = (await http.get("/api/mobile/reminders", headers=AUTH)).json()
    assert [(r["id"], r["text"], r["due_at"]) for r in body["reminders"]] == [
        (voice, "ring the vet", 3500.0), (sooner, "stretch", 4000.0),
        (dashboard, "water the plants", 4500.0), (discord, "reply to Ana", 4800.0),
        (later, "call the co-op", 5000.0)]
    assert [r["platform"] for r in body["reminders"]] == ["voice", "mobile", "dashboard", "discord", "telegram"]
    assert all(r["kind"] == "reminder" for r in body["reminders"])


@pytest.mark.asyncio
async def test_snooze_moves_it_from_now_and_refuses_odd_minutes(db):
    rid = await _reminder(db, user_id=1, platform="mobile", text="stretch", due=4000.0)
    async with _client() as http:
        before = time.time()
        ok = await http.post(f"/api/mobile/reminders/{rid}/snooze", json={"minutes": 15}, headers=AUTH)
        after = time.time()
        assert (await http.post(f"/api/mobile/reminders/{rid}/snooze", json={"minutes": 0}, headers=AUTH)).status_code == 422
        assert (await http.post(f"/api/mobile/reminders/{rid}/snooze", json={"minutes": 1441}, headers=AUTH)).status_code == 422
    assert ok.status_code == 200
    assert before + 900 <= ok.json()["due_at"] <= after + 900
    record = await db.get_scheduled_reminder_record(rid)
    assert record["scheduled_time"] == ok.json()["due_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes", [True, False, "15", 15.0, 1e2])
async def test_snooze_minutes_must_be_a_whole_number_not_a_bool_string_or_float(db, minutes):
    rid = await _reminder(db, user_id=1, platform="mobile", text="stretch", due=4000.0)
    async with _client() as http:
        bad = await http.post(f"/api/mobile/reminders/{rid}/snooze", json={"minutes": minutes}, headers=AUTH)
        assert bad.status_code == 422
        assert (await db.get_scheduled_reminder_record(rid))["scheduled_time"] == 4000.0
        # The same route takes a plain whole number, so the 422s above are the field's doing.
        good = await http.post(f"/api/mobile/reminders/{rid}/snooze", json={"minutes": 15}, headers=AUTH)
        assert good.status_code == 200


@pytest.mark.asyncio
async def test_snooze_refuses_a_done_one_a_strangers_and_a_missing_one(db):
    done = await _reminder(db, user_id=1, platform="mobile", text="done already", due=4000.0)
    theirs = await _reminder(db, user_id=2, platform="mobile", text="not his", due=4000.0)
    async with _client() as http:
        assert (await http.post(f"/api/mobile/reminders/{done}/cancel", headers=AUTH)).json() == {"ok": True}
        for rid in (done, theirs, 987654):
            refused = await http.post(f"/api/mobile/reminders/{rid}/snooze", json={"minutes": 5}, headers=AUTH)
            assert refused.status_code == 404
            assert refused.json() == {"detail": "That reminder isn't yours, or doesn't exist."}
    for rid in (done, theirs):
        record = await db.get_scheduled_reminder_record(rid)
        assert record["scheduled_time"] == 4000.0


@pytest.mark.asyncio
@pytest.mark.parametrize("big", [0, 2**63, 2**64])
async def test_an_out_of_range_id_is_422_not_a_500(db, big):
    async with _client() as http:
        assert (await http.post(f"/api/mobile/reminders/{big}/snooze", json={"minutes": 5},
                                headers=AUTH)).status_code == 422
        assert (await http.post(f"/api/mobile/reminders/{big}/cancel", headers=AUTH)).status_code == 422


@pytest.mark.asyncio
async def test_cancel_takes_it_off_the_list_and_someone_elses_is_404(db):
    mine = await _reminder(db, user_id=1, platform="mobile", text="stretch", due=4000.0)
    theirs = await _reminder(db, user_id=2, platform="mobile", text="not his", due=4000.0)
    async with _client() as http:
        assert (await http.post(f"/api/mobile/reminders/{mine}/cancel", headers=AUTH)).json() == {"ok": True}
        assert (await http.get("/api/mobile/reminders", headers=AUTH)).json()["reminders"] == []
        assert (await http.post(f"/api/mobile/reminders/{theirs}/cancel", headers=AUTH)).status_code == 404
        assert (await http.post(f"/api/mobile/reminders/{theirs}/snooze", json={"minutes": 5},
                                headers=AUTH)).status_code == 404
        assert (await http.get("/api/mobile/reminders")).status_code == 401
        assert (await http.post(f"/api/mobile/reminders/{mine}/snooze", json={"minutes": 5})).status_code == 401


def test_the_panel_files_reminders_under_every_chat_door_and_the_web_page():
    assert set(CHAT_DOORS) | {WEB_DOOR} <= set(REMINDER_PLATFORMS)
