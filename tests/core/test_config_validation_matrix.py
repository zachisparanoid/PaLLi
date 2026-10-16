from __future__ import annotations

import os

import core.config as config_module
from core.config import Config


def _valid_config() -> Config:
    cfg = Config()
    cfg.LLM_PROVIDER = "openai"
    cfg.LLM_RESPONSE_PROVIDER = ""
    cfg.LLM_ANALYSIS_PROVIDER = ""
    cfg.LLM_MEMORY_PROVIDER = ""
    cfg.LLM_DECISION_PROVIDER = ""
    cfg.LLM_PROACTIVE_PROVIDER = ""
    cfg.LLM_REFLECTION_PROVIDER = ""
    cfg.LLM_WEB_SEARCH_PROVIDER = ""
    cfg.LLM_VISION_PROVIDER = ""
    cfg.OPENAI_API_KEY = "ok"
    cfg.DEEPSEEK_API_KEY = "ok"
    cfg.ENABLE_VOICE_FEATURES = False
    cfg.ENABLE_WEB_SEARCH = False
    cfg.DATABASE_PATH = "data/test.sqlite3"
    cfg.ENVIRONMENT = "development"
    cfg.ALLOW_INSECURE_CORS = False
    cfg.FRONTEND_ORIGINS = []
    cfg.FRONTEND_ORIGIN_REGEX = None
    cfg.FRONTEND_URL = "https://trypalli.com/dashboard"
    cfg.DISCORD_TOKEN = ""
    # Since week 2 task 7 an enabled Discord bot with a blank token is a WARNING,
    # not a blocker: Discord is off for that boot and she starts anyway. Every
    # case below is about something else, so the bot is off here and no Discord
    # warning lands in the punch list these tests read.
    cfg.ENABLE_DISCORD_BOT = False
    return cfg


def test_a_blank_openai_key_is_setup_mode_not_a_refusal():
    """PREMISE UPDATE (first door, task 1; the owner's yes 2026-09-17 22:36).
    This used to assert `validate() is False`: a blank brain key was a hard
    failure, and so a stranger could never reach the door the 2026-09-10
    ruling gave them. The rule is reversed by design (spec §2): the same
    premise is SETUP MODE now — she starts, `brain_ready` is False, and one
    soft line names the key and where to add it."""
    cfg = _valid_config()
    cfg.LLM_PROVIDER = "openai"
    cfg.OPENAI_API_KEY = ""

    assert cfg.validate() is True
    assert cfg.brain_ready is False
    text = cfg.punch_list.render()
    assert "LLM — no brain configured" in text, text
    assert "OPENAI_API_KEY" in text, text
    assert cfg.punch_list.items == []


def test_a_blank_deepseek_key_is_setup_mode_not_a_refusal():
    """PREMISE UPDATE — the DeepSeek twin of the test above, same reason."""
    cfg = _valid_config()
    cfg.LLM_PROVIDER = "deepseek"
    cfg.DEEPSEEK_API_KEY = ""

    assert cfg.validate() is True
    assert cfg.brain_ready is False
    text = cfg.punch_list.render()
    assert "LLM — no brain configured" in text, text
    assert "DEEPSEEK_API_KEY" in text, text
    assert cfg.punch_list.items == []


def test_validate_still_refuses_a_local_server_with_no_address():
    """The "she still refuses" half, kept alive: setup mode is a missing KEY.
    A local brain with nowhere to call cannot be fixed by pasting one on the
    door, so ruling P3 keeps it hard."""
    cfg = _valid_config()
    cfg.LLM_PROVIDER = "local_openai"
    cfg.LOCAL_LLM_API_BASE = ""
    cfg.LOCAL_LLM_MODEL = "llama3.1:8b"

    assert cfg.validate() is False
    assert any("LOCAL_LLM_API_BASE" in item.why for item in cfg.punch_list.items)


def test_validate_passes_with_local_openai_provider():
    cfg = _valid_config()
    cfg.LLM_PROVIDER = "local_openai"
    cfg.LOCAL_LLM_API_BASE = "http://127.0.0.1:11434/v1"
    cfg.LOCAL_LLM_MODEL = "llama3.1:8b"
    cfg.OPENAI_API_KEY = ""
    assert cfg.validate() is True


def test_validate_passes_with_codex_provider_when_cli_login_is_available():
    cfg = _valid_config()
    cfg.LLM_PROVIDER = "codex"
    cfg.OPENAI_API_KEY = ""
    cfg.CODEX_MODEL = "gpt-5.1-codex-max"
    cfg.CODEX_TIMEOUT_SECONDS = 60
    cfg._codex_cli_login_available = lambda: True
    assert cfg.validate() is True


def test_validate_fails_with_codex_provider_when_cli_login_is_unavailable():
    cfg = _valid_config()
    cfg.LLM_PROVIDER = "codex"
    cfg.OPENAI_API_KEY = ""
    cfg.CODEX_MODEL = "gpt-5.1-codex-max"
    cfg.CODEX_TIMEOUT_SECONDS = 60
    cfg._codex_cli_login_available = lambda: False
    assert cfg.validate() is False


def test_validate_fails_with_invalid_task_provider():
    cfg = _valid_config()
    cfg.PLUGIN_RUNTIME_ENABLED = False
    cfg.LLM_ANALYSIS_PROVIDER = "bad-provider"
    assert cfg.validate() is False


def test_validate_passes_with_plugin_backed_task_provider_when_plugin_runtime_enabled():
    cfg = _valid_config()
    cfg.PLUGIN_RUNTIME_ENABLED = True
    cfg.LLM_ANALYSIS_PROVIDER = "custom-provider"
    assert cfg.validate() is True


def test_voice_without_deepgram_turns_listening_off_instead_of_blocking():
    """Week 1 task 1: this used to be `validate() is False` — a stranger with no
    Deepgram account had a text companion he could not boot. The key buys voice,
    and only voice, so its absence turns voice off and says so."""
    cfg = _valid_config()
    cfg.ENABLE_VOICE_FEATURES = True
    # Pinned, like its lemonfox sibling below: the shipped config selects the keyless
    # local pair now (step 28 task 4 fix 2), so an unpinned STT_PROVIDER resolves to
    # faster_whisper from the ambient snapshot and this test proves nothing about the
    # key gate it is named for.
    cfg.STT_PROVIDER = "deepgram"
    cfg.DEEPGRAM_API_KEY = ""
    assert cfg.validate() is True
    assert "DEEPGRAM_API_KEY" in cfg.punch_list.render()
    assert cfg.effective_stt_provider == "off"


# (test_validate_passes_with_cartesia_tts_when_keys_present retired
# 2026-08-20, step 31: Cartesia cut entirely — owner's call; "cartesia" left
# ALLOWED_TTS_PROVIDERS. Its rejection is pinned below; lemonfox took the
# passing slot.)


def test_validate_passes_with_lemonfox_tts():
    """Step 31: lemonfox is her provider of record — in the allowed set even
    without the plugin runtime's open registry. No env key-pair gate: the
    client degrades to logged silence without LEMONFOX_API_KEY; voice must
    never block boot."""
    cfg = _valid_config()
    cfg.PLUGIN_RUNTIME_ENABLED = False
    cfg.ENABLE_VOICE_FEATURES = True
    cfg.STT_PROVIDER = "deepgram"
    cfg.TTS_PROVIDER = "lemonfox"
    cfg.DEEPGRAM_API_KEY = "dg"
    assert cfg.validate() is True


def test_validate_fails_with_cartesia_tts_provider_cut():
    """Step 31: "cartesia" left the allowed set. (The allowed-set check is
    plugin_runtime-gated by design — an open registry may name any provider —
    so the rejection contract is the non-runtime one.)"""
    cfg = _valid_config()
    cfg.PLUGIN_RUNTIME_ENABLED = False
    cfg.ENABLE_VOICE_FEATURES = True
    cfg.STT_PROVIDER = "deepgram"
    cfg.TTS_PROVIDER = "cartesia"
    cfg.DEEPGRAM_API_KEY = "dg"
    assert cfg.validate() is False


def test_validate_passes_with_local_voice_providers():
    cfg = _valid_config()
    cfg.ENABLE_VOICE_FEATURES = True
    cfg.STT_PROVIDER = "faster_whisper"
    cfg.TTS_PROVIDER = "pyttsx3"
    cfg.DEEPGRAM_API_KEY = ""
    cfg.VOICE_LOCAL_STT_MODEL = "small.en"
    assert cfg.validate() is True


def test_validate_fails_with_invalid_voice_engine_default():
    cfg = _valid_config()
    cfg.ENABLE_VOICE_FEATURES = True
    cfg.VOICE_ENGINE_DEFAULT = "weird"
    cfg.DEEPGRAM_API_KEY = "dg"
    assert cfg.validate() is False


# (test_validate_requires_openai_key_when_openai_bridge_is_default and
# test_validate_passes_with_openai_bridge_default_when_openai_key_exists
# retired 2026-08-20, step 31: the realtime bridge is deleted —
# "openai_realtime_bridge" now fails the allowed-set check itself, pinned
# below.)


def test_validate_fails_with_bridge_engine_cut():
    """Step 31: the deleted bridge's engine name is no longer allowed."""
    cfg = _valid_config()
    cfg.ENABLE_VOICE_FEATURES = True
    cfg.VOICE_ENGINE_DEFAULT = "openai_realtime_bridge"
    cfg.STT_PROVIDER = "faster_whisper"
    cfg.TTS_PROVIDER = "pyttsx3"
    cfg.OPENAI_API_KEY = "openai"
    assert cfg.validate() is False


# (test_validate_fails_with_cartesia_tts_without_voice_id retired 2026-08-20,
# step 31: the cartesia key-pair validation block left with the provider;
# "cartesia" now fails the allowed-set check itself — pinned above.)


def test_validate_fails_with_invalid_voice_runtime_settings():
    cfg = _valid_config()
    cfg.VOICE_RUNTIME_MODE = "weird"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_INTERACTION_MODE = "yolo"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_PASSIVE_POLICY = "sometimes"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_SHORT_REPLY_MAX_CHARS = 10
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_DEEPGRAM_STREAM_ENCODING = "mp3"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_DEEPGRAM_STREAM_SAMPLE_RATE = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_DEEPGRAM_STREAM_CHANNELS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_DEEPGRAM_STREAM_CHUNK_MS = 10
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_LOCAL_STT_DEVICE = "quantum"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_LOCAL_MODEL_HOST = "other"
    assert cfg.validate() is False

    # (The OPENAI_REALTIME_WS_URL / VOICE_OPENAI_REALTIME_* non-empty pins
    # retired 2026-08-20, step 31: those attrs left core.config with the
    # deleted bridge; nothing bakes them into the runtime any more.)

    cfg = _valid_config()
    cfg.VOICE_DEBUG_RECORD_MIN_SECONDS = -1
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_DEBUG_RECORD_IDLE_FLUSH_SECONDS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_EOT_TIMEOUT_MS = -1
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.VOICE_CONTINUATION_GRACE_MS = -1
    assert cfg.validate() is False


def test_web_search_without_exa_turns_the_tool_off_instead_of_blocking():
    """Week 1 task 1, the twin of the voice case above: an optional feature with no
    key is a warning and a tool she does not get, never a boot she cannot have."""
    cfg = _valid_config()
    cfg.ENABLE_WEB_SEARCH = True
    cfg.EXA_API_KEY = ""
    assert cfg.validate() is True
    assert "EXA_API_KEY" in cfg.punch_list.render()
    assert cfg.effective_web_search_enabled is False


def test_validate_fails_with_empty_database_path():
    cfg = _valid_config()
    cfg.DATABASE_PATH = ""
    assert cfg.validate() is False


def test_validate_fails_in_prod_without_cors():
    cfg = _valid_config()
    cfg.ENVIRONMENT = "production"
    cfg.FRONTEND_ORIGINS = []
    cfg.FRONTEND_ORIGIN_REGEX = None
    cfg.ALLOW_INSECURE_CORS = False
    assert cfg.validate() is False


def test_validate_fails_in_prod_with_wildcard_origin_when_not_insecure():
    cfg = _valid_config()
    cfg.ENVIRONMENT = "production"
    cfg.FRONTEND_ORIGINS = ["*"]
    cfg.ALLOW_INSECURE_CORS = False
    assert cfg.validate() is False


def test_validate_passes_with_minimal_valid_settings():
    cfg = _valid_config()
    assert cfg.validate() is True


def test_get_frontend_origin():
    cfg = _valid_config()
    cfg.FRONTEND_URL = "https://trypalli.com/join?foo=bar"
    assert cfg.get_frontend_origin() == "https://trypalli.com"

    cfg.FRONTEND_URL = ""
    assert cfg.get_frontend_origin() is None


def test_get_backend_origin():
    cfg = _valid_config()
    cfg.BACKEND_URL = "http://127.0.0.1:8000/api?x=1"
    assert cfg.get_backend_origin() == "http://127.0.0.1:8000"


def test_get_frontend_and_backend_host_port():
    cfg = _valid_config()
    cfg.FRONTEND_URL = "https://example.test:3443/dashboard"
    cfg.BACKEND_URL = "http://127.0.0.1:8123"

    assert cfg.get_frontend_host_port() == ("example.test", 3443)
    assert cfg.get_backend_host_port() == ("127.0.0.1", 8123)


def test_parse_allowed_discord_user_ids_from_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_DISCORD_USER_IDS", "123, 456")
    cfg = Config()
    assert cfg.ALLOWED_DISCORD_USER_IDS == [123, 456]


def test_parse_agentic_allowlists_from_env(monkeypatch):
    monkeypatch.setenv("AGENTIC_ALLOWED_DISCORD_USER_IDS", "111, 222")
    monkeypatch.setenv("AGENTIC_DEBUG_ALLOW_USER_IDS", "222")
    monkeypatch.setenv("AGENTIC_SUBAGENTS_ENABLED", "true")
    cfg = Config()
    assert cfg.AGENTIC_ALLOWED_DISCORD_USER_IDS == [111, 222]
    assert cfg.AGENTIC_DEBUG_ALLOW_USER_IDS == [222]
    assert cfg.AGENTIC_SUBAGENTS_ENABLED is True


def test_agentic_workspace_root_falls_back_when_env_path_missing(monkeypatch, tmp_path):
    missing = tmp_path / "missing-root"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTIC_WORKSPACE_ROOT", str(missing))
    cfg = Config()
    assert cfg.AGENTIC_WORKSPACE_ROOT == os.path.abspath(str(tmp_path))


def test_agentic_workspace_root_expands_existing_env_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTIC_WORKSPACE_ROOT", str(tmp_path))
    cfg = Config()
    assert cfg.AGENTIC_WORKSPACE_ROOT == os.path.abspath(str(tmp_path))


def test_plugin_directories_expand_to_absolute_paths(monkeypatch, tmp_path):
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLUGIN_DIRECTORIES", "plugins")

    cfg = Config()

    expected = os.path.abspath(
        os.path.join(os.path.dirname(config_module.__file__), "..", "plugins")
    )
    assert cfg.PLUGIN_DIRECTORIES == [expected]


def test_validate_fails_with_negative_agentic_pending_confirmation_limit():
    cfg = _valid_config()
    cfg.AGENTIC_MAX_PENDING_CONFIRMATIONS_PER_USER = -1
    assert cfg.validate() is False


def test_validate_fails_with_invalid_post_confirm_settings():
    cfg = _valid_config()
    cfg.AGENTIC_POST_CONFIRM_MAX_STEPS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_POST_CONFIRM_TIMEOUT_SECONDS = 0
    assert cfg.validate() is False


def test_validate_fails_with_invalid_duplicate_command_limit():
    cfg = _valid_config()
    cfg.AGENTIC_MAX_SAME_COMMAND_ATTEMPTS_PER_RUN = 0
    assert cfg.validate() is False


def test_validate_fails_with_invalid_agentic_queue_settings():
    cfg = _valid_config()
    cfg.AGENTIC_QUEUE_MODE = "bad-mode"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_QUEUE_DEBOUNCE_MS = -1
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_QUEUE_CAP = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_SESSION_IDLE_TTL_SECONDS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_SESSION_ARCHIVE_RETENTION_DAYS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_SESSION_REPLY_BACK_MAX_ROUNDS = -1
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_CONTROL_CANCEL_SCOPE = "bad-scope"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_CONTROL_REDIRECT_MODE = "bad-mode"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_STALE_CONFIRMATION_POLICY = "bad-policy"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_LANE_ROUTING_MODE = "required"
    assert cfg.validate() is False


def test_validate_fails_with_invalid_agentic_streaming_settings():
    cfg = _valid_config()
    cfg.AGENTIC_STREAMING_BREAK = "bad-break"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_STREAMING_MIN_CHARS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_STREAMING_MIN_CHARS = 300
    cfg.AGENTIC_STREAMING_MAX_CHARS = 200
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_STREAMING_IDLE_MS = -1
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.DISCORD_AGENTIC_PREVIEW_MODE = "nope"
    assert cfg.validate() is False


def test_validate_fails_with_invalid_agentic_robustness_settings():
    cfg = _valid_config()
    cfg.AGENTIC_COMPACTION_TRIGGER_STEPS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_MODEL_RETRY_MAX_ATTEMPTS = -1
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_MAX_RUN_TOKENS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_CHILD_RUN_TIMEOUT_SECONDS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_ROOT_MAX_CHILD_TOTAL_TOKENS = 0
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.AGENTIC_BUDGET_WARN_RATIO = 1.0
    assert cfg.validate() is False


def test_parse_web_ops_event_types_from_env(monkeypatch):
    monkeypatch.setenv("WEB_OPS_EVENT_TYPES", "visit, signup ,checkout_started")
    monkeypatch.setenv("ENABLE_LEGACY_HOSTED_ACCESS", "true")
    cfg = Config()
    assert cfg.WEB_OPS_EVENT_TYPES == ["visit", "signup", "checkout_started"]


def test_local_default_web_ops_event_types_drop_checkout_events(monkeypatch):
    monkeypatch.delenv("WEB_OPS_EVENT_TYPES", raising=False)
    monkeypatch.setenv("PALLI_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("ENABLE_LEGACY_HOSTED_ACCESS", "false")

    cfg = Config()

    assert cfg.WEB_OPS_EVENT_TYPES == ["visit", "signup", "email_verified"]
    assert cfg.get_default_web_ops_event_types() == ["visit", "signup", "email_verified"]


def test_cloud_or_legacy_hosted_access_restores_checkout_events(monkeypatch):
    monkeypatch.delenv("WEB_OPS_EVENT_TYPES", raising=False)
    monkeypatch.setenv("PALLI_DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("ENABLE_LEGACY_HOSTED_ACCESS", "true")

    cfg = Config()

    assert cfg.WEB_OPS_EVENT_TYPES == [
        "visit",
        "signup",
        "email_verified",
        "checkout_started",
        "payment_succeeded",
    ]
    assert cfg.legacy_hosted_access_enabled() is True


def test_validate_fails_with_invalid_web_ops_settings():
    cfg = _valid_config()
    cfg.WEB_OPS_VISIT_MODE = "bad_mode"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.WEB_OPS_VISIT_WINDOW_SECONDS = 30
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.WEB_OPS_DETAIL_LEVEL = "verbose"
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.WEB_OPS_EVENT_TYPES = ["visit", "unknown_event"]
    assert cfg.validate() is False

    cfg = _valid_config()
    cfg.PALLI_DEPLOYMENT_MODE = "local"
    cfg.ENABLE_LEGACY_HOSTED_ACCESS = False
    cfg.WEB_OPS_EVENT_TYPES = ["visit", "checkout_started"]
    assert cfg.validate() is False
