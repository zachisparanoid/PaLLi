from __future__ import annotations

import pytest

import core.main as main_module


async def _run_startup_handlers(app) -> None:
    for handler in app.router.on_startup:
        await handler()


async def _run_shutdown_handlers(app) -> None:
    for handler in app.router.on_shutdown:
        await handler()


def test_create_app_refreshes_plugin_startup_catalog_before_loading_router_contributions(monkeypatch):
    calls: list[str] = []

    def _fake_prepare_startup_catalog(*, force: bool = False):
        calls.append(f"prepare:{force}")
        return []

    def _fake_get_startup_router_contributions():
        calls.append("routers")
        return []

    monkeypatch.setattr(
        main_module.plugin_runtime,
        "prepare_startup_catalog",
        _fake_prepare_startup_catalog,
    )
    monkeypatch.setattr(
        main_module.plugin_runtime,
        "get_startup_router_contributions",
        _fake_get_startup_router_contributions,
    )

    main_module.create_app()

    assert calls == ["prepare:True", "routers"]


@pytest.mark.asyncio
async def test_startup_event_runs_shared_preflight_before_backend_bootstrap(monkeypatch):
    calls: list[str] = []

    def _fake_prepare_startup_catalog(*, force: bool = False):
        return []

    def _fake_get_startup_router_contributions():
        return []

    def _fake_run_backend_startup_preflight() -> bool:
        calls.append("preflight")
        return True

    async def _fake_run_startup() -> dict[str, object]:
        calls.append("bootstrap")
        return {"status": "ready"}

    monkeypatch.setattr(
        main_module.plugin_runtime,
        "prepare_startup_catalog",
        _fake_prepare_startup_catalog,
    )
    monkeypatch.setattr(
        main_module.plugin_runtime,
        "get_startup_router_contributions",
        _fake_get_startup_router_contributions,
    )
    monkeypatch.setattr(
        main_module,
        "run_backend_startup_preflight",
        _fake_run_backend_startup_preflight,
    )
    monkeypatch.setattr(
        main_module.backend_bootstrap,
        "run_startup",
        _fake_run_startup,
    )

    app = main_module.create_app()

    await _run_startup_handlers(app)

    assert calls == ["preflight", "bootstrap"]


@pytest.mark.asyncio
async def test_startup_event_aborts_when_shared_preflight_fails(monkeypatch):
    calls: list[str] = []

    def _fake_prepare_startup_catalog(*, force: bool = False):
        return []

    def _fake_get_startup_router_contributions():
        return []

    def _fake_run_backend_startup_preflight() -> bool:
        calls.append("preflight")
        return False

    async def _unexpected_run_startup() -> dict[str, object]:
        calls.append("bootstrap")
        raise AssertionError("backend bootstrap should not run after failed preflight")

    monkeypatch.setattr(
        main_module.plugin_runtime,
        "prepare_startup_catalog",
        _fake_prepare_startup_catalog,
    )
    monkeypatch.setattr(
        main_module.plugin_runtime,
        "get_startup_router_contributions",
        _fake_get_startup_router_contributions,
    )
    monkeypatch.setattr(
        main_module,
        "run_backend_startup_preflight",
        _fake_run_backend_startup_preflight,
    )
    monkeypatch.setattr(
        main_module.backend_bootstrap,
        "run_startup",
        _unexpected_run_startup,
    )

    app = main_module.create_app()

    with pytest.raises(RuntimeError, match="Configuration validation failed"):
        await _run_startup_handlers(app)

    assert calls == ["preflight"]


@pytest.mark.asyncio
async def test_shutdown_event_routes_shared_cleanup_after_plugin_runtime_stop(monkeypatch):
    calls: list[str] = []

    async def _fake_stop_runtime_services() -> None:
        calls.append("plugin_runtime")

    async def _fake_shutdown_runtime_resources(*, brain=None) -> None:
        calls.append("shared_cleanup")
        assert brain is main_module.brain

    monkeypatch.setattr(main_module.plugin_runtime, "get_startup_router_contributions", lambda: [])
    monkeypatch.setattr(
        main_module.plugin_runtime,
        "stop_runtime_services",
        _fake_stop_runtime_services,
    )
    monkeypatch.setattr(
        main_module,
        "shutdown_runtime_resources",
        _fake_shutdown_runtime_resources,
    )

    app = main_module.create_app()

    await _run_shutdown_handlers(app)

    assert calls == ["plugin_runtime", "shared_cleanup"]


@pytest.mark.asyncio
async def test_shutdown_event_still_runs_shared_cleanup_when_plugin_runtime_stop_fails(monkeypatch):
    calls: list[str] = []

    async def _fake_stop_runtime_services() -> None:
        calls.append("plugin_runtime")
        raise RuntimeError("boom")

    async def _fake_shutdown_runtime_resources(*, brain=None) -> None:
        calls.append("shared_cleanup")
        assert brain is main_module.brain

    monkeypatch.setattr(main_module.plugin_runtime, "get_startup_router_contributions", lambda: [])
    monkeypatch.setattr(
        main_module.plugin_runtime,
        "stop_runtime_services",
        _fake_stop_runtime_services,
    )
    monkeypatch.setattr(
        main_module,
        "shutdown_runtime_resources",
        _fake_shutdown_runtime_resources,
    )

    app = main_module.create_app()

    await _run_shutdown_handlers(app)

    assert calls == ["plugin_runtime", "shared_cleanup"]
