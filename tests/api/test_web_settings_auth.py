"""Tests for A8 — _require_user gating on api/web_settings.py routes.

Without auth these endpoints expose the full settings snapshot and
allow anonymous changes (propose / apply / rollback). Decision 8 of
the 2026-05-08 completion refactor closes that gap.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from api import web_settings
from api import web_auth


def _build_app():
    app = FastAPI()
    app.include_router(web_settings.router)
    return app


@pytest.mark.asyncio
async def test_get_snapshot_returns_401_without_auth(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/web/settings/snapshot")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_providers_returns_401_without_auth(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/web/settings/providers")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_propose_change_returns_401_without_auth(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/web/settings/propose",
            json={"changes": {}, "reason": ""},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_apply_change_returns_401_without_auth(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/web/settings/apply",
            json={"change_id": "x"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rollback_change_returns_401_without_auth(monkeypatch):
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/web/settings/rollback",
            json={"rollback_token": "x"},
        )
    assert resp.status_code == 401
