from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from api import prompts, web_account, web_auth
from core.config import config
from core import web_security


CSRF_TOKEN = "test-csrf-token"


def _csrf_headers() -> dict[str, str]:
    return {"x-csrf-token": CSRF_TOKEN}


def _set_csrf_cookie(client) -> None:
    client.cookies.set(config.CSRF_COOKIE_NAME, CSRF_TOKEN)


@pytest.mark.asyncio
async def test_prompts_requires_admin(monkeypatch, tmp_path: Path):
    app = FastAPI()
    app.include_router(prompts.router, prefix="/api/prompts")

    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(prompts, "get_user_id_from_request", lambda request: 111)
    monkeypatch.setattr(prompts, "local_auth_bypass_enabled", lambda: False)
    monkeypatch.setattr(web_security, "local_auth_bypass_enabled", lambda: False)

    async def fake_get_user(user_id: int):
        return {"id": user_id, "role": "user"}

    monkeypatch.setattr(prompts.brain, "get_app_user_by_id", fake_get_user)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/prompts")

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


@pytest.mark.asyncio
async def test_prompts_admin_write_requires_csrf(monkeypatch, tmp_path: Path):
    app = FastAPI()
    app.include_router(prompts.router, prefix="/api/prompts")

    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    monkeypatch.setattr(prompts, "get_user_id_from_request", lambda request: 222)
    monkeypatch.setattr(prompts, "local_auth_bypass_enabled", lambda: False)
    monkeypatch.setattr(web_security, "local_auth_bypass_enabled", lambda: False)

    async def fake_get_user(user_id: int):
        return {"id": user_id, "role": "admin"}

    monkeypatch.setattr(prompts.brain, "get_app_user_by_id", fake_get_user)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        _set_csrf_cookie(client)

        no_csrf = await client.put("/api/prompts/local_prompt", json={"content": "hello"})
        assert no_csrf.status_code == 403

        ok = await client.put(
            "/api/prompts/local_prompt",
            json={"content": "hello"},
            headers=_csrf_headers(),
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "ok"
        assert (tmp_path / "local_prompt.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_auth_me_returns_local_owner_and_local_auth_surface(monkeypatch):
    app = FastAPI()
    app.include_router(web_auth.router, prefix="/api/web")

    async def fake_owner():
        return {
            "id": 7,
            "email": "local@palli.local",
            "has_access": True,
            "email_verified": True,
            "discord_user_id": None,
            "role": "admin",
        }

    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: True)
    monkeypatch.setattr(web_auth, "ensure_local_owner", fake_owner)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/auth/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"]["email"] == "local@palli.local"
    assert payload["user"]["role"] == "admin"
    assert "has_access" not in payload["user"]
    assert "beta_joined_at" not in payload["user"]
    assert payload["auth_surface"] == {
        "dashboard_auth_mode": "open",
        "cloud_auth_routes_enabled": False,
        "account_security_routes_enabled": False,
    }


@pytest.mark.asyncio
async def test_auth_me_reports_session_auth_surface_without_authenticated_user(monkeypatch):
    app = FastAPI()
    app.include_router(web_auth.router, prefix="/api/web")

    monkeypatch.setattr(config, "PALLI_DEPLOYMENT_MODE", "cloud")
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    monkeypatch.setattr(web_auth, "get_user_id_from_request", lambda request: None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/auth/me")

    assert response.status_code == 200
    assert response.json() == {
        "user": None,
        "auth_surface": {
            "dashboard_auth_mode": "session",
            "cloud_auth_routes_enabled": True,
            "account_security_routes_enabled": True,
        },
        # First door, task 4 (2026-09-17): /auth/me gained `local_mode`, so the
        # exhaustive shape gained it too. PALLI_DEPLOYMENT_MODE is pinned above
        # because this assertion now depends on it, and python-dotenv would
        # otherwise fill it from the owner's real .env.local.
        "local_mode": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deployment_mode", "expected_local_mode"),
    [("local", True), ("local_single_user", True), ("cloud", False)],
)
async def test_auth_me_tells_the_page_whether_this_is_a_self_hosted_install(
    monkeypatch, deployment_mode: str, expected_local_mode: bool
):
    """First door, task 4 (2026-09-17): the dashboard hides the SaaS-era account
    pages on a self-hosted install, and `/auth/me` is where the page learns it is
    one. The answer comes from `core.local_mode.local_mode_enabled()` — the same
    function `GET /api/setup/status` answers from — so the two surfaces can never
    disagree about the same install.

    The session shape is used on purpose: LOCAL_DASHBOARD_NO_AUTH can be off on a
    perfectly local install, so `local_mode` must not be read off the auth bypass.
    """
    app = FastAPI()
    app.include_router(web_auth.router, prefix="/api/web")

    monkeypatch.setattr(config, "PALLI_DEPLOYMENT_MODE", deployment_mode)
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: False)
    monkeypatch.setattr(web_auth, "get_user_id_from_request", lambda request: None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/auth/me")

    assert response.status_code == 200
    assert response.json()["local_mode"] is expected_local_mode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deployment_mode", "expected_local_mode"),
    [("local", True), ("cloud", False)],
)
async def test_auth_me_reports_local_mode_in_the_bypass_shape_too(
    monkeypatch, deployment_mode: str, expected_local_mode: bool
):
    """The same flag on the other return shape. The dashboard reads one field
    whichever way the owner got in, so both shapes have to carry it."""
    app = FastAPI()
    app.include_router(web_auth.router, prefix="/api/web")

    async def fake_owner():
        return {"id": 7, "email": "local@palli.local", "role": "admin"}

    monkeypatch.setattr(config, "PALLI_DEPLOYMENT_MODE", deployment_mode)
    monkeypatch.setattr(web_auth, "local_auth_bypass_enabled", lambda: True)
    monkeypatch.setattr(web_auth, "ensure_local_owner", fake_owner)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/web/auth/me")

    assert response.status_code == 200
    assert response.json()["local_mode"] is expected_local_mode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "request_kwargs"),
    [
        (
            "post",
            "/api/web/auth/register",
            {"json": {"email": "local@example.com", "password": "StrongPass123!"}},
        ),
        (
            "post",
            "/api/web/auth/login",
            {"json": {"email": "local@example.com", "password": "StrongPass123!"}},
        ),
        (
            "post",
            "/api/web/auth/password-reset/request",
            {"json": {"email": "local@example.com"}},
        ),
        (
            "post",
            "/api/web/auth/onboarding/complete",
            {
                "json": {
                    "display_name": "Local Owner",
                    "age_confirmed": True,
                    "accept_terms": True,
                    "accept_privacy": True,
                }
            },
        ),
        ("get", "/api/web/auth/google/start", {}),
    ],
)
async def test_cloud_auth_routes_do_not_exist(method, path, request_kwargs):
    """Great Excision stage 5 (2026-07-09): the cloud-auth endpoints were
    DELETED, not just gated — the previous local-mode-404 guarantee is now
    unconditional. This test pins that the routes stay gone."""
    app = FastAPI()
    app.include_router(web_auth.router, prefix="/api/web")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await getattr(client, method)(path, **request_kwargs)

    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "request_kwargs"),
    [
        (
            "/api/web/account/security/password",
            {"json": {"current_password": "old", "new_password": "NewPass123!"}},
        ),
        ("/api/web/account/security/mfa/setup", {}),
        (
            "/api/web/account/security/mfa/verify",
            {"json": {"code": "123456", "secret": "BASE32SECRET"}},
        ),
    ],
)
async def test_account_security_routes_do_not_exist(path, request_kwargs):
    """Great Excision stage 5 (2026-07-09): MFA/password endpoints deleted
    outright; the routes must stay gone in every mode."""
    app = FastAPI()
    app.include_router(web_account.router, prefix="/api/web")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(path, **request_kwargs)

    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
