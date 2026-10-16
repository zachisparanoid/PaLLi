from __future__ import annotations

import logging

import pytest

from ai.llm_provider_registry import (
    build_fallback_provider_name,
    close_provider_registry,
    collect_replaced_providers,
    configure_provider_registry,
    select_default_provider,
)


class _FakeProvider:
    def __init__(self, name: str, *, fail_on_close: bool = False) -> None:
        self.provider_name = name
        self.fail_on_close = fail_on_close
        self.close_calls = 0

    def diagnostics(self):
        return {
            "provider": self.provider_name,
            "label": self.provider_name,
            "configured": True,
            "default_model": "",
            "base_url": "",
            "supports_vision": False,
        }

    async def close(self) -> None:
        self.close_calls += 1
        if self.fail_on_close:
            raise RuntimeError("close failed")


def test_configure_provider_registry_uses_custom_primary_as_all_fallback_aliases() -> None:
    custom_primary = _FakeProvider("custom_primary")

    registry = configure_provider_registry(
        {"custom_primary": custom_primary},
        default_provider_name="custom_primary",
    )

    assert registry.provider is custom_primary
    assert registry.openai_provider is custom_primary
    assert registry.deepseek_provider is custom_primary
    assert registry.local_openai_provider is custom_primary
    assert registry.codex_provider is custom_primary
    assert build_fallback_provider_name(
        default_provider_name="custom_primary",
        openai_provider=registry.openai_provider,
    ) == "custom_primary"


def test_select_default_provider_warns_with_actual_fallback_name(caplog: pytest.LogCaptureFixture) -> None:
    fallback_provider = _FakeProvider("custom_primary")
    logger = logging.getLogger("tests.ai.llm_provider_registry")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        selected = select_default_provider(
            default_provider_name="mystery",
            providers={"custom_primary": fallback_provider},
            fallback_provider=fallback_provider,
            logger=logger,
        )

    assert selected is fallback_provider
    assert "Unknown provider 'mystery', defaulting to 'custom_primary'" in caplog.text


@pytest.mark.asyncio
async def test_close_provider_registry_closes_unique_providers_once_and_ignores_failures() -> None:
    shared_provider = _FakeProvider("shared")
    failing_provider = _FakeProvider("failing", fail_on_close=True)

    await close_provider_registry(
        [shared_provider, shared_provider, failing_provider]
    )

    assert shared_provider.close_calls == 1
    assert failing_provider.close_calls == 1


def test_collect_replaced_providers_skips_reused_instances_and_dedupes_removed() -> None:
    removed_provider = _FakeProvider("removed")
    kept_provider = _FakeProvider("kept")

    replaced = collect_replaced_providers(
        previous_providers=[removed_provider, kept_provider, removed_provider],
        next_providers=[kept_provider, _FakeProvider("new")],
    )

    assert replaced == [removed_provider]
