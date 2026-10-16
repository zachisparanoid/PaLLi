from __future__ import annotations

import httpx
import pytest

import core.main as main_module
from core.request_traffic import APIRequestTrafficService, api_request_traffic_service


def test_api_request_traffic_service_summarizes_recent_requests():
    service = APIRequestTrafficService()

    service.record_request_started()
    service.record_request_finished(
        method="GET",
        route="/api/health",
        path="/api/health",
        status_code=200,
        duration_seconds=0.05,
    )
    service.record_request_started()
    service.record_request_finished(
        method="POST",
        route="/api/web/runtime/plugins/reload",
        path="/api/web/runtime/plugins/reload",
        status_code=503,
        duration_seconds=0.12,
    )

    snapshot = service.get_snapshot(route_limit=4, recent_limit=4)

    assert snapshot["status"] == "degraded"
    assert snapshot["in_flight"] == 0
    assert snapshot["totals"]["requests"] == 2
    assert snapshot["totals"]["status_counts"]["2xx"] == 1
    assert snapshot["totals"]["status_counts"]["5xx"] == 1
    assert snapshot["totals"]["method_counts"]["GET"] == 1
    assert snapshot["totals"]["method_counts"]["POST"] == 1
    assert snapshot["windows"]["last_60_seconds"]["count"] == 2
    assert snapshot["windows"]["last_5_minutes"]["server_error_count"] == 1
    assert any(item["route"] == "/api/health" for item in snapshot["top_routes"])
    assert snapshot["recent_requests"][0]["status_code"] in {200, 503}


@pytest.mark.asyncio
async def test_create_app_records_api_request_metrics(monkeypatch):
    monkeypatch.setattr(main_module.plugin_runtime, "prepare_startup_catalog", lambda *, force=False: [])
    monkeypatch.setattr(main_module.plugin_runtime, "get_startup_router_contributions", lambda: [])
    monkeypatch.setattr(main_module.backend_bootstrap, "health_status", lambda: "ready")
    monkeypatch.setattr(
        main_module.backend_bootstrap,
        "snapshot",
        lambda: {"status": "ready", "counts": {"degraded": 0, "failed": 0}},
    )

    api_request_traffic_service.reset_for_tests()
    app = main_module.create_app()
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await client.get("/api/health")

        assert response.status_code == 200

        snapshot = api_request_traffic_service.get_snapshot(route_limit=4, recent_limit=4)
        assert snapshot["totals"]["requests"] >= 1
        assert snapshot["windows"]["last_60_seconds"]["count"] >= 1
        assert any(
            item["route"] == "/api/health" and item["status_code"] == 200
            for item in snapshot["recent_requests"]
        )
    finally:
        api_request_traffic_service.reset_for_tests()
