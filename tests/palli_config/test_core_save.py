"""save_all() — atomic write to .env.local + palli.yaml in sync."""
from pathlib import Path

import yaml

from palli_config.buffer import ConfigBuffer
from palli_config.core import load_state, save_all


def test_save_writes_task_change_to_env_local(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("response", provider="openai", model="gpt-4o")
    save_all(buf, env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    text = isolated_env_local.read_text(encoding="utf-8")
    assert "LLM_RESPONSE_PROVIDER=openai" in text
    assert "LLM_RESPONSE_MODEL=gpt-4o" in text


def test_save_writes_task_change_to_yaml(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("response", provider="openai", model="gpt-4o")
    save_all(buf, env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    yaml_data = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    routing = yaml_data["llm"]["routing"]
    assert routing["response"]["provider"] == "openai"
    assert routing["response"]["model"] == "gpt-4o"


def test_save_writes_api_key_to_env_local_only(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_provider_field("openai", "api_key", "sk-new-key")
    save_all(buf, env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    env_text = isolated_env_local.read_text(encoding="utf-8")
    yaml_text = isolated_palli_yaml.read_text(encoding="utf-8")
    assert "sk-new-key" in env_text
    assert "sk-new-key" not in yaml_text  # security: keys never in yaml


def test_save_is_atomic_failure_leaves_originals_intact(
    tmp_path: Path, isolated_env_local: Path, isolated_palli_yaml: Path,
    monkeypatch
):
    """Force a yaml write failure; .env.local should not have been changed either."""
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("response", provider="openai", model="gpt-4o")
    original_env_text = isolated_env_local.read_text(encoding="utf-8")

    # Simulate yaml write failure by injecting a failing dump on the
    # round-trip YAML instance used by core._render_palli_yaml.
    import palli_config.core as core_mod

    def boom(*args, **kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr(core_mod._RT_YAML, "dump", boom)

    import pytest as _pt
    with _pt.raises(OSError):
        save_all(buf, env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)

    # env.local must not have been mutated
    assert isolated_env_local.read_text(encoding="utf-8") == original_env_text


def test_save_preserves_yaml_comments(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    """Saving must NOT strip comments from data/palli.yaml.

    PyYAML's safe_dump erased the 18 institutional-knowledge comments
    (V4 family notes, max_tokens reminder, etc.) on save. ruamel.yaml's
    round-trip mode preserves them.
    """
    original_text = isolated_palli_yaml.read_text(encoding="utf-8")
    # Sentinel: a unique substring of an existing comment in data/palli.yaml.
    # Lives in the deepseek provider block (~line 54).
    sentinel = "max_tokens >= 200"
    assert sentinel in original_text, (
        "Test assumption broken: sentinel comment missing from fixture"
    )

    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("response", provider="openai", model="gpt-4o")
    save_all(buf, env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)

    saved_text = isolated_palli_yaml.read_text(encoding="utf-8")
    assert sentinel in saved_text, "Comment was stripped on save"


def test_save_preserves_unrelated_env_lines(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    """Saving a single change should not wipe other env vars."""
    # Add an unrelated line first.
    isolated_env_local.write_text(
        isolated_env_local.read_text(encoding="utf-8")
        + "\nUNRELATED_VAR=keep_this\n",
        encoding="utf-8",
    )
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("response", provider="openai", model="gpt-4o")
    save_all(buf, env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    text = isolated_env_local.read_text(encoding="utf-8")
    assert "UNRELATED_VAR=keep_this" in text


def test_save_clears_yaml_routing_when_value_emptied(
    isolated_env_local: Path, isolated_palli_yaml: Path
):
    """Empty provider/model in state must clear the yaml entry, not leave the prior value."""
    state = load_state(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("response", provider="", model="")
    save_all(buf, env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    yaml_data = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    response_block = yaml_data["llm"]["routing"].get("response", {})
    assert "provider" not in response_block
    assert "model" not in response_block
