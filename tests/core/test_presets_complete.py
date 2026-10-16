"""R2b (configurator spec 2026-09-03 §1.5 landmine 4b): every preset is a whole
answer to "who is thinking for her". Measured at b81fd28 against the ten live
lanes: hybrid 8 · claude-oauth 4 · ollama-primary 3 · codex-primary 2 ·
openai-primary 1 · local-only 0. A canary over the LIVE file, so a partial
preset can never land again."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.llm_capabilities import BRAIN_LANE, PROVIDERS
from core.settings_schema import PalliSettings, RoutingConfig
from core.settings_service import SettingsService

REPO_ROOT = Path(__file__).resolve().parents[2]
LANES = tuple(RoutingConfig.model_fields)


@pytest.fixture(scope="module")
def live() -> PalliSettings:
    return SettingsService(yaml_path=REPO_ROOT / "data" / "palli.yaml").load()


def _lanes_named(preset) -> set[str]:
    return {key.split(".")[-1] for key in preset.overrides if key.startswith("llm.routing.")}


def test_every_preset_names_every_lane(live):
    for name, preset in live.presets.items():
        missing = sorted(set(LANES) - _lanes_named(preset))
        assert not missing, f"preset {name!r} leaves untouched: {missing}"


def test_every_preset_carries_its_env_half(live):
    for name, preset in live.presets.items():
        response = preset.overrides[f"llm.routing.{BRAIN_LANE}"]
        assert preset.env.get("LLM_PROVIDER") == response["provider"], name
        assert preset.env.get("LLM_RESPONSE_MODEL") == response["model"], name
        own_key = PROVIDERS[response["provider"]].model_env_key
        assert preset.env.get(own_key) == response["model"], f"{name}: {own_key} must agree with LLM_RESPONSE_MODEL"


def test_the_way_home_exists(live):
    """deepseek-primary mirrors what she runs today: DeepSeek everywhere except the
    two lanes deliberately on the Claude subscription (spec §3.3 R2b)."""
    p = live.presets["deepseek-primary"]
    assert p.overrides["llm.routing.response"] == {"provider": "deepseek", "model": "deepseek-v4-flash"}
    assert p.overrides["llm.routing.narrative_author"]["provider"] == "claude"
    assert p.overrides["llm.routing.vision"]["provider"] == "claude"


def test_the_eight_presets_are_all_there(live):
    assert set(live.presets) == {
        "deepseek-primary", "anthropic-primary", "openai-primary", "codex-primary",
        "claude-oauth", "ollama-primary", "local-only", "hybrid",
    }


def test_claude_5_models_are_listed_under_anthropic(live):
    ids = {m.id for m in live.providers["anthropic"].models}
    assert {"claude-opus-5", "claude-sonnet-5"} <= ids
    assert live.presets["anthropic-primary"].overrides["llm.routing.response"]["model"] == "claude-opus-5"
    assert live.presets["hybrid"].overrides["llm.routing.response"]["model"] == "claude-opus-5"


def test_local_only_actually_moves_her_thinking_lanes(live):
    p = live.presets["local-only"]
    assert p.overrides["llm.routing.response"]["provider"] == "local_openai"
    assert p.env["LLM_PROVIDER"] == "local_openai"
    assert p.overrides["voice.stt_provider"] == "faster_whisper"  # the old two keys stay
