"""Tests for A8 — _require_user gating on api.chat.POST /message.

The HTTP chat endpoint is the dashboard's invocation site for the new
spine. Without auth, anyone hitting /api/chat/message could prompt
PaLLi (and trigger expensive LLM calls / tool execution). Decision 8
of the 2026-05-08 completion refactor closes that gap.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from api import chat as chat_mod
from api import web_auth


def _build_app():
    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api/chat")
    return app


@pytest.mark.asyncio
async def test_chat_message_returns_401_without_auth(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/chat/message",
            json={"message": "hello"},
        )
    assert resp.status_code == 401
