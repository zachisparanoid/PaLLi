"""The capability matrix (spec 2026-09-03 §3.1) — a gate, deterministic on purpose.

Truth table first: every provider against the three lanes that differ
(her voice needs real tool calls, vision needs eyes, a judge needs only
text). Then the vocabulary: nothing enters the matrix without a plain
English name, because both config surfaces print from it (§3.6)."""
from __future__ import annotations

import pytest

from core.llm_capabilities import (
    BRAIN_ENV_KEYS,
    BRAIN_LANE,
    LANES,
    PROVIDERS,
    Auth,
    Dialect,
    Governance,
    Level,
    ToolCalls,
    check_route,
    env_key_allowed_in_preset,
    suggest_brain_provider,
)


# --- the truth table ---------------------------------------------------------

@pytest.mark.parametrize("provider", ["deepseek", "openai", "local_openai"])
def test_native_openai_wire_providers_may_drive_her_brain(provider):
    v = check_route(provider, BRAIN_LANE)
    assert v.level is Level.OK, v.reason


@pytest.mark.parametrize("provider", ["claude", "codex", "ollama"])
def test_text_tag_emulators_are_blocked_from_her_brain(provider):
    """The path that measured 0 tool calls on 2026-09-02 (spec §1.3)."""
    v = check_route(provider, BRAIN_LANE)
    assert v.blocked
    assert "tool call" in v.reason
    assert "2026-09-02" in v.reason
    assert v.suggestion in PROVIDERS


def test_anthropic_is_blocked_from_her_brain_until_the_parked_build_lands():
    v = check_route("anthropic", BRAIN_LANE)
    assert v.blocked
    assert "docs/superpowers/plans/2026-09-02-anthropic-brain-kickoff.md" in v.reason


def test_ollama_refusal_points_at_the_same_box_through_its_other_door():
    """ollama and local_openai are one machine on opposite dispatch paths (§1.3)."""
    v = check_route("ollama", BRAIN_LANE)
    assert v.suggestion == "local_openai"
    assert "same machine" in v.reason


@pytest.mark.parametrize("provider", ["deepseek", "anthropic", "codex", "local_openai"])
def test_providers_without_eyes_warn_on_the_vision_lane(provider):
    v = check_route(provider, "vision")
    assert v.level is Level.WARN
    assert "pictures" in v.reason


@pytest.mark.parametrize("provider", ["openai", "claude", "ollama"])
def test_providers_with_eyes_are_fine_on_the_vision_lane(provider):
    assert check_route(provider, "vision").level is Level.OK


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_every_provider_may_run_a_text_only_judge(provider):
    assert check_route(provider, "decision").level is Level.OK


def test_unknown_provider_and_unknown_lane_block_with_a_reason():
    assert check_route("gemini", "decision").blocked
    assert "gemini" in check_route("gemini", "decision").reason
    assert check_route("deepseek", "dreams").blocked


def test_brain_lane_warns_when_no_model_is_named_anywhere():
    """DEFECT_RECORD §22, as the runtime actually behaves (core/config.py + dispatch.py:211):
    LLM_RESPONSE_MODEL ABSENT → config defaults it to OPENAI_MODEL and her voice runs gpt-4o on
    whatever provider is set — WARN, whatever the provider's own key says. PRESENT BUT EMPTY →
    the runner falls to the provider's own key — OK when that key is set (R5, Task 3)."""
    assert check_route("local_openai", BRAIN_LANE, env={}).level is Level.WARN
    assert check_route("local_openai", BRAIN_LANE, env={"LOCAL_LLM_MODEL": "qwen3.6-palli"}).level is Level.WARN
    assert check_route("local_openai", BRAIN_LANE, env={"LLM_RESPONSE_MODEL": "qwen3.6-palli"}).level is Level.OK
    assert check_route("local_openai", BRAIN_LANE, env={"LLM_RESPONSE_MODEL": "", "LOCAL_LLM_MODEL": "qwen3.6-palli"}).level is Level.OK
    assert check_route("local_openai", BRAIN_LANE, env={"LLM_RESPONSE_MODEL": ""}).level is Level.WARN
    assert check_route("openai", BRAIN_LANE, env={}).level is Level.OK  # the OpenAI default IS its own model
    assert "OpenAI default" in check_route("deepseek", BRAIN_LANE, env={}).reason


def test_suggest_brain_provider_prefers_the_same_cost_kind():
    assert suggest_brain_provider("ollama") == "local_openai"      # free -> free
    assert suggest_brain_provider("claude") == "deepseek"          # no subscription provider can drive her brain
    assert suggest_brain_provider("anthropic") in ("deepseek", "openai")


# --- the rows themselves -----------------------------------------------------

def test_the_brain_lane_is_the_only_env_governed_lane_and_the_only_tool_lane():
    env_only = [name for name, lane in LANES.items() if lane.governed_by is Governance.ENV_ONLY]
    tool_lanes = [name for name, lane in LANES.items() if lane.needs_tool_calls]
    assert env_only == [BRAIN_LANE]
    assert tool_lanes == [BRAIN_LANE]
    assert [name for name, lane in LANES.items() if lane.needs_vision] == ["vision"]


def test_first_lane_is_her_voice():
    assert next(iter(LANES)) == BRAIN_LANE


def test_dialects_match_what_dispatch_does_by_auth():
    """Keyless providers ride the text-tag adapter except local_openai; keyed
    ones ride AsyncOpenAI — which is wrong for anthropic (spec §1.3)."""
    assert PROVIDERS["local_openai"].auth is Auth.NONE
    assert PROVIDERS["local_openai"].runner_dialect is Dialect.OPENAI_COMPAT
    assert PROVIDERS["ollama"].runner_dialect is Dialect.SDK_ADAPTER
    assert PROVIDERS["claude"].runner_dialect is Dialect.SDK_ADAPTER
    assert PROVIDERS["codex"].runner_dialect is Dialect.SDK_ADAPTER
    assert PROVIDERS["anthropic"].runner_dialect is Dialect.UNSUPPORTED
    assert PROVIDERS["anthropic"].tool_calls is ToolCalls.NATIVE


# --- vocabulary (§3.6 rule 2) ------------------------------------------------

@pytest.mark.parametrize("name", list(PROVIDERS))
def test_every_provider_has_a_plain_name_a_cost_line_and_a_cost_kind(name):
    cap = PROVIDERS[name]
    assert cap.plain_name.strip() and cap.plain_name != name
    assert cap.cost.strip()
    assert cap.cost_kind in {"per_token", "subscription", "free"}
    assert cap.model_env_key.isupper() and cap.model_env_key.endswith("_MODEL")


@pytest.mark.parametrize("name", list(LANES))
def test_every_lane_has_a_plain_name(name):
    assert LANES[name].plain_name.strip() and LANES[name].plain_name != name


@pytest.mark.parametrize("name", list(PROVIDERS))
def test_blocked_providers_carry_a_note_for_the_refusal(name):
    cap = PROVIDERS[name]
    if cap.tool_calls is not ToolCalls.NATIVE or cap.runner_dialect is Dialect.UNSUPPORTED:
        assert cap.note.strip()


# --- the preset env allowlist (§3.3 R2) --------------------------------------

def test_allowlist_is_exactly_the_brain_keys_and_the_seven_model_keys():
    expected = {"LLM_PROVIDER", "LLM_RESPONSE_MODEL", "CADENCE_MODEL"} | {
        cap.model_env_key for cap in PROVIDERS.values()
    }
    assert BRAIN_ENV_KEYS == frozenset(expected)
    assert len({cap.model_env_key for cap in PROVIDERS.values()}) == 7


@pytest.mark.parametrize("key", ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DISCORD_TOKEN", "X_SECRET", "DB_PASSWORD"])
def test_credential_shaped_keys_are_refused_by_name(key):
    assert env_key_allowed_in_preset(key) is False


@pytest.mark.parametrize("key", ["LLM_PROVIDER", "LLM_RESPONSE_MODEL", "CADENCE_MODEL", "CLAUDE_CLI_MODEL", "LOCAL_LLM_MODEL"])
def test_brain_keys_are_allowed(key):
    assert env_key_allowed_in_preset(key) is True


def test_unlisted_harmless_keys_are_still_refused():
    assert env_key_allowed_in_preset("BACKEND_PORT") is False
    assert env_key_allowed_in_preset("llm_provider") is False
