from __future__ import annotations

import pytest

import core.backend_bootstrap as backend_bootstrap_module
from core.backend_bootstrap import BackendBootstrapCoordinator


@pytest.mark.asyncio
async def test_backend_bootstrap_runs_ready_path_with_local_owner(monkeypatch):
    calls: list[object] = []

    async def fake_initialize_database() -> None:
        calls.append("database")

    async def fake_initialize_plugin_runtime() -> None:
        calls.append("plugin_runtime")

    async def fake_ensure_runtime_bootstrap() -> None:
        calls.append("runtime_control")

    async def fake_run_recovery() -> None:
        calls.append("runtime_recovery")

    async def fake_ensure_local_owner() -> dict[str, object]:
        calls.append("local_owner")
        return {"id": 7, "email": "local@palli.local"}

    async def fake_start_runtime_services(*, owner_user_id: int | None) -> None:
        calls.append(("runtime_services", owner_user_id))

    monkeypatch.setattr(backend_bootstrap_module.brain, "initialize", fake_initialize_database)
    monkeypatch.setattr(backend_bootstrap_module.plugin_runtime, "initialize", fake_initialize_plugin_runtime)
    monkeypatch.setattr(backend_bootstrap_module.runtime_control, "ensure_bootstrap", fake_ensure_runtime_bootstrap)
    monkeypatch.setattr(backend_bootstrap_module.runtime_recovery, "run_startup_recovery", fake_run_recovery)
    monkeypatch.setattr(backend_bootstrap_module, "local_mode_enabled", lambda: True)
    monkeypatch.setattr(backend_bootstrap_module, "ensure_local_owner", fake_ensure_local_owner)
    monkeypatch.setattr(backend_bootstrap_module.plugin_runtime, "start_runtime_services", fake_start_runtime_services)

    coordinator = BackendBootstrapCoordinator()
    snapshot = await coordinator.run_startup()

    assert snapshot["status"] == "ready"
    assert snapshot["owner_user_id"] == 7
    assert calls == [
        "database",
        "plugin_runtime",
        "runtime_control",
        "runtime_recovery",
        "local_owner",
        ("runtime_services", 7),
    ]
    stage_statuses = {stage["stage_id"]: stage["status"] for stage in snapshot["stages"]}
    assert stage_statuses == {
        "database": "ready",
        "plugin_runtime": "ready",
        "runtime_control": "ready",
        "runtime_recovery": "ready",
        "local_owner": "ready",
        "runtime_services": "ready",
    }


@pytest.mark.asyncio
async def test_backend_bootstrap_records_degraded_optional_stages(monkeypatch):
    calls: list[object] = []

    async def fake_initialize_database() -> None:
        calls.append("database")

    async def fake_initialize_plugin_runtime() -> None:
        calls.append("plugin_runtime")

    async def fake_ensure_runtime_bootstrap() -> None:
        calls.append("runtime_control")

    async def fake_run_recovery() -> None:
        calls.append("runtime_recovery")
        raise RuntimeError("recovery boom")

    async def fake_start_runtime_services(*, owner_user_id: int | None) -> None:
        calls.append(("runtime_services", owner_user_id))
        raise RuntimeError("service boom")

    monkeypatch.setattr(backend_bootstrap_module.brain, "initialize", fake_initialize_database)
    monkeypatch.setattr(backend_bootstrap_module.plugin_runtime, "initialize", fake_initialize_plugin_runtime)
    monkeypatch.setattr(backend_bootstrap_module.runtime_control, "ensure_bootstrap", fake_ensure_runtime_bootstrap)
    monkeypatch.setattr(backend_bootstrap_module.runtime_recovery, "run_startup_recovery", fake_run_recovery)
    monkeypatch.setattr(backend_bootstrap_module, "local_mode_enabled", lambda: False)
    monkeypatch.setattr(backend_bootstrap_module.plugin_runtime, "start_runtime_services", fake_start_runtime_services)

    coordinator = BackendBootstrapCoordinator()
    snapshot = await coordinator.run_startup()

    assert snapshot["status"] == "degraded"
    assert snapshot["counts"]["degraded"] == 2
    assert calls == [
        "database",
        "plugin_runtime",
        "runtime_control",
        "runtime_recovery",
        ("runtime_services", None),
    ]
    stage_statuses = {stage["stage_id"]: stage["status"] for stage in snapshot["stages"]}
    assert stage_statuses["runtime_recovery"] == "degraded"
    assert stage_statuses["local_owner"] == "skipped"
    assert stage_statuses["runtime_services"] == "degraded"
    warnings = coordinator.warning_items()
    assert {item["code"] for item in warnings} == {
        "backend_startup_runtime_recovery_degraded",
        "backend_startup_runtime_services_degraded",
    }


@pytest.mark.asyncio
async def test_backend_bootstrap_fails_fast_on_required_stage_error(monkeypatch):
    async def fake_initialize_database() -> None:
        raise RuntimeError("db unavailable")

    async def fail_unexpected_call() -> None:
        raise AssertionError("later startup stages should not run after a required failure")

    monkeypatch.setattr(backend_bootstrap_module.brain, "initialize", fake_initialize_database)
    monkeypatch.setattr(backend_bootstrap_module.plugin_runtime, "initialize", fail_unexpected_call)
    monkeypatch.setattr(backend_bootstrap_module.runtime_control, "ensure_bootstrap", fail_unexpected_call)
    monkeypatch.setattr(backend_bootstrap_module.runtime_recovery, "run_startup_recovery", fail_unexpected_call)
    monkeypatch.setattr(backend_bootstrap_module, "local_mode_enabled", lambda: False)
    monkeypatch.setattr(backend_bootstrap_module.plugin_runtime, "start_runtime_services", fail_unexpected_call)

    coordinator = BackendBootstrapCoordinator()

    with pytest.raises(RuntimeError, match="db unavailable"):
        await coordinator.run_startup()

    snapshot = coordinator.snapshot()
    assert snapshot["status"] == "failed"
    assert snapshot["counts"]["failed"] == 1
    assert [stage["stage_id"] for stage in snapshot["stages"]] == ["database"]
    assert snapshot["stages"][0]["status"] == "failed"
    assert "db unavailable" in snapshot["stages"][0]["message"]


# --- Batch 8 Task 11 A2: the real "no owner id" line, said at boot ----------
#
# The config-time line (core/config_schema.py) can only read .env.local, so it
# used to say "everyone is a guest" on a box whose owner id was stored in the
# database all along. The local_owner stage holds the stored owner, so it is the
# one place that can tell the truth: it LOGS the real warning only when the env
# id is blank AND the stored owner has no id for that door. A log line, never a
# stage verdict — the stage stays "ready" (the first test above pins that).


def _patch_ready_path(monkeypatch, owner: dict) -> None:
    async def _noop() -> None:
        return None

    async def fake_ensure_local_owner() -> dict:
        return dict(owner)

    async def fake_start_runtime_services(*, owner_user_id) -> None:
        return None

    monkeypatch.setattr(backend_bootstrap_module.brain, "initialize", _noop)
    monkeypatch.setattr(backend_bootstrap_module.plugin_runtime, "initialize", _noop)
    monkeypatch.setattr(backend_bootstrap_module.runtime_control, "ensure_bootstrap", _noop)
    monkeypatch.setattr(backend_bootstrap_module.runtime_recovery, "run_startup_recovery", _noop)
    monkeypatch.setattr(backend_bootstrap_module, "local_mode_enabled", lambda: True)
    monkeypatch.setattr(backend_bootstrap_module, "ensure_local_owner", fake_ensure_local_owner)
    monkeypatch.setattr(backend_bootstrap_module.plugin_runtime, "start_runtime_services", fake_start_runtime_services)


def _doors(monkeypatch, *, discord: bool = False, telegram: bool = False,
           discord_env: str = "", telegram_env: str = "") -> None:
    from core.config import config as cfg

    monkeypatch.setattr(cfg, "ENABLE_DISCORD_BOT", discord, raising=False)
    monkeypatch.setattr(cfg, "DISCORD_TOKEN", "x" * 40 if discord else "", raising=False)
    monkeypatch.setattr(cfg, "LOCAL_OWNER_DISCORD_USER_ID", discord_env, raising=False)
    monkeypatch.setattr(cfg, "ENABLE_TELEGRAM_BOT", telegram, raising=False)
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "1:" + "y" * 30 if telegram else "", raising=False)
    monkeypatch.setattr(cfg, "LOCAL_OWNER_TELEGRAM_USER_ID", telegram_env, raising=False)


def _owner_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == backend_bootstrap_module.logger.name and r.levelname == "WARNING"
    ]


@pytest.mark.asyncio
async def test_discord_on_no_id_anywhere_logs_the_guest_warning_and_stays_ready(monkeypatch, caplog):
    _patch_ready_path(monkeypatch, {"id": 7, "email": "local@palli.local"})
    _doors(monkeypatch, discord=True)
    caplog.set_level("WARNING", logger=backend_bootstrap_module.logger.name)

    snapshot = await BackendBootstrapCoordinator().run_startup()

    stage_statuses = {stage["stage_id"]: stage["status"] for stage in snapshot["stages"]}
    assert stage_statuses["local_owner"] == "ready"
    assert snapshot["status"] == "ready"
    warnings = _owner_warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "LOCAL_OWNER_DISCORD_USER_ID" in warnings[0]
    assert "guest" in warnings[0]
    assert "stored" in warnings[0]


@pytest.mark.asyncio
async def test_discord_owner_stored_but_not_in_env_says_nothing(monkeypatch, caplog):
    """Her own box: the env line is blank, the id lives in the database. The
    config-time line could not see it; boot can, and stays quiet."""
    _patch_ready_path(monkeypatch, {"id": 7, "discord_user_id": "123456789012345678"})  # gitleaks:allow
    _doors(monkeypatch, discord=True)
    caplog.set_level("WARNING", logger=backend_bootstrap_module.logger.name)

    await BackendBootstrapCoordinator().run_startup()

    assert _owner_warnings(caplog) == []


@pytest.mark.asyncio
async def test_discord_off_says_nothing_about_its_owner_id(monkeypatch, caplog):
    _patch_ready_path(monkeypatch, {"id": 7})
    _doors(monkeypatch, discord=False)
    caplog.set_level("WARNING", logger=backend_bootstrap_module.logger.name)

    await BackendBootstrapCoordinator().run_startup()

    assert _owner_warnings(caplog) == []


@pytest.mark.asyncio
async def test_an_env_id_leaves_the_config_line_to_speak(monkeypatch, caplog):
    """A set env id is the config line's business (numeric: seeded; not
    numeric: it already names the value). Boot does not say it twice."""
    _patch_ready_path(monkeypatch, {"id": 7})
    _doors(monkeypatch, discord=True, discord_env="your-id-here")
    caplog.set_level("WARNING", logger=backend_bootstrap_module.logger.name)

    await BackendBootstrapCoordinator().run_startup()

    assert _owner_warnings(caplog) == []


@pytest.mark.asyncio
async def test_telegram_twin_warns_only_when_nothing_is_stored(monkeypatch, caplog):
    _patch_ready_path(monkeypatch, {"id": 7})
    _doors(monkeypatch, telegram=True)
    caplog.set_level("WARNING", logger=backend_bootstrap_module.logger.name)

    snapshot = await BackendBootstrapCoordinator().run_startup()

    assert {s["stage_id"]: s["status"] for s in snapshot["stages"]}["local_owner"] == "ready"
    warnings = _owner_warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "LOCAL_OWNER_TELEGRAM_USER_ID" in warnings[0]
    assert "guest" in warnings[0]

    caplog.clear()
    _patch_ready_path(monkeypatch, {"id": 7, "telegram_user_id": "424242424"})
    await BackendBootstrapCoordinator().run_startup()
    assert _owner_warnings(caplog) == []
