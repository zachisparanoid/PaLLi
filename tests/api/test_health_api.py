from __future__ import annotations

import httpx
import pytest

from core.main import app


@pytest.mark.asyncio
async def test_health_endpoint_returns_backend_startup_status(monkeypatch):
    monkeypatch.setattr("core.main.backend_bootstrap.health_status", lambda: "degraded")
    monkeypatch.setattr(
        "core.main.backend_bootstrap.snapshot",
        lambda: {
            "status": "degraded",
            "counts": {"degraded": 2, "failed": 0},
        },
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "version": "2.0.0",
        "startup_status": "degraded",
        "degraded_stages": 2,
        "failed_stages": 0,
    }


@pytest.mark.asyncio
async def test_health_detail_endpoint_returns_structured_payload(monkeypatch):
    monkeypatch.setattr(
        "core.main.build_public_health_detail",
        lambda: {
            "status": "healthy",
            "version": "2.0.0",
            "host": {"host_backend": "linux_x11"},
            "backend": {"url": "http://127.0.0.1:8000", "status": "ready"},
            "frontend": {"url": "http://127.0.0.1:3000", "status": "running"},
            "plugins": {"status": "ready", "counts": {"enabled": 3, "total": 4}},
            "surface_reliability": {"counts": {"fallback_events": 1, "recovery_attempts": 2}},
            "doctor": {"status": "ok", "summary": "Environment is ready."},
        },
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/health/detail")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["host"]["host_backend"] == "linux_x11"
    assert response.json()["backend"]["status"] == "ready"
    assert response.json()["plugins"]["counts"]["enabled"] == 3
    assert response.json()["surface_reliability"]["counts"]["fallback_events"] == 1


@pytest.mark.asyncio
async def test_health_detail_is_built_off_her_event_loop(monkeypatch):
    """Second read F7 (2026-10-04): the detail is synchronous — blocking HTTP
    and TCP probes, and A3's /proc walk (52-76 ms on the live box) — so the
    endpoint builds it in a worker thread, never on the loop that runs her."""
    import threading

    seen: dict[str, int] = {}

    def build():
        seen["thread"] = threading.get_ident()
        return {"status": "healthy"}

    monkeypatch.setattr("core.main.build_public_health_detail", build)
    loop_thread = threading.get_ident()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/health/detail")

    assert response.status_code == 200 and response.json() == {"status": "healthy"}
    assert seen["thread"] != loop_thread
