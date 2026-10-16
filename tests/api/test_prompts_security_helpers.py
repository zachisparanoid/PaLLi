from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api import prompts


def _dummy_request() -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_safe_prompt_path_rejects_traversal():
    with pytest.raises(HTTPException) as exc:
        prompts._safe_prompt_path("../secret")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc2:
        prompts._safe_prompt_path("a/b")
    assert exc2.value.status_code == 400


@pytest.mark.asyncio
async def test_require_admin_allows_config_admin_ids(monkeypatch):
    monkeypatch.setattr(prompts, "get_user_id_from_request", lambda _request: 999)
    monkeypatch.setattr(prompts.config, "ADMIN_USER_IDS", [999])
    monkeypatch.setattr(prompts, "local_auth_bypass_enabled", lambda: False)

    async def fake_get_user(_user_id: int):
        return {"id": 999, "role": "user"}

    monkeypatch.setattr(prompts.brain, "get_app_user_by_id", fake_get_user)
    user = await prompts._require_admin(_dummy_request())
    assert user["id"] == 999


@pytest.mark.asyncio
async def test_require_admin_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(prompts, "get_user_id_from_request", lambda _request: 111)
    monkeypatch.setattr(prompts.config, "ADMIN_USER_IDS", [])
    monkeypatch.setattr(prompts, "local_auth_bypass_enabled", lambda: False)

    async def fake_get_user(_user_id: int):
        return {"id": 111, "role": "user"}

    monkeypatch.setattr(prompts.brain, "get_app_user_by_id", fake_get_user)
    with pytest.raises(HTTPException) as exc:
        await prompts._require_admin(_dummy_request())
    assert exc.value.status_code == 403
