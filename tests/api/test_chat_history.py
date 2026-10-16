"""GET /api/chat/history (web-page reminders, the owner 2026-10-10).

The web page showed only replies to what was just typed, and started blank on
every visit. It now loads the recent web conversation when it opens, and
every 15 seconds asks for reminders that arrived on their own
(``reminders_only``) after the newest message it has (``since_id``). A
reminder is a row the reminder delivery wrote: metadata type
"deferred_action". Same gate as POST /message: an anonymous caller gets 401.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from api import chat as chat_mod
from api import web_auth

ROWS = [
    {"id": 10, "role": "user", "content": "remind me in 2 minutes to call the dentist", "timestamp": 100.0,
     "metadata": {}},
    {"id": 11, "role": "assistant", "content": "Set for 9:02.", "timestamp": 101.0, "metadata": {}},
    {"id": 12, "role": "system", "content": "(a reply the door could not send)", "timestamp": 102.0,
     "metadata": {}},
    {"id": 13, "role": "assistant", "content": "Time to call the dentist.", "timestamp": 220.0,
     "metadata": {"type": "deferred_action", "reminder_id": 71}},
    {"id": 14, "role": "assistant", "content": "   ", "timestamp": 221.0, "metadata": {}},
]


def _build_app():
    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api/chat")
    return app


@pytest.fixture
def signed_in(monkeypatch):
    from core.database import brain

    asked: list[dict] = []

    async def require_user(request):
        return {"id": 1}

    async def owner_id():
        return 1

    async def history(**kw):
        asked.append(kw)
        return [dict(r) for r in ROWS]

    monkeypatch.setattr(chat_mod, "_require_user", require_user)
    monkeypatch.setattr(chat_mod, "_resolve_local_owner_id", owner_id)
    monkeypatch.setattr(brain, "get_conversation_history", history)
    return asked


async def _get(path: str):
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_an_anonymous_caller_gets_401(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    resp = await _get("/api/chat/history")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_the_page_gets_the_owners_web_conversation_oldest_first(signed_in):
    resp = await _get("/api/chat/history")
    assert resp.status_code == 200
    got = resp.json()["messages"]
    # the system note and the blank row are never shown as a message
    assert [m["id"] for m in got] == [10, 11, 13]
    assert got[0] == {"id": 10, "role": "user", "text": "remind me in 2 minutes to call the dentist",
                      "timestamp": 100.0, "reminder": False}
    assert got[2]["reminder"] is True
    q = signed_in[0]
    assert (q["user_id"], q["platform"], q["channel_id"], q["include_metadata"]) == (1, "dashboard", 0, True)


@pytest.mark.asyncio
async def test_the_15_second_check_gets_only_new_reminders(signed_in):
    resp = await _get("/api/chat/history?since_id=11&reminders_only=true")
    assert [m["id"] for m in resp.json()["messages"]] == [13]
    resp = await _get("/api/chat/history?since_id=13&reminders_only=true")
    assert resp.json()["messages"] == []


@pytest.mark.asyncio
async def test_the_limit_is_bounded(signed_in):
    await _get("/api/chat/history?limit=100000")
    await _get("/api/chat/history?limit=0")
    assert [q["limit"] for q in signed_in] == [200, 1]


@pytest.mark.asyncio
async def test_no_local_owner_is_a_503_not_a_crash(monkeypatch):
    async def require_user(request):
        return {"id": 1}

    async def no_owner():
        raise RuntimeError("local owner could not be resolved")

    monkeypatch.setattr(chat_mod, "_require_user", require_user)
    monkeypatch.setattr(chat_mod, "_resolve_local_owner_id", no_owner)
    resp = await _get("/api/chat/history")
    assert resp.status_code == 503
