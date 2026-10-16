"""The picture (spec §3.4): her voice reads .env.local, every other lane reads
data/palli.yaml first. Resolved from the files, never from core.config, so the
answer cannot depend on the calling process."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.llm_capabilities import BRAIN_LANE, Level
from core.settings_service import SettingsService
from palli_config import brain
from palli_config.brain import (
    ENV_LANE_MODEL_KEYS,
    ENV_LANE_PROVIDER_KEYS,
    ROUTING_LANES,
    BrainError,
    load_picture,
    resolve_target,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(tmp_path: Path, text: str, name: str = ".env.local") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _picture(tmp_path, palli_yaml, env_text, base_text=None):
    env_local = _env(tmp_path, env_text)
    env_base = _env(tmp_path, base_text, ".env") if base_text is not None else None
    return load_picture(env_local=env_local, palli_yaml=palli_yaml, env_base=env_base)


# --- her voice -----------------------------------------------------------------

def test_brain_lane_reads_llm_provider_and_response_model_not_the_yaml_row(tmp_path, isolated_palli_yaml):
    doc = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    assert doc["llm"]["routing"]["response"]["provider"] != "openai"
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=openai\nLLM_RESPONSE_MODEL=gpt-4o\n")
    assert pic.brain.pair == ("openai", "gpt-4o")
    assert pic.brain.source == "env"
    assert pic.brain.verdict.level is Level.OK


def test_brain_lane_falls_to_the_code_default_when_unset_and_warns_without_a_model(tmp_path, isolated_palli_yaml):
    pic = _picture(tmp_path, isolated_palli_yaml, "")
    assert pic.brain.provider == "deepseek"
    assert pic.brain.source == "code_default"
    assert pic.brain.model == brain.CODE_DEFAULT_OPENAI_MODEL   # the §22 shape: gpt-4o on DeepSeek, shown, not hidden
    assert pic.brain.verdict.level is Level.WARN   # DEFECT_RECORD §22


def test_brain_model_absent_means_the_openai_default_present_but_empty_means_the_providers_own_key(tmp_path, isolated_palli_yaml):
    """core/config.py: LLM_RESPONSE_MODEL = os.getenv("LLM_RESPONSE_MODEL", OPENAI_MODEL). An ABSENT
    key is gpt-4o on whatever provider is set (DEFECT_RECORD §22); only a PRESENT-BUT-EMPTY key lets
    the runner fall to the provider's own key (_default_runner_model). The picture says which."""
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=local_openai\nLOCAL_LLM_MODEL=qwen3.6-palli\n")
    assert pic.brain.pair == ("local_openai", "gpt-4o")
    assert pic.brain.verdict.level is Level.WARN
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=local_openai\nLLM_RESPONSE_MODEL=\nLOCAL_LLM_MODEL=qwen3.6-palli\n")
    assert pic.brain.pair == ("local_openai", "qwen3.6-palli")
    assert pic.brain.verdict.level is Level.OK
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=local_openai\nOPENAI_MODEL=gpt-4o-mini\n")
    assert pic.brain.pair == ("local_openai", "gpt-4o-mini")   # the file's own OPENAI_MODEL beats the code default


def test_code_default_openai_model_matches_core_config():
    text = (REPO_ROOT / "core" / "config.py").read_text(encoding="utf-8", errors="replace")
    assert f'os.getenv("OPENAI_MODEL", "{brain.CODE_DEFAULT_OPENAI_MODEL}")' in text


def test_env_local_overrides_the_base_env_file(tmp_path, isolated_palli_yaml):
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=deepseek\n", base_text="LLM_PROVIDER=openai\nOPENAI_MODEL=gpt-4o\n")
    assert pic.brain.provider == "deepseek"
    assert pic.env["OPENAI_MODEL"] == "gpt-4o"   # base keys still visible


def test_code_default_matches_core_config():
    text = (REPO_ROOT / "core" / "config.py").read_text(encoding="utf-8", errors="replace")
    assert f'os.getenv("LLM_PROVIDER", "{brain.CODE_DEFAULT_PROVIDER}")' in text


# --- every other lane -----------------------------------------------------------

def test_routing_lane_prefers_yaml_then_env_lane_then_llm_provider(tmp_path, isolated_palli_yaml):
    doc = yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))
    expected = doc["llm"]["routing"]["decision"]
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=openai\nLLM_DECISION_PROVIDER=openai\nLLM_DECISION_MODEL=gpt-4o\n")
    assert pic.lanes["decision"].pair == (expected["provider"], expected["model"])
    assert pic.lanes["decision"].source == "yaml"

    del doc["llm"]["routing"]["appraisal"]   # optional lane: absent is legal
    stripped = tmp_path / "stripped.yaml"
    stripped.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    pic = _picture(tmp_path, stripped, "LLM_PROVIDER=deepseek\nDEEPSEEK_MODEL=deepseek-v4-flash\nLLM_APPRAISAL_PROVIDER=openai\nLLM_APPRAISAL_MODEL=gpt-4o\n")
    assert pic.lanes["appraisal"].pair == ("openai", "gpt-4o") and pic.lanes["appraisal"].source == "env_lane"
    pic = _picture(tmp_path, stripped, "LLM_PROVIDER=deepseek\nDEEPSEEK_MODEL=deepseek-v4-flash\n")
    assert pic.lanes["appraisal"].pair == ("deepseek", "deepseek-v4-flash") and pic.lanes["appraisal"].source == "env_default"


def test_picture_has_every_lane_in_schema_order_with_her_voice_first(tmp_path, isolated_palli_yaml):
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=deepseek\n")
    assert list(pic.lanes) == list(ROUTING_LANES)
    assert ROUTING_LANES[0] == BRAIN_LANE and len(ROUTING_LANES) == 10


def test_cadence_and_default_provider_are_read(tmp_path, isolated_palli_yaml):
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=deepseek\nCADENCE_MODEL=claude::haiku\n")
    assert pic.cadence_model == "claude::haiku"
    assert pic.default_provider == yaml.safe_load(isolated_palli_yaml.read_text(encoding="utf-8"))["llm"]["default_provider"]


def test_env_lane_keys_match_the_runtime_tables():
    from ai.llm_routing import TASK_MODEL_FIELDS, TASK_PROVIDER_FIELDS
    assert ENV_LANE_PROVIDER_KEYS == TASK_PROVIDER_FIELDS
    assert ENV_LANE_MODEL_KEYS == {k: v for k, v in TASK_MODEL_FIELDS.items() if k != "present_moment"}


# --- the target -----------------------------------------------------------------

def _settings(palli_yaml):
    return SettingsService(yaml_path=palli_yaml).load()


def test_a_preset_covers_all_ten_lanes_and_carries_the_env_half(tmp_path, isolated_palli_yaml):
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=claude\nLLM_RESPONSE_MODEL=sonnet\n")
    target = resolve_target(pic, _settings(isolated_palli_yaml), preset="deepseek-primary")
    assert target.name == "deepseek-primary"
    assert set(target.lanes) == set(ROUTING_LANES)
    assert target.lanes["response"] == ("deepseek", "deepseek-v4-flash")
    assert target.env_updates == {"LLM_PROVIDER": "deepseek", "LLM_RESPONSE_MODEL": "deepseek-v4-flash", "DEEPSEEK_MODEL": "deepseek-v4-flash"}
    assert target.yaml_overrides["llm.routing.response"] == {"provider": "deepseek", "model": "deepseek-v4-flash"}
    assert target.yaml_overrides["llm.default_provider"] == "deepseek"
    assert "response" in target.moving(pic)
    assert target.touches_brain()


def test_except_response_leaves_her_voice_alone(tmp_path, isolated_palli_yaml):
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=deepseek\nLLM_RESPONSE_MODEL=deepseek-v4-flash\n")
    target = resolve_target(pic, _settings(isolated_palli_yaml), preset="claude-oauth", except_lanes=["response"])
    assert target.lanes["response"] == pic.brain.pair
    assert "llm.routing.response" not in target.yaml_overrides
    assert "LLM_PROVIDER" not in target.env_updates and "LLM_RESPONSE_MODEL" not in target.env_updates
    assert target.env_updates == {}   # Ruling 11: her voice is not moving, so .env.local is not touched at all
    assert target.yaml_overrides["llm.default_provider"] == "claude"  # advisory row still follows the preset
    assert "response" not in target.moving(pic)
    assert not target.touches_brain()
    assert target.excepted == ("response",)


def test_pins_win_over_the_preset(tmp_path, isolated_palli_yaml):
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=deepseek\nLLM_RESPONSE_MODEL=deepseek-v4-flash\n")
    target = resolve_target(pic, _settings(isolated_palli_yaml), preset="openai-primary", pins={"vision": ("claude", "haiku")})
    assert target.lanes["vision"] == ("claude", "haiku")
    assert target.yaml_overrides["llm.routing.vision"] == {"provider": "claude", "model": "haiku"}
    assert target.lanes["decision"] == ("openai", "gpt-4o")


def test_a_pin_on_her_voice_writes_both_halves(tmp_path, isolated_palli_yaml):
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=deepseek\nLLM_RESPONSE_MODEL=deepseek-v4-flash\n")
    target = resolve_target(pic, _settings(isolated_palli_yaml), pins={"response": ("openai", "gpt-4o")})
    assert target.name == "pin"
    assert target.env_updates == {"LLM_PROVIDER": "openai", "LLM_RESPONSE_MODEL": "gpt-4o", "OPENAI_MODEL": "gpt-4o"}
    assert target.yaml_overrides == {"llm.routing.response": {"provider": "openai", "model": "gpt-4o"}}
    assert target.moving(pic) == ["response"]


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"preset": "gemini-primary"}, "not a preset"),
        ({"preset": "deepseek-primary", "except_lanes": ["dreams"]}, "not one of her lanes"),
        ({"pins": {"vision": ("gemini", "pro")}}, "not a provider PaLLi knows"),
        ({"pins": {"vision": ("claude", "")}}, "name a model"),
        ({}, "nothing to do"),
    ],
)
def test_bad_requests_raise_a_plain_english_brain_error(tmp_path, isolated_palli_yaml, kwargs, fragment):
    pic = _picture(tmp_path, isolated_palli_yaml, "LLM_PROVIDER=deepseek\n")
    with pytest.raises(BrainError) as err:
        resolve_target(pic, _settings(isolated_palli_yaml), **kwargs)
    assert fragment in str(err.value)
