"""/api/mobile/events and /api/mobile/files (spec §5)."""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from api import mobile, web_auth
from core import web_security
from core.config import config
from core.mobile_device_store import MobileDeviceStore
from core.mobile_events_store import MobileEventsStore


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "routes.sqlite3"))
    await brain.initialize()
    monkeypatch.setattr("core.database.brain", brain)

    async def owner():
        return {"id": 1}

    monkeypatch.setattr(web_auth, "ensure_local_owner", owner)
    monkeypatch.setattr(web_auth, "local_mode_enabled", lambda: True)
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: True)
    monkeypatch.setattr(web_security, "local_auth_bypass_enabled", lambda: True)
    monkeypatch.setattr(config, "CSRF_PROTECT_LOCAL", True, raising=False)
    try:
        yield brain
    finally:
        await brain.close()


async def _token(db, *, owner_id: int = 1, code: str = "EEEE0001") -> str:
    store = MobileDeviceStore(db)
    await store.create_pair_code(owner_id=owner_id, code=code)
    token = f"pmd_{code}"
    await store.redeem(code=code, name="Phone", token=token)
    return token


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/mobile")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_events_page_after_and_before_and_only_the_owners(db):
    token = await _token(db)
    events = MobileEventsStore(db)
    ids = [(await events.append(owner_id=1, kind="message", payload={"text": str(i)}))["id"] for i in range(3)]
    await events.append(owner_id=2, kind="message", payload={"text": "not yours"})
    async with _client() as http:
        after = (await http.get("/api/mobile/events?after=0", headers=_bearer(token))).json()
        assert [e["id"] for e in after["events"]] == ids and after["more"] is False
        before = (await http.get(f"/api/mobile/events?before={ids[2]}&limit=1", headers=_bearer(token))).json()
        assert [e["id"] for e in before["events"]] == [ids[1]] and before["more"] is True
        assert (await http.get("/api/mobile/events", headers=_bearer(token))).status_code == 422
        assert (await http.get("/api/mobile/events?after=0&before=5", headers=_bearer(token))).status_code == 422
        assert (await http.get("/api/mobile/events?after=0")).status_code == 401


@pytest.mark.asyncio
async def test_a_file_downloads_by_id_for_its_owner_only(db, tmp_path):
    mine = await _token(db)
    theirs = await _token(db, owner_id=2, code="EEEE0002")
    path = tmp_path / "plot.png"
    path.write_bytes(b"\x89PNG-bytes")
    ref = await MobileEventsStore(db).register_file(owner_id=1, path=str(path), filename="plot.png",
                                                    content_type="image/png")
    async with _client() as http:
        got = await http.get(f"/api/mobile/files/{ref['id']}", headers=_bearer(mine))
        assert got.status_code == 200 and got.content == b"\x89PNG-bytes"
        assert got.headers["content-type"] == "image/png"
        assert (await http.get(f"/api/mobile/files/{ref['id']}", headers=_bearer(theirs))).status_code == 404
        path.unlink()
        assert (await http.get(f"/api/mobile/files/{ref['id']}", headers=_bearer(mine))).status_code == 404


@pytest.mark.asyncio
async def test_revoking_a_phone_closes_its_open_sockets(db, monkeypatch):
    token = await _token(db)
    from bots.mobile import hub as hub_mod

    closed: list[int] = []
    monkeypatch.setattr(hub_mod.hub, "revoke", lambda device_id: closed.append(device_id) or 1)
    device = await MobileDeviceStore(db).device_for_token(token)
    async with _client() as http:
        assert (await http.delete(f"/api/mobile/devices/{device['id']}", headers=_bearer(token))).status_code == 200
    assert closed == [device["id"]]


@pytest.mark.asyncio
async def test_an_id_past_what_the_store_holds_is_422_never_500(db):
    """Review M8 (ledger T2 L54): a value past 2^63 never reaches sqlite as
    an OverflowError; it is refused as 422, as the reminders routes do."""
    token = await _token(db)
    async with _client() as http:
        too_big = 2**64
        assert (await http.delete(f"/api/mobile/devices/{too_big}", headers=_bearer(token))).status_code == 422
        assert (await http.delete("/api/mobile/devices/0", headers=_bearer(token))).status_code == 422
        assert (await http.get(f"/api/mobile/events?after={too_big}", headers=_bearer(token))).status_code == 422
        assert (await http.get(f"/api/mobile/events?before={too_big}", headers=_bearer(token))).status_code == 422
        assert (await http.get("/api/mobile/events?after=-1", headers=_bearer(token))).status_code == 422
        assert (await http.get(f"/api/mobile/events?after={2**63 - 1}",
                               headers=_bearer(token))).json() == {"events": [], "more": False}


@pytest.mark.asyncio
async def test_a_device_name_is_logged_as_one_line(db, caplog):
    """Review M8: a name with a newline cannot write a second log line."""
    store = MobileDeviceStore(db)
    await store.create_pair_code(owner_id=1, code="EEEE0003")
    with caplog.at_level("INFO", logger="api.mobile"):
        async with _client() as http:
            paired = await http.post("/api/mobile/pair", json={
                "code": "EEEE0003", "device_name": "Pixel\nmobile door: revoked device 1"})
    assert paired.status_code == 200
    lines = [r.getMessage() for r in caplog.records if "paired device" in r.getMessage()]
    assert len(lines) == 1 and "\n" not in lines[0]
    assert "'Pixel\\nmobile door: revoked device 1'" in lines[0]
