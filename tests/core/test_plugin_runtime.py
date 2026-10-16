from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.plugin_runtime import plugin_runtime


@pytest.mark.asyncio
async def test_discover_persists_builtins_and_local_manifest(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "plugins"
    sample = plugin_dir / "sample-tool"
    sample.mkdir(parents=True)
    (sample / "palli_plugin.json").write_text(
        json.dumps(
            {
                "plugin_id": "local.sample_tool",
                "display_name": "Sample Tool",
                "version": "0.1.0",
                "plugin_api_version": "1",
                "entrypoint": "plugin.py:get_plugin_contributions",
                "families": ["agent_tool"],
                "criticality": "optional",
                "reload_mode": "runtime_reload",
                "host_support": ["any"],
            }
        ),
        encoding="utf-8",
    )

    persisted: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []

    async def fake_upsert(**kwargs):
        persisted.append(kwargs)

    async def fake_create_run(**kwargs):
        runs.append(kwargs)

    monkeypatch.setattr("core.plugin_runtime.config.PLUGIN_DIRECTORIES", [str(plugin_dir)])
    monkeypatch.setattr("core.plugin_runtime.config.PLUGIN_API_VERSION", "1")
    monkeypatch.setattr("core.plugin_runtime.config.PLUGIN_RUNTIME_ENABLED", True)
    monkeypatch.setattr("core.plugin_runtime.host_platform_service.platform_snapshot", lambda: {"host_backend": "linux_x11"})
    monkeypatch.setattr("core.plugin_runtime.brain.upsert_plugin_runtime_plugin", fake_upsert)
    monkeypatch.setattr("core.plugin_runtime.brain.create_plugin_runtime_run", fake_create_run)

    plugin_runtime.prepare_startup_catalog(force=True)
    await plugin_runtime.discover(trigger_kind="manual")

    ids = {str(item["plugin_id"]) for item in persisted}
    assert "builtin.llm.providers" in ids
    assert "builtin.voice.providers" in ids
    assert "local.sample_tool" in ids
    assert runs and runs[0]["action_kind"] == "discover"


@pytest.mark.asyncio
async def test_set_desired_enabled_blocks_required_plugin_disable(monkeypatch):
    async def fake_get_plugin(_plugin_id: str):
        return {
            "plugin_id": "builtin.llm.providers",
            "criticality": "required",
            "reload_mode": "runtime_reload",
            "health_status": "ready",
        }

    monkeypatch.setattr(plugin_runtime, "get_plugin", fake_get_plugin)

    with pytest.raises(ValueError, match="required_plugin_cannot_be_disabled"):
        await plugin_runtime.set_desired_enabled(plugin_id="builtin.llm.providers", enabled=False)


@pytest.mark.asyncio
async def test_apply_runtime_reload_plugins_uses_llm_registry_replacement(monkeypatch):
    captured: dict[str, object] = {}
    custom_provider = object()

    class _FakeLLM:
        async def replace_provider_registry(self, providers):
            captured["llm_providers"] = dict(providers)

        def set_provider_registry(self, providers):
            raise AssertionError("runtime reload should use replace_provider_registry")

    class _FakeVoiceSpeechService:
        async def replace_provider_registry(self, *, stt_providers, tts_providers):
            captured["stt_providers"] = dict(stt_providers)
            captured["tts_providers"] = dict(tts_providers)

        def set_provider_registry(self, *, stt_providers, tts_providers):
            raise AssertionError("runtime reload should use replace_provider_registry")

    async def fake_persist(catalog, startup_apply_restart_required=False):
        captured["persisted_catalog"] = [dict(item) for item in catalog]

    async def fake_create_run(**kwargs):
        captured["run"] = dict(kwargs)

    monkeypatch.setattr(
        plugin_runtime,
        "_load_contribution_bundle",
        lambda item: SimpleNamespace(
            llm_providers={"custom": custom_provider},
            stt_providers={},
            tts_providers={},
        ),
    )
    monkeypatch.setattr(plugin_runtime, "_persist_catalog", fake_persist)
    monkeypatch.setattr("core.plugin_runtime.brain.create_plugin_runtime_run", fake_create_run)
    monkeypatch.setattr("ai.llm_client.llm", _FakeLLM())
    monkeypatch.setattr("voice.speech.voice_speech_service", _FakeVoiceSpeechService())

    catalog = [
        {
            "plugin_id": "local.runtime_llm",
            "reload_mode": "runtime_reload",
            "desired_enabled": True,
            "compatible": True,
            "families": ["llm_provider"],
        }
    ]

    result = await plugin_runtime._apply_runtime_reload_plugins(catalog, trigger_kind="manual")

    assert captured["llm_providers"] == {"custom": custom_provider}
    assert captured["stt_providers"] == {}
    assert captured["tts_providers"] == {}
    assert result["errors"] == {}
    assert result["restart_required"] is False
    assert captured["persisted_catalog"][0]["applied_enabled"] is True
    assert captured["persisted_catalog"][0]["health_status"] == "ready"
    assert captured["run"]["status"] == "completed"


@pytest.mark.asyncio
async def test_set_desired_enabled_marks_restart_required_for_staged_plugins(monkeypatch):
    updated: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []

    async def fake_get_plugin(_plugin_id: str):
        return {
            "plugin_id": "builtin.api.web",
            "criticality": "required",
            "reload_mode": "restart_required",
            "health_status": "ready",
        }

    async def fake_update(**kwargs):
        updated.append(kwargs)

    async def fake_create_run(**kwargs):
        runs.append(kwargs)

    async def fake_get_runtime_plugin(*, plugin_id: str):
        return {
            "plugin_id": plugin_id,
            "desired_enabled": True,
            "restart_required": True,
        }

    monkeypatch.setattr(plugin_runtime, "get_plugin", fake_get_plugin)
    monkeypatch.setattr("core.plugin_runtime.brain.update_plugin_runtime_plugin", fake_update)
    monkeypatch.setattr("core.plugin_runtime.brain.create_plugin_runtime_run", fake_create_run)
    monkeypatch.setattr("core.plugin_runtime.brain.get_plugin_runtime_plugin", fake_get_runtime_plugin)

    item = await plugin_runtime.set_desired_enabled(plugin_id="builtin.api.web", enabled=True)

    assert item["restart_required"] is True
    assert updated[0]["health_status"] == "restart_required"
    assert runs[0]["action_kind"] == "stage"


@pytest.mark.asyncio
async def test_stop_runtime_services_stops_every_service_even_if_one_fails():
    calls: list[str] = []

    async def _failing_stop() -> None:
        calls.append("service:fail")
        raise RuntimeError("boom")

    async def _successful_stop() -> None:
        calls.append("service:ok")

    previous_started_services = dict(plugin_runtime._started_services)
    plugin_runtime._started_services = {
        "failing": SimpleNamespace(stop=_failing_stop),
        "healthy": SimpleNamespace(stop=_successful_stop),
        "passive": SimpleNamespace(stop=None),
    }

    try:
        await plugin_runtime.stop_runtime_services()
        assert plugin_runtime._started_services == {}
    finally:
        plugin_runtime._started_services = previous_started_services

    assert calls == ["service:fail", "service:ok"]


def test_build_health_snapshot_marks_restart_pending_and_counts(monkeypatch):
    monkeypatch.setattr(
        plugin_runtime,
        "prepare_startup_catalog",
        lambda force=False: [
            {
                "plugin_id": "builtin.llm.providers",
                "display_name": "Built-in LLM Providers",
                "source_kind": "builtin",
                "families": ["llm_provider"],
                "desired_enabled": True,
                "compatibility_status": "compatible",
                "health_status": "ready",
                "restart_required": False,
                "last_error": "",
            },
            {
                "plugin_id": "local.broken",
                "display_name": "Broken Local Plugin",
                "source_kind": "local",
                "families": ["agent_tool"],
                "desired_enabled": True,
                "compatibility_status": "manifest_invalid",
                "health_status": "error",
                "restart_required": True,
                "last_error": "bad manifest",
            },
        ],
    )

    snapshot = plugin_runtime.build_health_snapshot()

    assert snapshot["status"] == "degraded"
    assert snapshot["counts"]["total"] == 2
    assert snapshot["counts"]["enabled"] == 2
    assert snapshot["counts"]["errors"] == 1
    assert snapshot["restart_pending"] is True


def test_doctor_checks_upgrade_required_plugin_issues_to_blockers(monkeypatch):
    monkeypatch.setattr(
        plugin_runtime,
        "prepare_startup_catalog",
        lambda force=False: [
            {
                "plugin_id": "builtin.voice.providers",
                "display_name": "Built-in Speech Providers",
                "criticality": "required",
                "desired_enabled": True,
                "compatibility_status": "dependency_missing",
            },
            {
                "plugin_id": "local.optional",
                "display_name": "Optional Local Plugin",
                "criticality": "optional",
                "desired_enabled": True,
                "compatibility_status": "incompatible",
            },
        ],
    )

    checks = plugin_runtime.doctor_checks()

    assert checks[0]["severity"] == "core_blocker"
    assert checks[1]["severity"] == "warning"


def test_prepare_startup_catalog_excludes_removed_channel_builtins():
    """Wave 0 removed the Slack and Telegram channel builtins. Telegram came
    back on the owner's word (the telegram door, 2026-09-14) and is now a
    real door with a real client behind it; Slack is still gone."""
    items = plugin_runtime.prepare_startup_catalog(force=True)
    plugin_ids = {str(item.get("plugin_id") or "") for item in items}

    assert "builtin.channel.discord_bot" in plugin_ids
    assert "builtin.channel.telegram_bot" in plugin_ids
    assert "builtin.channel.slack_bot" not in plugin_ids


def test_builtin_llm_bundle_matches_default_registry():
    """The builtin bundle is installed at boot via replace_provider_registry(),
    which sets llm.providers to EXACTLY this dict — so it must carry every
    provider build_default_provider_registry() knows about, or a routed
    provider is silently absent from the live server.

    Regression (2026-07-06): this test previously pinned the set WITHOUT
    'anthropic' while Bug 3 (2026-07-04) routed turn_read/cadence/appraisal to
    anthropic::claude-haiku — so every live turn's read resolved to "unknown
    provider", fell back to OpenAI, and 404'd to None. The bundle now derives
    from build_default_provider_registry so the two can't drift again.
    """
    from ai.llm_provider_registry import build_default_provider_registry

    bundle = plugin_runtime._load_builtin_bundle("builtin.llm.providers")
    assert set(bundle.llm_providers) == set(build_default_provider_registry())
    # Explicit floor: the pay-per-token Anthropic API provider (distinct from
    # the 'claude' CLI/subscription provider) must be installable at boot.
    assert "anthropic" in bundle.llm_providers


def test_all_yaml_routed_providers_are_installed_at_boot():
    """Every provider referenced by data/palli.yaml's llm.routing lanes must be
    in the boot bundle — the real invariant behind the anthropic outage."""
    from core.settings_service import SettingsService

    settings = SettingsService(yaml_path="data/palli.yaml").load()
    bundle = plugin_runtime._load_builtin_bundle("builtin.llm.providers")
    installed = set(bundle.llm_providers)
    routing = settings.llm.routing
    for lane_name in type(routing).model_fields:
        entry = getattr(routing, lane_name)
        if entry is None:
            continue
        assert entry.provider in installed, (
            f"llm.routing.{lane_name} routes to provider {entry.provider!r} "
            f"which the boot bundle does not install ({sorted(installed)})"
        )

