"""R4 (spec §1.5 landmine 5): the TUI knew 8 of 10 lanes, 5 of 7 providers,
and resolved `env or yaml` while the runtime resolves `yaml or env` — and
never wrote LLM_PROVIDER, so it could not move her voice at all."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.llm_capabilities import PROVIDERS as CAPABILITIES
from palli_config.brain import ROUTING_LANES
from palli_config.buffer import ConfigBuffer
from palli_config.core import PROVIDERS, TASKS, load_state, save_all
from palli_config.providers import ProviderCatalog


def test_the_tui_lists_the_runtimes_lanes_and_providers():
    assert TASKS == ROUTING_LANES and len(TASKS) == 10
    assert PROVIDERS == tuple(CAPABILITIES) and len(PROVIDERS) == 7


def test_her_voice_resolves_from_llm_provider_not_the_lane_key(tmp_path, isolated_palli_yaml):
    env = tmp_path / ".env.local"
    env.write_text("LLM_PROVIDER=openai\nLLM_RESPONSE_MODEL=gpt-4o\nLLM_RESPONSE_PROVIDER=deepseek\n", encoding="utf-8")
    state = load_state(env_local=env, palli_yaml=isolated_palli_yaml)
    assert (state.tasks["response"].provider, state.tasks["response"].model) == ("openai", "gpt-4o")
    assert any("her voice runs env LLM_PROVIDER='openai'" in d and "yaml is ignored" in d for d in state.disagreements)


def test_a_routing_lane_prefers_the_yaml_and_says_so(tmp_path, isolated_palli_yaml):
    doc = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    yaml_row = doc["llm"]["routing"]["decision"]
    other = "openai" if yaml_row["provider"] != "openai" else "anthropic"
    env = tmp_path / ".env.local"
    env.write_text(f"LLM_PROVIDER=deepseek\nLLM_DECISION_PROVIDER={other}\nLLM_DECISION_MODEL=gpt-4o\n", encoding="utf-8")
    state = load_state(env_local=env, palli_yaml=isolated_palli_yaml)
    assert state.tasks["decision"].provider == yaml_row["provider"]
    assert any(d.startswith("task 'decision' provider: yaml=") and "wins" in d for d in state.disagreements)


def test_save_writes_llm_provider_from_her_voice_and_keeps_crlf(tmp_path, isolated_palli_yaml):
    env = tmp_path / ".env.local"
    env.write_bytes(b"# keep me\r\nLLM_PROVIDER=deepseek\r\nLLM_RESPONSE_MODEL=deepseek-v4-flash\r\n")
    state = load_state(env_local=env, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("response", provider="openai", model="gpt-4o")
    save_all(buf, env_local=env, palli_yaml=isolated_palli_yaml)
    raw = env.read_bytes()
    assert b"\r\n" in raw and b"# keep me\r\n" in raw
    text = raw.decode("utf-8")
    assert "LLM_PROVIDER=openai" in text and "LLM_RESPONSE_MODEL=gpt-4o" in text
    assert "LLM_NARRATIVE_AUTHOR_PROVIDER" not in text   # no env key exists for that lane
    assert text.count("LLM_PROVIDER=") == 1


def test_the_two_new_lanes_are_editable_and_saved_to_the_yaml(tmp_path, isolated_palli_yaml):
    env = tmp_path / ".env.local"
    env.write_text("LLM_PROVIDER=deepseek\n", encoding="utf-8")
    state = load_state(env_local=env, palli_yaml=isolated_palli_yaml)
    assert "appraisal" in state.tasks and "narrative_author" in state.tasks
    buf = ConfigBuffer(state)
    buf.set_task("narrative_author", provider="claude", model="opus")
    save_all(buf, env_local=env, palli_yaml=isolated_palli_yaml)
    doc = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    assert doc["llm"]["routing"]["narrative_author"] == {"provider": "claude", "model": "opus"}


def test_catalog_knows_all_seven_and_who_needs_a_key(isolated_palli_yaml):
    catalog = ProviderCatalog.load(isolated_palli_yaml)
    assert set(catalog.provider_names()) == set(CAPABILITIES)
    assert catalog.requires_api_key("local_openai") is False
    assert catalog.requires_api_key("claude") is False
    assert catalog.requires_api_key("anthropic") is True
    # Step 28 task 5: this named the owner's private Ollama tag (`qwen3.6-palli`), which
    # task 4 replaced with a public one in the shipped example — so it failed in every
    # clone. What matters is that the catalogue is READ FROM THE YAML rather than
    # hardcoded, which this pins exactly and without naming anyone's model.
    doc = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    assert [m.id for m in catalog.models_for("local_openai")] == \
        [m["id"] for m in doc["providers"]["local_openai"]["models"]]
    assert catalog.models_for("local_openai"), "the keyless local lane must offer a model"


@pytest.mark.asyncio
async def test_menu_rows_carry_plain_names_and_a_mark(isolated_env_local, isolated_palli_yaml):
    from palli_config.skins.menu import MenuApp
    app = MenuApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompts = [str(opt.prompt) for opt in app.query_one("#task-list").options]
        assert len(prompts) == 10
        assert prompts[0].startswith("✓ Her voice — the live conversation")
        assert any("What she keeps and recalls" in p for p in prompts)
        assert not any(p.lstrip("✓⚠✗ ").startswith("narrative_author") for p in prompts)
        providers = [str(opt.prompt) for opt in app.query_one("#provider-list").options]
        assert len(providers) == 7 and any(p.startswith("Claude Code (your subscription)") for p in providers)


def _write_env(tmp_path, text: str):
    env = tmp_path / ".env.local"
    env.write_text(text, encoding="utf-8")
    return env


def test_a_no_edit_save_leaves_the_yaml_response_row_alone(tmp_path, isolated_palli_yaml):
    """Ruling 19: her voice's yaml row is not the TUI's to rewrite from a resolved default —
    the cadence judge and the reply governor read it."""
    before = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))["llm"]["routing"]["response"]
    env = _write_env(tmp_path, "LLM_PROVIDER=deepseek\n")   # no LLM_RESPONSE_MODEL → picture says gpt-4o
    state = load_state(env_local=env, palli_yaml=isolated_palli_yaml)
    assert state.tasks["response"].model == "gpt-4o"
    save_all(ConfigBuffer(state), env_local=env, palli_yaml=isolated_palli_yaml)
    after = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))["llm"]["routing"]["response"]
    assert after == before


def test_a_no_edit_save_never_writes_a_half_row_for_an_optional_lane(tmp_path, isolated_palli_yaml):
    """Ruling 19 / F1: an absent or null optional lane stays absent and the file still validates."""
    from core.settings_service import SettingsService

    doc = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    doc["llm"]["routing"]["narrative_author"] = None
    del doc["llm"]["routing"]["appraisal"]
    isolated_palli_yaml.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    env = _write_env(tmp_path, "LLM_PROVIDER=deepseek\n")
    state = load_state(env_local=env, palli_yaml=isolated_palli_yaml)
    save_all(ConfigBuffer(state), env_local=env, palli_yaml=isolated_palli_yaml)
    routing = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))["llm"]["routing"]
    assert routing.get("narrative_author") is None and "appraisal" not in routing
    SettingsService(yaml_path=isolated_palli_yaml).load()   # still boots


def test_an_edited_optional_lane_is_written_whole(tmp_path, isolated_palli_yaml):
    doc = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    doc["llm"]["routing"]["narrative_author"] = None
    isolated_palli_yaml.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    env = _write_env(tmp_path, "LLM_PROVIDER=deepseek\nLLM_RESPONSE_MODEL=deepseek-v4-flash\n")
    state = load_state(env_local=env, palli_yaml=isolated_palli_yaml)
    buf = ConfigBuffer(state)
    buf.set_task("narrative_author", provider="claude", model="sonnet")
    save_all(buf, env_local=env, palli_yaml=isolated_palli_yaml)
    routing = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))["llm"]["routing"]
    assert routing["narrative_author"] == {"provider": "claude", "model": "sonnet"}


def test_a_present_but_empty_managed_key_survives_a_save(tmp_path, isolated_palli_yaml):
    """Ruling 19: core/config.py reads present-empty and absent differently; the TUI must not turn one into the other."""
    env = tmp_path / ".env.local"
    env.write_bytes(b"LLM_PROVIDER=deepseek\r\nLLM_RESPONSE_MODEL=deepseek-v4-flash\r\nLOCAL_LLM_MODEL=\r\n")
    state = load_state(env_local=env, palli_yaml=isolated_palli_yaml)
    save_all(ConfigBuffer(state), env_local=env, palli_yaml=isolated_palli_yaml)
    text = env.read_bytes().decode("utf-8")
    assert "LOCAL_LLM_MODEL=" in text and text.count("LOCAL_LLM_MODEL=") == 1


@pytest.mark.asyncio
async def test_dashboard_rows_carry_plain_names_and_a_mark(isolated_env_local, isolated_palli_yaml):
    from textual.widgets import DataTable

    from palli_config.skins.dashboard import DashboardApp
    app = DashboardApp(env_local=isolated_env_local, palli_yaml=isolated_palli_yaml)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#task-table", DataTable)
        first = [str(cell) for cell in table.get_row_at(0)]
        assert first[0].startswith("✓ Her voice — the live conversation")
