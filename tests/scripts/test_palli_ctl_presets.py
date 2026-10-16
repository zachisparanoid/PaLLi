"""R3 (spec §1.5 landmine 4): palli_ctl had four hardcoded presets that wrote env,
data/palli.yaml had six that wrote yaml, two names existed in both with different
contents, and the wizard had a fourth list. Now: one definition, the YAML."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    module_path = REPO_ROOT / "scripts" / "palli_ctl.py"
    spec = importlib.util.spec_from_file_location("palli_ctl_presets_test_module", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def controller(tmp_path):
    module = _load_module()
    ctl = module.LauncherController()
    yaml_copy = tmp_path / "palli.yaml"
    # The SHIPPED config, not his: data/palli.yaml exists in a clone only because
    # tests/conftest.py seeds it, which made this fixture depend on suite order
    # (the assembled public tree, 2026-09-18). The example ships; read that.
    _source = REPO_ROOT / "data" / "palli.yaml"
    if not _source.is_file():
        _source = REPO_ROOT / "data" / "palli.example.yaml"
    shutil.copyfile(_source, yaml_copy)
    ctl.palli_yaml_path = yaml_copy
    ctl.env_local_path = tmp_path / ".env.local"
    return module, ctl


def test_the_hardcoded_list_is_gone(controller):
    module, _ = controller
    assert not hasattr(module, "CONFIG_PRESET_CHOICES")
    assert not hasattr(module.LauncherController, "_provider_default_model")


def test_presets_come_from_the_yaml(controller):
    _, ctl = controller
    assert set(ctl._yaml_presets()) == {
        "deepseek-primary", "anthropic-primary", "openai-primary", "codex-primary",
        "claude-oauth", "ollama-primary", "local-only", "hybrid",
    }


def test_configure_updates_derive_from_the_preset(controller):
    _, ctl = controller
    updates, notes = ctl._build_configure_updates(preset="claude-oauth", codex_model=None, codex_profile=None)
    assert updates["LLM_PROVIDER"] == "claude" and updates["LLM_RESPONSE_MODEL"] == "sonnet"
    assert updates["CLAUDE_CLI_MODEL"] == "sonnet"
    assert updates["LLM_RESPONSE_PROVIDER"] == "claude" and updates["LLM_DECISION_MODEL"] == "sonnet"
    assert updates["LLM_VISION_PROVIDER"] == "claude" and updates["LLM_VISION_MODEL"] == "haiku"
    assert not any(k.startswith("LLM_NARRATIVE_AUTHOR") for k in updates)   # yaml-only lane


def test_codex_model_overrides_every_codex_lane(controller):
    _, ctl = controller
    updates, _ = ctl._build_configure_updates(preset="codex-primary", codex_model="gpt-5.3-codex", codex_profile="personal")
    assert updates["CODEX_MODEL"] == "gpt-5.3-codex" and updates["LLM_RESPONSE_MODEL"] == "gpt-5.3-codex"
    assert updates["LLM_MEMORY_MODEL"] == "gpt-5.3-codex" and updates["LLM_MEMORY_PROVIDER"] == "codex"
    assert updates["LLM_VISION_MODEL"] == "haiku"   # vision is on claude in that preset; untouched
    assert updates["CODEX_PROFILE"] == "personal"


def test_unknown_preset_is_refused(controller):
    _, ctl = controller
    with pytest.raises(RuntimeError, match="Unsupported preset"):
        ctl._build_configure_updates(preset="gemini-primary", codex_model=None, codex_profile=None)


def test_prereqs_follow_the_brain_providers_auth(controller, monkeypatch):
    module, ctl = controller
    monkeypatch.setattr(module.config, "OPENAI_API_KEY", "", raising=False)
    with pytest.raises(RuntimeError, match="requires OPENAI_API_KEY"):
        ctl._ensure_configure_prereqs(preset="openai-primary", non_interactive=True)
    monkeypatch.setattr(module.config, "OPENAI_API_KEY", "sk-test", raising=False)
    ctl._ensure_configure_prereqs(preset="openai-primary", non_interactive=True)
    monkeypatch.setattr(module.config, "LOCAL_LLM_API_BASE", "", raising=False)
    with pytest.raises(RuntimeError, match="requires LOCAL_LLM_API_BASE"):
        ctl._ensure_configure_prereqs(preset="local-only", non_interactive=True)


def test_infer_runtime_preset_reads_the_yaml(controller, monkeypatch):
    module, ctl = controller
    monkeypatch.setattr(module.config, "LLM_PROVIDER", "claude", raising=False)
    assert ctl._infer_runtime_preset() == "claude-oauth"
    monkeypatch.setattr(module.config, "LLM_PROVIDER", "anthropic", raising=False)
    assert ctl._infer_runtime_preset() == "anthropic-primary"
    monkeypatch.setattr(module.config, "LLM_PROVIDER", "gemini", raising=False)
    assert ctl._infer_runtime_preset() == "deepseek-primary"


def test_sync_applies_the_whole_preset_to_the_yaml(controller, monkeypatch):
    module, ctl = controller
    proposed = {}

    class FakeService:
        def snapshot(self):
            return ctl._yaml_settings()
        def propose(self, changes, *, reason=""):
            proposed.update(changes)
            return type("P", (), {"change_id": "chg_test", "diff": [{"path": k, "old": None, "new": v} for k, v in changes.items()]})()
        def apply(self, change_id):
            return type("A", (), {"applied": True, "rollback_token": "rb_test"})()
        _yaml_path = ctl.palli_yaml_path

    import core.settings_boot as boot
    monkeypatch.setattr(boot, "settings_service", FakeService())
    monkeypatch.setattr(boot, "boot_settings", lambda **kw: None)
    ctl._selected_models_preset = "claude-oauth"
    message = ctl._sync_structured_settings({"LLM_PROVIDER": "claude"})
    assert "Synced" in message
    assert proposed["llm.routing.narrative_author"] == {"provider": "claude", "model": "sonnet"}
    assert proposed["llm.routing.appraisal"] == {"provider": "claude", "model": "sonnet"}


def test_parser_accepts_any_preset_name_and_the_controller_validates():
    module = _load_module()
    args = module.build_parser().parse_args(["configure", "--preset", "hybrid"])
    assert args.preset == "hybrid"


def test_sync_keeps_the_runs_codex_model_in_the_yaml_half(controller, monkeypatch):
    """Ruling 21: the router reads the yaml first, so --codex-model must reach it, not only .env.local."""
    module, ctl = controller
    proposed = {}

    class FakeService:
        def snapshot(self):
            return ctl._yaml_settings()
        def propose(self, changes, *, reason=""):
            proposed.update(changes)
            return type("P", (), {"change_id": "chg_test", "diff": []})()
        def apply(self, change_id):
            return type("A", (), {"applied": True, "rollback_token": "rb_test"})()
        _yaml_path = ctl.palli_yaml_path

    import core.settings_boot as boot
    monkeypatch.setattr(boot, "settings_service", FakeService())
    monkeypatch.setattr(boot, "boot_settings", lambda **kw: None)
    updates, _ = ctl._build_configure_updates(preset="codex-primary", codex_model="gpt-5.1-codex-max", codex_profile=None)
    ctl._selected_models_preset = "codex-primary"
    ctl._sync_structured_settings(updates)
    assert proposed["llm.routing.memory"] == {"provider": "codex", "model": "gpt-5.1-codex-max"}
    assert proposed["llm.routing.response"] == {"provider": "codex", "model": "gpt-5.1-codex-max"}
    assert proposed["llm.routing.vision"] == {"provider": "claude", "model": "haiku"}      # untouched: a different provider
    assert proposed["llm.routing.narrative_author"] == {"provider": "codex", "model": "gpt-5.5"}  # yaml-only lane keeps the preset row
    assert proposed["llm.default_provider"] == "codex"


def test_configure_warns_when_the_preset_would_lame_her_voice(controller):
    """Ruling 22: configure does not refuse (the operator onboarding default is codex-primary), but it says so."""
    _, ctl = controller
    _, notes = ctl._build_configure_updates(preset="claude-oauth", codex_model=None, codex_profile=None)
    assert any("WARNING" in n and "tool calls" in n and "palli_ctl.py brain claude-oauth --except response" in n for n in notes)
    _, notes = ctl._build_configure_updates(preset="openai-primary", codex_model=None, codex_profile=None)
    assert not any("WARNING" in n for n in notes)


def test_unsupported_preset_lists_the_valid_names(controller):
    _, ctl = controller
    with pytest.raises(RuntimeError, match="Presets: .*deepseek-primary"):
        ctl._build_configure_updates(preset="gemini-primary", codex_model=None, codex_profile=None)
