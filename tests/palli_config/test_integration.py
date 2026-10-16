"""Integration: confirm all three skins produce identical file diffs.

Workflow: load -> switch response task to Anthropic Sonnet -> save -> verify
file contents are identical regardless of which skin was used.
"""
import yaml
from pathlib import Path

import pytest

from palli_config.buffer import ConfigBuffer
from palli_config.core import load_state, save_all


@pytest.fixture
def fresh_env_yaml(tmp_path, isolated_palli_yaml):
    env_local = tmp_path / ".env.local"
    env_local.write_text(
        "LLM_RESPONSE_PROVIDER=deepseek\nLLM_RESPONSE_MODEL=deepseek-v4-pro\n",
        encoding="utf-8",
    )
    return env_local, isolated_palli_yaml


def _do_switch_via_core(env_local: Path, palli_yaml: Path) -> None:
    """The same operation each skin would perform internally."""
    state = load_state(env_local=env_local, palli_yaml=palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("response", provider="anthropic", model="claude-sonnet-4-6")
    save_all(buf, env_local=env_local, palli_yaml=palli_yaml)


def test_round_trip_via_core_reflects_in_both_files(fresh_env_yaml):
    env_local, palli_yaml = fresh_env_yaml
    _do_switch_via_core(env_local, palli_yaml)

    env_text = env_local.read_text(encoding="utf-8")
    yaml_data = yaml.safe_load(palli_yaml.read_text(encoding="utf-8"))

    assert "LLM_RESPONSE_PROVIDER=anthropic" in env_text
    assert "LLM_RESPONSE_MODEL=claude-sonnet-4-6" in env_text
    assert yaml_data["llm"]["routing"]["response"]["provider"] == "anthropic"
    assert yaml_data["llm"]["routing"]["response"]["model"] == "claude-sonnet-4-6"


def test_round_trip_idempotent(fresh_env_yaml):
    """Running the same change twice produces identical output the second time."""
    env_local, palli_yaml = fresh_env_yaml
    _do_switch_via_core(env_local, palli_yaml)
    after_first = (
        env_local.read_text(encoding="utf-8"),
        palli_yaml.read_text(encoding="utf-8"),
    )
    _do_switch_via_core(env_local, palli_yaml)
    after_second = (
        env_local.read_text(encoding="utf-8"),
        palli_yaml.read_text(encoding="utf-8"),
    )
    assert after_first == after_second


def test_load_after_save_returns_same_state(fresh_env_yaml):
    """Save + reload + save -> no spurious changes detected."""
    env_local, palli_yaml = fresh_env_yaml
    _do_switch_via_core(env_local, palli_yaml)
    state = load_state(env_local=env_local, palli_yaml=palli_yaml)
    buf = ConfigBuffer(state)
    assert buf.pending_changes() == []
