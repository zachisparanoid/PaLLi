"""core.load_state() — read current config from .env.local + palli.yaml."""
from pathlib import Path

import pytest

from palli_config.core import ConfigState, load_state


def test_load_returns_config_state_with_every_runtime_lane(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    assert isinstance(state, ConfigState)
    from palli_config.brain import ROUTING_LANES
    assert tuple(state.tasks.keys()) == ROUTING_LANES   # ten, her voice first (R4, 2026-09-03)


def test_load_reads_response_task_from_env_local(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    response = state.tasks["response"]
    assert response.provider == "deepseek"
    assert response.model == "deepseek-v4-pro"


def test_load_reads_provider_api_keys_from_env_local(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    openai = state.providers["openai"]
    assert openai.api_key == "sk-test-key"
    anthropic = state.providers["anthropic"]
    assert anthropic.api_key == "sk-ant-test-key"
