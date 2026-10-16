from __future__ import annotations

from pathlib import Path

import pytest
import yaml as _yaml
from pydantic import ValidationError

from core.settings_schema import (
    ChannelEntry,
    ChannelsConfig,
    DiscoveryMode,
    FeaturesConfig,
    InteractionMode,
    LLMConfig,
    LocalTuiMode,
    ModelDefinition,
    OperatorPolicy,
    PalliSettings,
    PolicyConfig,
    PresetDefinition,
    ProactivePolicy,
    ProviderConfig,
    RoutingEntry,
    STTProvider,
    SecretRef,
    SecretSource,
    TTSProvider,
    VoiceConfig,
    VoiceEngine,
)


def test_secretref_parses_env_dict():
    ref = SecretRef.model_validate({"source": "env", "name": "OPENAI_API_KEY"})
    assert ref.source is SecretSource.ENV
    assert ref.name == "OPENAI_API_KEY"


def test_secretref_rejects_raw_string():
    with pytest.raises(ValidationError):
        SecretRef.model_validate("sk-abc123")


def test_secretref_rejects_unknown_source():
    with pytest.raises(ValidationError):
        SecretRef.model_validate({"source": "vault", "name": "X"})


def test_discovery_mode_accepts_curated_and_api():
    assert DiscoveryMode("curated") is DiscoveryMode.CURATED
    assert DiscoveryMode("api") is DiscoveryMode.API


def test_model_definition_parses_minimal():
    m = ModelDefinition.model_validate({"id": "gpt-4o"})
    assert m.id == "gpt-4o"
    assert m.display_name == "gpt-4o"  # defaults to id
    assert m.context_window is None
    assert m.supports_vision is False
    assert m.supports_reasoning is False


def test_model_definition_parses_full():
    m = ModelDefinition.model_validate(
        {
            "id": "gpt-5.1-codex-max",
            "display_name": "Codex Max",
            "context_window": 200000,
            "supports_vision": False,
            "supports_reasoning": True,
        }
    )
    assert m.context_window == 200000
    assert m.supports_reasoning is True


def test_provider_config_curated_requires_models():
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate(
            {"enabled": True, "discovery": "curated", "models": []}
        )


def test_provider_config_api_allows_empty_models():
    p = ProviderConfig.model_validate(
        {"enabled": True, "discovery": "api", "models": []}
    )
    assert p.discovery is DiscoveryMode.API
    assert p.models == []


def test_provider_config_api_key_ref_roundtrip():
    p = ProviderConfig.model_validate(
        {
            "enabled": True,
            "discovery": "curated",
            "api_key_ref": {"source": "env", "name": "OPENAI_API_KEY"},
            "models": [{"id": "gpt-4o"}],
        }
    )
    assert p.api_key_ref is not None
    assert p.api_key_ref.name == "OPENAI_API_KEY"


def test_routing_entry_parses():
    r = RoutingEntry.model_validate({"provider": "openai", "model": "gpt-4o"})
    assert r.provider == "openai"
    assert r.model == "gpt-4o"


def test_llm_config_parses_all_tasks():
    cfg = LLMConfig.model_validate(
        {
            "default_provider": "openai",
            "routing": {
                "response":   {"provider": "openai", "model": "gpt-4o"},
                "decision":   {"provider": "deepseek", "model": "deepseek-chat"},
                "analysis":   {"provider": "deepseek", "model": "deepseek-chat"},
                "memory":     {"provider": "deepseek", "model": "deepseek-chat"},
                "proactive":  {"provider": "openai", "model": "gpt-4o"},
                "reflection": {"provider": "openai", "model": "gpt-4o"},
                "web_search": {"provider": "openai", "model": "gpt-4o"},
                "vision":     {"provider": "openai", "model": "gpt-4o"},
            },
        }
    )
    assert cfg.routing.response.provider == "openai"
    assert cfg.routing.decision.model == "deepseek-chat"


def test_llm_config_rejects_missing_task():
    with pytest.raises(ValidationError):
        LLMConfig.model_validate(
            {
                "default_provider": "openai",
                "routing": {"response": {"provider": "openai", "model": "gpt-4o"}},
            }
        )


def test_voice_config_default_shape():
    cfg = VoiceConfig.model_validate(
        {
            "engine": "classic",
            "stt_provider": "deepgram",
            "tts_provider": "deepgram",
            "interaction_mode": "direct_address",
            "local_tui": {"enabled": True, "default_mode": "push_to_talk"},
        }
    )
    assert cfg.engine is VoiceEngine.CLASSIC
    assert cfg.stt_provider is STTProvider.DEEPGRAM
    assert cfg.local_tui.default_mode is LocalTuiMode.PUSH_TO_TALK
    assert cfg.interaction_mode is InteractionMode.DIRECT_ADDRESS


def test_voice_config_rejects_unknown_engine():
    with pytest.raises(ValidationError):
        VoiceConfig.model_validate(
            {
                "engine": "whisper_cloud",
                "stt_provider": "deepgram",
                "tts_provider": "deepgram",
                "interaction_mode": "direct_address",
                "local_tui": {"enabled": False, "default_mode": "quiet"},
            }
        )


def test_features_config_defaults():
    c = FeaturesConfig()
    assert c.proactive_conversations is True
    assert c.home_assistant is False


def test_channels_config_parses():
    c = ChannelsConfig.model_validate(
        {
            "discord": {"enabled": True},
            "telegram": {"enabled": False},
            "slack": {"enabled": False},
        }
    )
    assert c.discord.enabled is True
    assert c.telegram.enabled is False


def test_policy_config_parses():
    c = PolicyConfig.model_validate(
        {
            "proactive": {"max_per_day": 3, "min_gap_hours": 2},
            "operator": {"browser_enabled": True, "desktop_enabled": True},
        }
    )
    assert c.proactive.max_per_day == 3
    assert c.operator.browser_enabled is True


def test_policy_rejects_negative_cap():
    with pytest.raises(ValidationError):
        ProactivePolicy.model_validate({"max_per_day": -1, "min_gap_hours": 0})


def test_preset_accepts_dot_paths():
    p = PresetDefinition.model_validate(
        {
            "llm.default_provider": "codex",
            "llm.routing.response": {"provider": "codex", "model": "gpt-5.1-codex-max"},
        }
    )
    assert p.overrides["llm.default_provider"] == "codex"
    assert p.overrides["llm.routing.response"]["provider"] == "codex"


def test_preset_rejects_empty():
    with pytest.raises(ValidationError):
        PresetDefinition.model_validate({})


def test_preset_rejects_non_dot_key():
    with pytest.raises(ValidationError):
        PresetDefinition.model_validate({"!not a key!": "value"})


def _base_payload() -> dict:
    return {
        "version": 1,
        "providers": {
            "openai": {
                "enabled": True,
                "discovery": "curated",
                "api_key_ref": {"source": "env", "name": "OPENAI_API_KEY"},
                "models": [{"id": "gpt-4o"}],
            },
            "deepseek": {
                "enabled": True,
                "discovery": "curated",
                "api_key_ref": {"source": "env", "name": "DEEPSEEK_API_KEY"},
                "models": [{"id": "deepseek-chat"}],
            },
        },
        "llm": {
            "default_provider": "openai",
            "routing": {
                "response":   {"provider": "openai",   "model": "gpt-4o"},
                "decision":   {"provider": "deepseek", "model": "deepseek-chat"},
                "analysis":   {"provider": "deepseek", "model": "deepseek-chat"},
                "memory":     {"provider": "deepseek", "model": "deepseek-chat"},
                "proactive":  {"provider": "openai",   "model": "gpt-4o"},
                "reflection": {"provider": "openai",   "model": "gpt-4o"},
                "web_search": {"provider": "openai",   "model": "gpt-4o"},
                "vision":     {"provider": "openai",   "model": "gpt-4o"},
            },
        },
        "voice": {
            "engine": "classic",
            "stt_provider": "deepgram",
            "tts_provider": "deepgram",
            "interaction_mode": "direct_address",
            "local_tui": {"enabled": True, "default_mode": "push_to_talk"},
        },
        "features": {},
        "channels": {},
        "policy": {
            "proactive": {"max_per_day": 3, "min_gap_hours": 2},
            "operator": {"browser_enabled": True, "desktop_enabled": True},
        },
        "presets": {},
    }


def test_palli_settings_full_roundtrip():
    s = PalliSettings.model_validate(_base_payload())
    assert s.version == 1
    assert s.llm.default_provider == "openai"
    assert "openai" in s.providers


def test_palli_settings_routing_references_unknown_provider():
    payload = _base_payload()
    payload["llm"]["routing"]["response"] = {"provider": "anthropic", "model": "claude-opus-4-6"}
    with pytest.raises(ValidationError) as err:
        PalliSettings.model_validate(payload)
    assert "anthropic" in str(err.value)


def test_palli_settings_default_provider_must_exist():
    payload = _base_payload()
    payload["llm"]["default_provider"] = "anthropic"
    with pytest.raises(ValidationError):
        PalliSettings.model_validate(payload)


def test_palli_settings_routing_references_unknown_model():
    payload = _base_payload()
    payload["llm"]["routing"]["response"]["model"] = "gpt-99"
    with pytest.raises(ValidationError) as err:
        PalliSettings.model_validate(payload)
    assert "gpt-99" in str(err.value)


def test_palli_settings_version_required():
    payload = _base_payload()
    del payload["version"]
    with pytest.raises(ValidationError):
        PalliSettings.model_validate(payload)


def test_seed_palli_yaml_validates():
    path = Path(__file__).resolve().parents[2] / "data" / "palli.yaml"
    assert path.exists(), "data/palli.yaml must be shipped as the default seed"
    raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    settings = PalliSettings.model_validate(raw)
    assert settings.version == 1
    # Structural sanity only — provider enablement and the default
    # provider are owner-tuned in the live yaml (e.g. 2026-06 revert to
    # the DeepSeek brain) and must not be value-pinned here.
    assert "ollama" in settings.providers
    assert "openai" in settings.providers
    assert settings.providers["codex"].discovery is DiscoveryMode.CURATED
    assert settings.llm.default_provider in settings.providers
    # Channels default to whatever the current env enables; only discord is on by default
    assert settings.channels.discord.enabled is True


def test_companion_introspection_ships_default_off():
    """The introspect tool (Selfhood evidence layer) must be flag-off pure:
    schema default False, unbooted settings mean OFF — only the owner's
    palli.yaml turns it on. Spec: 2026-07-13-introspection-tool-v1-design."""
    from core.settings_schema import CompanionConfig

    assert CompanionConfig().introspection is False


def test_tts_provider_lemonfox_is_valid():
    """lemonfox joined the voice TTS enum 2026-07-14 (Cartesia credit-outage
    fallback); the live yaml points at it, so validation must accept it."""
    from core.settings_schema import TTSProvider

    assert TTSProvider("lemonfox") is TTSProvider.LEMONFOX


# --- presets carry an env half (configurator spec 2026-09-03 §3.3 R2) -------

def test_preset_env_half_accepts_brain_keys():
    p = PresetDefinition.model_validate(
        {
            "overrides": {"llm.routing.response": {"provider": "deepseek", "model": "deepseek-v4-flash"}},
            "env": {"LLM_PROVIDER": "deepseek", "LLM_RESPONSE_MODEL": "deepseek-v4-flash", "CADENCE_MODEL": ""},
        }
    )
    assert p.env["LLM_PROVIDER"] == "deepseek"
    assert p.env["CADENCE_MODEL"] == ""


def test_preset_env_defaults_to_empty_and_flat_form_still_means_overrides():
    p = PresetDefinition.model_validate({"llm.default_provider": "codex"})
    assert p.env == {}
    assert p.overrides == {"llm.default_provider": "codex"}


@pytest.mark.parametrize("key", ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DISCORD_BOT_TOKEN", "SOME_SECRET"])
def test_preset_env_refuses_credentials_by_name(key):
    with pytest.raises(ValidationError) as err:
        PresetDefinition.model_validate(
            {"overrides": {"llm.default_provider": "openai"}, "env": {key: "sk-live"}}
        )
    assert key in str(err.value)


@pytest.mark.parametrize("key", ["BACKEND_PORT", "llm_provider", "LLM.PROVIDER", "LLM_RESPONSE_PROVIDER"])
def test_preset_env_refuses_keys_outside_the_allowlist(key):
    with pytest.raises(ValidationError):
        PresetDefinition.model_validate(
            {"overrides": {"llm.default_provider": "openai"}, "env": {key: "x"}}
        )


def test_preset_env_values_must_be_strings():
    with pytest.raises(ValidationError):
        PresetDefinition.model_validate(
            {"overrides": {"llm.default_provider": "openai"}, "env": {"LLM_PROVIDER": 3}}
        )


def test_memory_config_carries_the_corrections_budget_as_its_fifth_field():
    """Batch 5, task 7 fix round 1 (ruling R56): the reply's corrections
    band budget is a setting, default 1500, 0 allowed (the band off),
    negative refused. Batch 8, task 3 (ruling T3-5) appends the identity
    history prune's two knobs after it."""
    from core.settings_schema import MemoryConfig

    assert list(MemoryConfig.model_fields) == [
        "core_budget_chars", "durable_archive_days", "thread_archive_days",
        "onboarding_nag_days", "corrections_budget_chars",
        "history_keep_last", "history_daily_days",
    ]
    assert MemoryConfig().corrections_budget_chars == 1500
    assert MemoryConfig(corrections_budget_chars=0).corrections_budget_chars == 0
    with pytest.raises(ValidationError):
        MemoryConfig(corrections_budget_chars=-1)
    description = MemoryConfig.model_fields["corrections_budget_chars"].description or ""
    assert "0" in description and "reply" in description.lower()
