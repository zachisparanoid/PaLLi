"""Who may use the mobile door, and how a device token meets the rest of the
backend's auth (spec §3, rules 1–4)."""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request

from api import mobile, web_auth
from core import web_security
from core.config import config

CSRF = "csrf-for-this-test"


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "auth.sqlite3"))
    await brain.initialize()
    monkeypatch.setattr("core.database.brain", brain)
    try:
        yield brain
    finally:
        await brain.close()


@pytest.fixture(autouse=True)
def local_owner(monkeypatch):
    async def owner():
        return {"id": 1, "email": "local@palli.local"}

    monkeypatch.setattr(web_auth, "ensure_local_owner", owner)
    monkeypatch.setattr(web_auth, "local_mode_enabled", lambda: True)
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: True)
    monkeypatch.setattr(web_security, "local_auth_bypass_enabled", lambda: True)
    monkeypatch.setattr(config, "CSRF_PROTECT_LOCAL", True, raising=False)
    monkeypatch.setattr(config, "CSRF_COOKIE_NAME", "palli_csrf_test", raising=False)
    monkeypatch.setattr(config, "MOBILE_PUBLIC_URL", "http://palli-box.example.ts.net:8000", raising=False)
    monkeypatch.setattr(config, "effective_mobile_enabled", True, raising=False)
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    from core.rate_limit import limiter

    limiter.reset()


def _csrf() -> dict:
    return {"X-CSRF-Token": CSRF, "Cookie": f"palli_csrf_test={CSRF}"}


async def _probe(request: Request):
    user = await web_auth._require_user(request)
    if request.method == "POST":
        web_security.require_csrf(request)
    return {"id": user["id"]}


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(mobile.router, prefix="/api/mobile")
    app.add_api_route("/probe", _probe, methods=["GET", "POST"])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _paired(http: httpx.AsyncClient, name: str = "Pixel 9") -> str:
    start = await http.post("/api/mobile/pair/start", headers=_csrf())
    assert start.status_code == 200, start.text
    code = start.json()["code"].lower()            # typed loosely on purpose
    paired = await http.post("/api/mobile/pair", json={"code": code, "device_name": name})
    assert paired.status_code == 200, paired.text
    return paired.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_mobile_routes_refuse_a_request_with_no_token_even_in_bypass_mode(db):
    async with _client() as http:
        assert (await http.get("/api/mobile/me")).status_code == 401
        assert (await http.get("/api/mobile/me", headers=_bearer(""))).status_code == 401
        assert (await http.get("/api/mobile/me", headers=_bearer("pmd_nope"))).status_code == 401


@pytest.mark.asyncio
async def test_pairing_end_to_end(db):
    async with _client() as http:
        start = (await http.post("/api/mobile/pair/start", headers=_csrf())).json()
        assert len(start["code"]) == 9 and start["code"][4] == "-"
        assert start["url"] == "http://palli-box.example.ts.net:8000"
        assert start["link"].startswith("palli://pair?u=")
        assert "<svg" in start["qr_svg"]

        paired = await http.post("/api/mobile/pair",
                                 json={"code": start["code"], "device_name": " Pixel 9 "})
        body = paired.json()
        assert body["token"].startswith("pmd_") and body["api_version"] == 1

        me = (await http.get("/api/mobile/me", headers=_bearer(body["token"]))).json()
        assert me["device"] == {"id": body["device_id"], "name": "Pixel 9"}
        assert me["door_state"] == "open" and me["api_version"] == 1


@pytest.mark.asyncio
async def test_without_a_public_url_the_code_comes_alone(db, monkeypatch):
    monkeypatch.setattr(config, "MOBILE_PUBLIC_URL", "", raising=False)
    async with _client() as http:
        start = (await http.post("/api/mobile/pair/start", headers=_csrf())).json()
    assert start["url"] is None and start["link"] is None and "qr_svg" not in start


@pytest.mark.asyncio
async def test_pair_start_needs_the_csrf_pair(db):
    async with _client() as http:
        assert (await http.post("/api/mobile/pair/start")).status_code == 403


@pytest.mark.asyncio
async def test_a_wrong_code_is_401_a_used_one_410_and_the_sixth_try_429(db):
    async with _client() as http:
        start = (await http.post("/api/mobile/pair/start", headers=_csrf())).json()
        ok = await http.post("/api/mobile/pair", json={"code": start["code"], "device_name": "A"})
        assert ok.status_code == 200
        again = await http.post("/api/mobile/pair", json={"code": start["code"], "device_name": "B"})
        assert again.status_code == 410
        wrong = await http.post("/api/mobile/pair", json={"code": "ZZZZ-ZZZZ", "device_name": "C"})
        assert wrong.status_code == 401
        codes = [(await http.post("/api/mobile/pair",
                                  json={"code": "ZZZZ-ZZZZ", "device_name": "D"})).status_code
                 for _ in range(3)]
        assert codes[-1] == 429


@pytest.mark.asyncio
async def test_a_blank_device_name_is_422(db):
    async with _client() as http:
        start = (await http.post("/api/mobile/pair/start", headers=_csrf())).json()
        bad = await http.post("/api/mobile/pair", json={"code": start["code"], "device_name": ""})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_a_bad_bearer_on_a_web_route_is_401_never_the_no_login_owner(db):
    async with _client() as http:
        assert (await http.get("/probe")).json() == {"id": 1}          # bypass, no token
        assert (await http.get("/probe", headers=_bearer("pmd_wrong"))).status_code == 401


@pytest.mark.asyncio
async def test_a_good_bearer_is_the_owner_and_skips_csrf_while_a_cookie_still_needs_it(db):
    async with _client() as http:
        token = await _paired(http)
        assert (await http.post("/probe", headers=_bearer(token))).json() == {"id": 1}
        assert (await http.post("/probe")).status_code == 403
        assert (await http.post("/probe", headers=_csrf())).status_code == 200


@pytest.mark.asyncio
async def test_devices_list_marks_this_phone_and_a_phone_can_unpair_itself(db):
    async with _client() as http:
        first = await _paired(http, "Phone A")
        second = await _paired(http, "Phone B")
        listed = (await http.get("/api/mobile/devices", headers=_bearer(first))).json()["devices"]
        assert [(d["name"], d["this_device"]) for d in listed] == [("Phone A", True), ("Phone B", False)]

        mine = listed[0]["id"]
        assert (await http.delete(f"/api/mobile/devices/{mine}", headers=_bearer(first))).status_code == 200
        assert (await http.get("/api/mobile/me", headers=_bearer(first))).status_code == 401
        assert (await http.get("/api/mobile/me", headers=_bearer(second))).status_code == 200


@pytest.mark.asyncio
async def test_the_owner_revokes_from_the_door_page_with_csrf(db):
    async with _client() as http:
        token = await _paired(http)
        listed = (await http.get("/api/mobile/devices")).json()["devices"]     # bypass owner
        assert listed[0]["this_device"] is False
        device_id = listed[0]["id"]
        assert (await http.delete(f"/api/mobile/devices/{device_id}")).status_code == 403
        assert (await http.delete(f"/api/mobile/devices/{device_id}", headers=_csrf())).status_code == 200
        assert (await http.get("/api/mobile/me", headers=_bearer(token))).status_code == 401
        assert (await http.delete(f"/api/mobile/devices/{device_id}", headers=_csrf())).status_code == 404


@pytest.mark.asyncio
async def test_door_state_reads_the_brain_then_the_door(db, monkeypatch):
    async with _client() as http:
        token = await _paired(http)
        monkeypatch.setattr(config, "effective_mobile_enabled", False, raising=False)
        assert (await http.get("/api/mobile/me", headers=_bearer(token))).json()["door_state"] == "closed"
        monkeypatch.setattr(config, "brain_ready", False, raising=False)
        assert (await http.get("/api/mobile/me", headers=_bearer(token))).json()["door_state"] == "no_brain"
