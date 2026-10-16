"""ConfigBuffer — staged-changes buffer in front of ConfigState."""
import pytest

from palli_config.buffer import ConfigBuffer
from palli_config.core import ConfigState, ProviderConfig, TaskConfig


def _state() -> ConfigState:
    return ConfigState(
        tasks={
            "response": TaskConfig(provider="deepseek", model="deepseek-v4-pro"),
            "decision": TaskConfig(provider="ollama", model="qwen3:8b"),
        },
        providers={
            "openai": ProviderConfig(name="openai", api_key="sk-old"),
            "deepseek": ProviderConfig(name="deepseek", api_key="sk-deep"),
        },
    )


def test_buffer_starts_with_no_pending_changes():
    buf = ConfigBuffer(_state())
    assert buf.pending_changes() == []


def test_set_task_records_change():
    buf = ConfigBuffer(_state())
    buf.set_task("response", provider="openai", model="gpt-4o")
    pending = buf.pending_changes()
    assert any(c.kind == "task" and c.target == "response" for c in pending)


def test_set_task_to_same_value_records_no_change():
    buf = ConfigBuffer(_state())
    buf.set_task("response", provider="deepseek", model="deepseek-v4-pro")
    assert buf.pending_changes() == []


def test_set_provider_field_records_change():
    buf = ConfigBuffer(_state())
    buf.set_provider_field("openai", "api_key", "sk-new")
    pending = buf.pending_changes()
    assert any(c.kind == "provider" and c.target == "openai" for c in pending)


def test_revert_clears_all_pending_changes():
    buf = ConfigBuffer(_state())
    buf.set_task("response", provider="openai", model="gpt-4o")
    buf.set_provider_field("openai", "api_key", "sk-new")
    buf.revert()
    assert buf.pending_changes() == []


def test_effective_state_reflects_pending_changes():
    buf = ConfigBuffer(_state())
    buf.set_task("response", provider="openai", model="gpt-4o")
    eff = buf.effective_state()
    assert eff.tasks["response"].provider == "openai"
    assert eff.tasks["response"].model == "gpt-4o"


def test_set_task_unknown_task_raises():
    buf = ConfigBuffer(_state())
    with pytest.raises(ValueError, match="unknown task"):
        buf.set_task("nonexistent", provider="openai", model="gpt-4o")


def test_set_provider_field_unknown_provider_raises():
    buf = ConfigBuffer(_state())
    with pytest.raises(ValueError, match="unknown provider"):
        buf.set_provider_field("nonexistent", "api_key", "sk-x")
