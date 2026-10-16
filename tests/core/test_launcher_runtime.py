from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.launcher_runtime import (
    PalliLauncher,
    collect_prompt_loading_status,
    enabled_channel_integrations,
    run_backend_startup_preflight,
    shutdown_runtime_resources,
)
from core.plugin_runtime import PluginChannelContribution


def test_enabled_channel_integrations_filters_builtin_channels_but_keeps_custom_ones():
    """Wave 0 dropped slack from build_channel_enabled_map, and the
    Telegram door (2026-09-14) put telegram_bot back on it beside
    discord_bot. The two builtins are the whole first-class set;
    custom contributions flow through unfiltered, which is what this
    case pins — a contribution this map has never heard of is kept."""
    config_obj = SimpleNamespace(
        ENABLE_DISCORD_BOT=True,
        DISCORD_TOKEN="discord-token",
    )
    plugin_runtime_obj = SimpleNamespace(
        get_startup_channel_integrations=lambda: [
            PluginChannelContribution(
                channel_id="discord_bot",
                label="Discord Bot",
                start=lambda: asyncio.sleep(0),
            ),
            PluginChannelContribution(
                channel_id="custom_bridge",
                label="Custom Bridge",
                start=lambda: asyncio.sleep(0),
            ),
        ]
    )

    integrations = enabled_channel_integrations(
        plugin_runtime_obj=plugin_runtime_obj,
        config_obj=config_obj,
    )

    assert [item.channel_id for item in integrations] == [
        "discord_bot",
        "custom_bridge",
    ]


def test_the_channel_map_follows_boots_verdict_not_the_raw_flag():
    """Week 2 task 7: a blank token no longer stops the boot, so what decides
    whether the Discord client is started is `effective_discord_enabled` — the
    verdict validate() recorded — and not the two raw settings. A token in the
    file with the flag turned off for this boot must NOT open the channel."""
    from core.launcher_runtime import build_channel_enabled_map

    off = SimpleNamespace(
        effective_discord_enabled=False,
        ENABLE_DISCORD_BOT=True,
        DISCORD_TOKEN="x" * 40,
    )
    assert build_channel_enabled_map(config_obj=off)["discord_bot"] is False

    on = SimpleNamespace(
        effective_discord_enabled=True,
        ENABLE_DISCORD_BOT=True,
        DISCORD_TOKEN="x" * 40,
    )
    assert build_channel_enabled_map(config_obj=on)["discord_bot"] is True


def test_the_channel_map_never_opens_discord_on_a_blank_token():
    """Belt and braces. `Config` declares effective_discord_enabled True at
    class level, so an object read before validate() ran would say "on" — and
    starting a Discord client with an empty token is the one thing this map
    exists to prevent. A blank token closes the channel whatever the flag
    says; with no flag at all the old expression is the fallback."""
    from core.launcher_runtime import build_channel_enabled_map

    unvalidated = SimpleNamespace(
        effective_discord_enabled=True,
        ENABLE_DISCORD_BOT=True,
        DISCORD_TOKEN="",
    )
    assert build_channel_enabled_map(config_obj=unvalidated)["discord_bot"] is False

    stand_in = SimpleNamespace(ENABLE_DISCORD_BOT=True, DISCORD_TOKEN="")
    assert build_channel_enabled_map(config_obj=stand_in)["discord_bot"] is False


def test_run_backend_startup_preflight_validates_logs_prompts_and_prepares_catalog():
    calls: list[str] = []

    def _validate() -> bool:
        calls.append("validate")
        return True

    def _log_all_prompts() -> dict[str, bool]:
        calls.append("prompts")
        return {
            "turn_read": True,
            "vision_analysis": True,
        }

    def _prepare_startup_catalog(*, force: bool = False):
        calls.append(f"catalog:{force}")
        return []

    result = run_backend_startup_preflight(
        config_obj=SimpleNamespace(validate=_validate),
        plugin_runtime_obj=SimpleNamespace(
            prepare_startup_catalog=_prepare_startup_catalog,
        ),
        prompt_loader=SimpleNamespace(log_all_prompts=_log_all_prompts),
    )

    assert result is True
    assert calls == ["validate", "prompts", "catalog:True"]


def test_run_backend_startup_preflight_stops_on_invalid_config():
    calls: list[str] = []

    def _validate() -> bool:
        calls.append("validate")
        return False

    def _log_all_prompts() -> dict[str, bool]:
        calls.append("prompts")
        return {}

    def _prepare_startup_catalog(*, force: bool = False):
        calls.append(f"catalog:{force}")
        return []

    result = run_backend_startup_preflight(
        config_obj=SimpleNamespace(validate=_validate),
        plugin_runtime_obj=SimpleNamespace(
            prepare_startup_catalog=_prepare_startup_catalog,
        ),
        prompt_loader=SimpleNamespace(log_all_prompts=_log_all_prompts),
    )

    assert result is False
    assert calls == ["validate"]


def test_collect_prompt_loading_status_reports_critical_missing_from_inventory():
    report = collect_prompt_loading_status(
        prompt_loader=SimpleNamespace(
            inspect_prompt_inventory=lambda: {
                "status": {
                    "vision_analysis": False,
                    "turn_read": True,
                    "reply_govern": True,
                },
                "missing": ["vision_analysis"],
                "empty": [],
                "errors": {},
                "loaded_count": 2,
                "total_count": 3,
            }
        )
    )

    assert report["status"] == "warning"
    assert report["critical_missing"] == ["vision_analysis"]
    assert report["loaded_count"] == 2
    assert report["total_count"] == 3
    assert "vision_analysis" in report["summary"]


@pytest.mark.asyncio
async def test_launcher_start_uses_shared_backend_preflight(monkeypatch):
    calls: list[str] = []

    async def _unexpected_monitor(_tasks) -> None:
        raise AssertionError("launcher should not build tasks after failed preflight")

    launcher = PalliLauncher(
        config_obj=SimpleNamespace(validate=lambda: False),
        plugin_runtime_obj=SimpleNamespace(
            prepare_startup_catalog=lambda force=False: [],
            get_startup_channel_integrations=lambda: [],
        ),
        cleanup_runner=lambda: asyncio.sleep(0),
    )

    monkeypatch.setattr(
        "core.launcher_runtime.run_backend_startup_preflight",
        lambda **kwargs: calls.append("preflight") or False,
    )
    monkeypatch.setattr(launcher, "_monitor_running_tasks", _unexpected_monitor)

    await launcher.start()

    assert calls == ["preflight"]


@pytest.mark.asyncio
async def test_monitor_running_tasks_cancels_pending_components_when_one_exits():
    cancellation_seen = asyncio.Event()

    async def _long_running() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            raise

    async def _finished_component() -> None:
        return None

    launcher = PalliLauncher(
        config_obj=SimpleNamespace(validate=lambda: True),
        plugin_runtime_obj=SimpleNamespace(
            prepare_startup_catalog=lambda force=False: [],
            get_startup_channel_integrations=lambda: [],
        ),
        cleanup_runner=lambda: asyncio.sleep(0),
    )

    completed_task = asyncio.create_task(_finished_component(), name="backend")
    pending_task = asyncio.create_task(_long_running(), name="channel:discord_bot")

    await launcher._monitor_running_tasks([completed_task, pending_task])

    assert cancellation_seen.is_set()
    assert pending_task.cancelled() is True


@pytest.mark.asyncio
async def test_shutdown_runtime_resources_closes_dependencies_in_order():
    calls: list[object] = []

    class _Closable:
        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            calls.append(self.name)

    await shutdown_runtime_resources(
        brain=_Closable("brain"),
        llm=_Closable("llm"),
        voice_speech_service=_Closable("voice"),
    )

    assert calls == [
        "brain",
        "llm",
        "voice",
    ]
