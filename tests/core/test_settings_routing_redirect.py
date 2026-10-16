from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.settings_boot import boot_settings, settings_service
from core.settings_schema import PalliSettings


@pytest.fixture(autouse=True)
def _reset_boot(tmp_path: Path):
    """Ensure each test starts with a fresh boot from a hermetic yaml.

    The live data/palli.yaml routing is owner-tuned and changes over
    time; these tests pin routing values they wrote themselves
    (response → openai/gpt-5-mini).
    """
    import core.settings_boot as _mod
    from tests.core.conftest import write_hermetic_settings_yaml

    target = write_hermetic_settings_yaml(tmp_path / "palli.yaml")

    original_service = _mod.settings_service
    original_booted = _mod._booted
    _mod._booted = False
    _mod.settings_service = __import__("core.settings_service", fromlist=["SettingsService"]).SettingsService(
        yaml_path=target,
    )
    _mod.settings_service.load()
    _mod._booted = True
    yield
    # Restore the original singleton — leaking the temp-yaml service
    # poisons later tests (e.g. voice.speech resolves stt/tts providers
    # from core.settings_boot.settings_service at call time).
    _mod.settings_service = original_service
    _mod._booted = original_booted


def test_get_task_provider_reads_from_settings():
    from ai.llm_routing import get_task_provider

    # The seed YAML routes all tasks through openai (per .env.template alignment)
    provider = get_task_provider("response")
    assert provider == "openai"


def test_get_task_model_reads_from_settings():
    from ai.llm_routing import get_task_model

    # The seed YAML routes response to gpt-5-mini
    model = get_task_model("response")
    assert model is not None
    # Model reference is "provider::model" format
    assert "gpt-5-mini" in model


def test_get_task_provider_uses_routing_not_env(monkeypatch):
    from ai.llm_routing import get_task_provider

    # Even if env says deepseek, the YAML says openai
    monkeypatch.setenv("LLM_RESPONSE_PROVIDER", "deepseek")
    provider = get_task_provider("response")
    assert provider == "openai"


def test_get_task_model_uses_routing_not_env(monkeypatch):
    from ai.llm_routing import get_task_model

    monkeypatch.setenv("LLM_RESPONSE_MODEL", "gpt-fake-model")
    model = get_task_model("response")
    assert model is not None
    assert "gpt-fake-model" not in model
    assert "gpt-5-mini" in model
