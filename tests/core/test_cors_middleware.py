from __future__ import annotations

import httpx
import pytest

import core.main as main_module


@pytest.mark.asyncio
async def test_create_app_insecure_cors_supports_credentialed_browser_requests(monkeypatch):
    monkeypatch.setattr(main_module.config, "ALLOW_INSECURE_CORS", True, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGINS", [], raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGIN_REGEX", None, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_URL", "http://localhost:3000", raising=False)

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_create_app_explicit_frontend_origin_allows_credentials(monkeypatch):
    monkeypatch.setattr(main_module.config, "ALLOW_INSECURE_CORS", False, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGINS", ["http://localhost:3000"], raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGIN_REGEX", None, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_URL", "", raising=False)

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_the_browser_she_tells_them_to_open_is_allowed_even_when_frontend_origins_names_another_port(monkeypatch):
    """The door is unreachable if CORS forbids the address she prints.

    The punch list, the honest setup sentence and SETUP.md all name FRONTEND_URL.
    A stale, non-empty FRONTEND_ORIGINS used to win silently: the permissive branch
    only fires when the list is empty or holds "*", so a stranger on the shipped
    template opened localhost:3000 and the page said "Her backend is not answering."
    """
    monkeypatch.setattr(main_module.config, "ALLOW_INSECURE_CORS", True, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGINS", ["http://localhost:3200"], raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGIN_REGEX", None, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_URL", "http://localhost:3000", raising=False)

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


@pytest.mark.asyncio
async def test_the_address_she_prints_is_allowed_with_insecure_cors_off_too(monkeypatch):
    """The same rule holds on the tightened path: her printed address is never forbidden."""
    monkeypatch.setattr(main_module.config, "ALLOW_INSECURE_CORS", False, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGINS", ["http://localhost:3200"], raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGIN_REGEX", None, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_URL", "http://localhost:3000", raising=False)

    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_an_explicit_origin_list_is_not_thrown_away_when_her_address_is_added(monkeypatch):
    """Adding her address must not drop what the operator listed."""
    monkeypatch.setattr(main_module.config, "ALLOW_INSECURE_CORS", False, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGINS", ["https://palli.example"], raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_ORIGIN_REGEX", None, raising=False)
    monkeypatch.setattr(main_module.config, "FRONTEND_URL", "http://localhost:3000", raising=False)

    origins, regex, credentials = main_module._resolve_cors_settings()

    assert "https://palli.example" in origins
    assert "http://localhost:3000" in origins
    assert credentials is True


def test_the_shipped_template_lets_a_stranger_reach_the_door():
    """.env.template must not point the browser at one port and CORS at another."""
    from pathlib import Path
    from urllib.parse import urlparse
    import re

    template = Path(__file__).resolve().parents[2] / ".env.template"
    text = template.read_text(encoding="utf-8")

    def value_of(key: str) -> str:
        match = re.search(rf"(?m)^{key}=(.*)$", text)
        assert match is not None, f"{key} is missing from .env.template"
        return match.group(1).strip()

    frontend_url = value_of("FRONTEND_URL")
    origins = [piece.strip() for piece in value_of("FRONTEND_ORIGINS").split(",") if piece.strip()]

    assert frontend_url, "FRONTEND_URL is the address the punch list prints; it cannot be blank"
    if origins:
        parsed = urlparse(frontend_url)
        printed_origin = f"{parsed.scheme}://{parsed.netloc}"
        assert printed_origin in origins, (
            "the shipped FRONTEND_ORIGINS forbids the address the door tells a stranger to open"
        )
