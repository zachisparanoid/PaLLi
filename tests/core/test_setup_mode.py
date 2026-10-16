"""Setup mode — she boots with no brain, honest and quiet (first door, task 1).

The owner's ruling of 2026-09-10 made the dashboard a stranger's first door.
Until this task a stranger could not reach it: a provider that needs a key,
with no key, was a hard failure and the boot exited 2. Now it is SETUP MODE —
she starts, both chat doors stay shut, every thinking lane is quiet, and one
soft line on the punch list names the address where a brain can be added.

What is pinned here, in-process:

  * the schema's verdict — `brain_ready`, the soft line, the doors forced shut,
    and the checks that must stay HARD (a local server with no address, a
    per-task override naming a keyless provider);
  * the shared pieces in core/setup_mode.py — the sentence, the address, the
    once-an-hour idle line every gated lane shares;
  * the NullProvider — an ungated path fails loudly, never sends a keyless
    request to a real endpoint;
  * the live probes — `--preflight-only` and the doctor never PONG a brain
    that is not there, and never report its absence as a failed probe;
  * a reminder that comes due is HELD, not fired and not lost.

`Config()` reads env at IMPORT into class attributes and python-dotenv fills
any unset variable from this checkout's .env.local, so every deciding value is
set on an instance (the tests/core/test_config_validation_matrix.py pattern).
"""
from __future__ import annotations

import logging

import pytest

from core import setup_mode
from core.config import Config, config
from core.trusted_networks import DEFAULT_ALLOWED_NETWORKS

TASK_PROVIDER_VARS = (
    "LLM_RESPONSE_PROVIDER",
    "LLM_ANALYSIS_PROVIDER",
    "LLM_MEMORY_PROVIDER",
    "LLM_DECISION_PROVIDER",
    "LLM_PROACTIVE_PROVIDER",
    "LLM_REFLECTION_PROVIDER",
    "LLM_WEB_SEARCH_PROVIDER",
    "LLM_VISION_PROVIDER",
)
FICTIONAL_DISCORD_TOKEN = "x" * 40
FICTIONAL_TELEGRAM_TOKEN = "123456:ABC-fictional-token"
THE_SENTENCE = (
    "I don't have a brain yet — open the dashboard at http://127.0.0.1:3000 "
    "and add a key, then I'm here."
)


def _stranger_config(provider: str = "deepseek") -> Config:
    """A first boot with NO brain key at all: API-only, every extra off, so the
    only thing a case can be about is the brain."""
    cfg = Config()
    cfg.LLM_PROVIDER = provider
    cfg.DEEPSEEK_API_KEY = ""
    cfg.OPENAI_API_KEY = ""
    for name in TASK_PROVIDER_VARS:
        setattr(cfg, name, "")
    cfg.PLUGIN_RUNTIME_ENABLED = False
    cfg.ENABLE_DISCORD_BOT = False
    cfg.DISCORD_TOKEN = ""
    cfg.LOCAL_OWNER_DISCORD_USER_ID = ""
    cfg.ENABLE_TELEGRAM_BOT = False
    cfg.TELEGRAM_BOT_TOKEN = ""
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = ""
    cfg.ENABLE_VOICE_FEATURES = False
    cfg.ENABLE_WEB_SEARCH = False
    cfg.BACKEND_HOST = "127.0.0.1"
    cfg.BACKEND_ALLOWED_NETWORKS = DEFAULT_ALLOWED_NETWORKS
    cfg.BACKEND_PORT = 8000
    cfg.ENVIRONMENT = "development"
    cfg.ALLOW_INSECURE_CORS = False
    cfg.FRONTEND_ORIGINS = []
    cfg.FRONTEND_ORIGIN_REGEX = None
    cfg.FRONTEND_URL = ""
    return cfg


def _telegram_library(monkeypatch, present: bool) -> None:
    import core.config_schema as cs

    monkeypatch.setattr(cs, "_telegram_library_present", lambda: present)


@pytest.fixture
def no_brain(monkeypatch):
    """The live singleton in setup mode, put back afterwards."""
    monkeypatch.setattr(config, "brain_ready", False, raising=False)
    monkeypatch.setattr(config, "FRONTEND_URL", "", raising=False)
    monkeypatch.setattr(setup_mode, "_last_idle_log", {})


# ── (a) the schema's verdict ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider, key_name",
    [("deepseek", "DEEPSEEK_API_KEY"), ("openai", "OPENAI_API_KEY")],
)
def test_a_provider_with_no_key_at_all_is_setup_mode_not_a_failure(provider, key_name):
    cfg = _stranger_config(provider)

    assert cfg.validate() is True
    assert cfg.brain_ready is False

    text = cfg.punch_list.render()
    assert (
        "- LLM — no brain configured; open http://127.0.0.1:3000 to add one "
        f"(or set {key_name} in .env.local)"
    ) in text, text
    # Soft: a warning for this boot, never a numbered reason to refuse.
    assert "PaLLi cannot start yet." not in text, text
    # The way out is said once. The default where-text would say it twice.
    assert text.count(".env.local") == 1, text


def test_the_line_names_the_dashboard_this_install_declares():
    cfg = _stranger_config()
    cfg.FRONTEND_URL = "http://192.0.2.7:3000"

    assert cfg.validate() is True
    assert "open http://192.0.2.7:3000 to add one" in cfg.punch_list.render()


def test_a_blank_that_is_only_whitespace_is_still_no_key():
    cfg = _stranger_config()
    cfg.DEEPSEEK_API_KEY = "   "

    assert cfg.validate() is True
    assert cfg.brain_ready is False


def test_a_local_server_with_no_address_is_still_hard():
    """Setup mode is a missing KEY. A local brain with nowhere to call is a
    misconfiguration the door cannot fix by pasting anything."""
    cfg = _stranger_config("local_openai")
    cfg.LOCAL_LLM_API_BASE = ""
    cfg.LOCAL_LLM_MODEL = "qwen3.5:9b"

    assert cfg.validate() is False
    assert "LOCAL_LLM_API_BASE" in cfg.punch_list.render()


def test_a_local_server_with_an_address_and_a_model_has_a_brain():
    """The shipped template. It needs no key, so it is never setup mode —
    whether the server answers is the live test's business."""
    cfg = _stranger_config("local_openai")
    cfg.LOCAL_LLM_API_BASE = "http://127.0.0.1:11434/v1"
    cfg.LOCAL_LLM_MODEL = "qwen3.5:9b"

    assert cfg.validate() is True
    assert cfg.brain_ready is True
    assert "no brain configured" not in cfg.punch_list.render()


def test_a_lane_override_naming_a_keyless_provider_is_still_hard():
    """Ruling P3: only the TOP-LEVEL provider's missing key is a first boot.
    An override that names a provider with no key is a real mistake."""
    cfg = _stranger_config("deepseek")
    cfg.DEEPSEEK_API_KEY = "sk-test-not-a-real-key"
    cfg.LLM_DECISION_PROVIDER = "openai"

    assert cfg.validate() is False
    assert "LLM_DECISION_PROVIDER=openai requires OPENAI_API_KEY" in cfg.punch_list.render()


def test_an_override_naming_the_same_keyless_provider_is_part_of_setup_mode():
    """Ruling P3 refined (the owner's yes, 2026-09-17 22:36). An override that
    names the SAME provider as the keyless top-level one wants the very key
    the door is about to supply — one missing key, one soft line, no second
    verdict. The owner's own walk: the key removed from a scratch copy of
    .env.local, the overrides still saying deepseek."""
    cfg = _stranger_config("deepseek")
    cfg.LLM_RESPONSE_PROVIDER = "deepseek"
    cfg.LLM_ANALYSIS_PROVIDER = "deepseek"

    assert cfg.validate() is True
    assert cfg.brain_ready is False

    text = cfg.punch_list.render()
    assert "PaLLi cannot start yet." not in text, text
    assert text.count("no brain configured") == 1, text
    assert "requires DEEPSEEK_API_KEY" not in text, text
    assert cfg.punch_list.items == []


def test_an_override_naming_a_different_keyless_provider_is_still_hard():
    """The other half of the refinement: a second provider with no key is not
    the door's missing key, it is a second mistake."""
    cfg = _stranger_config("deepseek")
    cfg.LLM_ANALYSIS_PROVIDER = "openai"

    assert cfg.validate() is False
    assert "LLM_ANALYSIS_PROVIDER=openai requires OPENAI_API_KEY" in cfg.punch_list.render()


def test_a_keyed_top_level_with_a_same_provider_override_says_nothing_new():
    """The owner's boot as it is today: a key, and overrides naming its
    provider. Byte-identical — no line, no verdict."""
    cfg = _stranger_config("deepseek")
    cfg.DEEPSEEK_API_KEY = "sk-test-not-a-real-key"
    cfg.LLM_RESPONSE_PROVIDER = "deepseek"
    cfg.LLM_ANALYSIS_PROVIDER = "deepseek"

    assert cfg.validate() is True
    assert cfg.brain_ready is True
    assert cfg.punch_list.render() == ""


def test_the_verdict_follows_the_key_when_she_is_validated_again():
    cfg = _stranger_config()
    assert cfg.validate() is True
    assert cfg.brain_ready is False

    cfg.DEEPSEEK_API_KEY = "sk-test-not-a-real-key"
    assert cfg.validate() is True
    assert cfg.brain_ready is True
    assert "no brain configured" not in cfg.punch_list.render()


# ── (b) the doors ──────────────────────────────────────────────────────────


def test_both_doors_stay_shut_with_no_brain_even_with_their_tokens(monkeypatch):
    """She must not answer on Discord with no brain. A door that HAS its token
    gets its own line — "no token" would be a lie."""
    _telegram_library(monkeypatch, True)
    cfg = _stranger_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = FICTIONAL_DISCORD_TOKEN
    cfg.LOCAL_OWNER_DISCORD_USER_ID = "123456789012345678"  # gitleaks:allow
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = FICTIONAL_TELEGRAM_TOKEN
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = "424242424"

    assert cfg.validate() is True
    assert cfg.effective_discord_enabled is False
    assert cfg.effective_telegram_enabled is False

    text = cfg.punch_list.render()
    assert "- Discord — closed until a brain is configured" in text, text
    assert "- Telegram — closed until a brain is configured" in text, text
    # Neither token is missing, so neither "no token" line is said.
    assert "DISCORD_TOKEN" not in text, text
    assert "TELEGRAM_BOT_TOKEN" not in text, text

    from core.launcher_runtime import build_channel_enabled_map

    assert build_channel_enabled_map(config_obj=cfg) == {
        "discord_bot": False,
        "telegram_bot": False,
        "mobile_door": False,
    }


def test_a_door_with_no_token_keeps_the_line_it_has_today():
    cfg = _stranger_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = ""

    assert cfg.validate() is True

    text = cfg.punch_list.render()
    assert "DISCORD_TOKEN — no token, so the Discord door is closed for this boot" in text, text
    assert "Discord — closed until a brain is configured" not in text, text


def test_the_brain_validator_has_the_last_word_on_the_doors():
    """Pydantic runs after-validators in definition order and the collector's
    `effective` dict keeps the last write. The brain check closes the doors, so
    it must be defined BELOW both door checks — a reorder would quietly reopen
    a door on a boot with no brain."""
    from core.config_schema import CoreConfigSchema

    order = [
        decorator.cls_var_name
        for decorator in CoreConfigSchema.__pydantic_decorators__.model_validators.values()
    ]
    brain = order.index("_validate_llm_provider_keys")
    assert order.index("_validate_messaging_platform") < brain
    assert order.index("_validate_telegram_door") < brain


# ── (e) the owner's boot ───────────────────────────────────────────────────


def test_a_key_that_is_present_changes_nothing(monkeypatch):
    _telegram_library(monkeypatch, True)
    cfg = _stranger_config()
    cfg.DEEPSEEK_API_KEY = "sk-test-not-a-real-key"
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = FICTIONAL_DISCORD_TOKEN
    cfg.LOCAL_OWNER_DISCORD_USER_ID = "123456789012345678"  # gitleaks:allow
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = FICTIONAL_TELEGRAM_TOKEN
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = "424242424"

    assert cfg.validate() is True
    assert cfg.brain_ready is True
    assert cfg.effective_discord_enabled is True
    assert cfg.effective_telegram_enabled is True
    # Nothing at all to say: no brain line, no door line.
    assert cfg.punch_list.render() == ""


def test_nothing_has_been_decided_before_the_first_validate():
    """The class default. A reader that runs before validate() — and every test
    that never validates — behaves exactly as it did before this existed."""
    assert Config.brain_ready is True


# ── the shared pieces ──────────────────────────────────────────────────────


def test_the_address_is_the_declared_dashboard_or_the_loopback_default():
    assert setup_mode.dashboard_address("") == "http://127.0.0.1:3000"
    assert setup_mode.dashboard_address("   ") == "http://127.0.0.1:3000"
    assert setup_mode.dashboard_address(None) == "http://127.0.0.1:3000"
    assert setup_mode.dashboard_address("http://192.0.2.7:3000/") == "http://192.0.2.7:3000/"


def test_the_sentence_is_the_one_fixed_sentence(no_brain, monkeypatch):
    assert setup_mode.no_brain_sentence() == THE_SENTENCE

    monkeypatch.setattr(config, "FRONTEND_URL", "http://192.0.2.7:3000", raising=False)
    assert "open the dashboard at http://192.0.2.7:3000 and add a key" in (
        setup_mode.no_brain_sentence()
    )


def test_a_lane_with_a_brain_is_not_idle_and_says_nothing(monkeypatch, caplog):
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    monkeypatch.setattr(setup_mode, "_last_idle_log", {})

    with caplog.at_level(logging.INFO, logger="core.setup_mode"):
        assert setup_mode.lane_idle("night cycle") is False

    assert caplog.records == []


def test_an_idle_lane_says_so_once_an_hour_at_most(no_brain, monkeypatch, caplog):
    clock = {"now": 1000.0}
    monkeypatch.setattr(setup_mode, "_clock", lambda: clock["now"])

    with caplog.at_level(logging.INFO, logger="core.setup_mode"):
        assert setup_mode.lane_idle("night cycle") is True
        clock["now"] += 3599.0
        assert setup_mode.lane_idle("night cycle") is True
        lines = [r.getMessage() for r in caplog.records]
        assert lines == ["setup mode: night cycle idle — no brain"]

        # Another lane has its own hour.
        assert setup_mode.lane_idle("daydream") is True
        # And the first lane speaks again once its hour is up.
        clock["now"] += 2.0
        assert setup_mode.lane_idle("night cycle") is True

    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "setup mode: night cycle idle — no brain",
        "setup mode: daydream idle — no brain",
        "setup mode: night cycle idle — no brain",
    ]
    assert {r.levelno for r in caplog.records} == {logging.INFO}

    # A brain arrives and is lost again inside the same hour: the lane's hour
    # is forgotten when it sees the brain, so the new idle period announces
    # itself instead of being swallowed by the old one's throttle.
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    assert setup_mode.lane_idle("night cycle") is False
    monkeypatch.setattr(config, "brain_ready", False, raising=False)
    clock["now"] += 5.0
    with caplog.at_level(logging.INFO, logger="core.setup_mode"):
        assert setup_mode.lane_idle("night cycle") is True
    assert [r.getMessage() for r in caplog.records][3:] == [
        "setup mode: night cycle idle — no brain"
    ]


def test_the_gate_is_read_fresh_so_a_lane_resumes_without_a_restart(no_brain, monkeypatch):
    """Task 2's API flips `config.brain_ready` on the running process. The
    engines were started at boot; the gate is what lets them pick up."""
    assert setup_mode.lane_idle("inner monologue") is True

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    assert setup_mode.lane_idle("inner monologue") is False


# ── the NullProvider ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_call_on_the_null_provider_raises_brain_not_configured():
    from ai.llm_provider_registry import NullProvider

    provider = NullProvider("deepseek")

    assert provider.provider_name == "deepseek"
    assert provider.is_configured() is False
    with pytest.raises(setup_mode.BrainNotConfigured):
        await provider.chat_completion([{"role": "user", "content": "hi"}])
    with pytest.raises(setup_mode.BrainNotConfigured):
        await provider.chat_completion_stream([{"role": "user", "content": "hi"}])
    with pytest.raises(setup_mode.BrainNotConfigured):
        await provider.vision_completion("aGk=", "what is this")


def _touch_me_not(monkeypatch):
    """Any real provider that tries to open a session fails the test: setup
    mode never sends a keyless request to a real endpoint."""
    from ai.llm_openai_compatible import BaseLLMProvider

    async def _explode(self):
        raise AssertionError(f"setup mode opened a session on {self.provider_name}")

    monkeypatch.setattr(BaseLLMProvider, "_get_session", _explode)


@pytest.mark.asyncio
async def test_the_client_with_no_brain_fails_loudly_once_and_touches_nothing(
    no_brain, monkeypatch, caplog
):
    from ai.llm_client import LLMClient
    from ai.llm_provider_registry import NullProvider
    from ai.llm_providers import DeepSeekProvider, OpenAIProvider

    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    _touch_me_not(monkeypatch)
    client = LLMClient()
    assert isinstance(client.provider, NullProvider)

    # Every later install goes through the same funnel (the plugin runtime's
    # boot install, a settings hot-reload). Two providers only, so resolving a
    # route never asks the CLI providers whether they are logged in.
    client.set_provider_registry({"openai": OpenAIProvider(), "deepseek": DeepSeekProvider()})
    assert isinstance(client.provider, NullProvider)
    assert isinstance(client.providers["deepseek"], NullProvider)

    with caplog.at_level(logging.ERROR, logger="ai.llm_client"):
        # The documented contract holds: never raises, returns "".
        reply = await client.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="deepseek::deepseek-chat",
            log_context="an_ungated_lane",
        )

    assert reply == ""
    failures = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(failures) == 1, failures
    assert "an_ungated_lane" in failures[0]
    assert "no brain is configured" in failures[0]
    await client.close()


@pytest.mark.asyncio
async def test_a_brain_that_arrives_swaps_the_real_provider_back_in(no_brain, monkeypatch):
    """Task 2 rebuilds the registry once a key is saved. Nothing may hold the
    NullProvider anywhere that swap cannot reach."""
    from ai.llm_client import LLMClient
    from ai.llm_provider_registry import NullProvider, build_default_provider_registry
    from ai.llm_providers import DeepSeekProvider

    from ai.llm_providers import OpenAIProvider

    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "", raising=False)
    client = LLMClient()
    assert isinstance(client.provider, NullProvider)
    assert isinstance(client.openai_provider, NullProvider)

    # What the setup API does: the key lands on config, the verdict flips,
    # and the registry is rebuilt through the same funnel.
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test-not-a-real-key", raising=False)
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    await client.replace_provider_registry(build_default_provider_registry())

    assert isinstance(client.provider, DeepSeekProvider)
    assert not any(isinstance(p, NullProvider) for p in client.providers.values())
    assert client.deepseek_provider is client.provider
    # The still-keyless OpenAI entry is the real class again, as on any keyed boot.
    assert type(client.openai_provider) is OpenAIProvider
    await client.close()


@pytest.mark.asyncio
async def test_with_a_brain_the_registry_is_exactly_what_it_was(monkeypatch):
    from ai.llm_client import LLMClient
    from ai.llm_provider_registry import NullProvider
    from ai.llm_providers import DeepSeekProvider

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    client = LLMClient()

    assert isinstance(client.provider, DeepSeekProvider)
    assert not any(isinstance(p, NullProvider) for p in client.providers.values())
    await client.close()


def _blank_every_cloud_key(monkeypatch) -> None:
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setattr(config, name, "", raising=False)


def test_every_keyless_provider_that_needs_a_key_is_nulled_not_only_the_default(monkeypatch):
    """Fix round 1. Calls are routed by provider NAME — `llm.routing.<task>` in
    the yaml first, env second — so a lane can name a provider that is not the
    default. With deepseek the (keyless) default and a keyless openai beside
    it, a yaml row naming openai would have POSTed to api.openai.com with no
    Authorization header. Which providers need a key is the capability
    matrix's answer; whether one has it is the provider's own. The local and
    CLI providers spend nothing and are left exactly as they are — and are not
    even ASKED, because asking a CLI provider spawns its login probe."""
    from ai.llm_claude_cli import ClaudeCLIProvider
    from ai.llm_codex_cli import CodexCLIProvider
    from ai.llm_ollama import OllamaProvider
    from ai.llm_provider_registry import (
        NullProvider,
        build_default_provider_registry,
        configure_provider_registry,
    )
    from ai.llm_providers import LocalOpenAIProvider

    _blank_every_cloud_key(monkeypatch)
    for spared in (ClaudeCLIProvider, CodexCLIProvider, OllamaProvider, LocalOpenAIProvider):
        monkeypatch.setattr(
            spared,
            "is_configured",
            lambda self: (_ for _ in ()).throw(
                AssertionError(f"setup mode asked {type(self).__name__} whether it is configured")
            ),
        )

    registry = configure_provider_registry(
        build_default_provider_registry(),
        default_provider_name="deepseek",
        brain_ready=False,
    )

    nulled = sorted(
        name for name, provider in registry.providers.items() if isinstance(provider, NullProvider)
    )
    assert nulled == ["anthropic", "deepseek", "openai"]
    assert type(registry.providers["local_openai"]) is LocalOpenAIProvider
    assert type(registry.providers["ollama"]) is OllamaProvider
    assert type(registry.providers["codex"]) is CodexCLIProvider
    assert type(registry.providers["claude"]) is ClaudeCLIProvider
    # The default, and the fallback an unknown route lands on, are both refused.
    assert registry.provider is registry.providers["deepseek"]
    assert isinstance(registry.openai_provider, NullProvider)
    # Boot's pre-warm still finds the real local provider.
    assert registry.ollama_provider is registry.providers["ollama"]


def test_a_provider_that_has_its_key_stays_real_even_with_no_brain(monkeypatch):
    """Nulled means "needs a key and has none". A keyed provider beside the
    keyless default is a working route; every lane is idle anyway."""
    from ai.llm_provider_registry import (
        NullProvider,
        build_default_provider_registry,
        configure_provider_registry,
    )
    from ai.llm_providers import OpenAIProvider

    _blank_every_cloud_key(monkeypatch)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test-not-a-real-key", raising=False)

    registry = configure_provider_registry(
        build_default_provider_registry(),
        default_provider_name="deepseek",
        brain_ready=False,
    )

    assert isinstance(registry.providers["deepseek"], NullProvider)
    assert type(registry.providers["openai"]) is OpenAIProvider


@pytest.mark.asyncio
async def test_a_lane_routed_by_name_to_a_keyless_provider_sends_nothing(
    no_brain, monkeypatch, caplog
):
    """The reviewer's case end to end: LLM_PROVIDER=deepseek, the route names
    openai, neither has a key. Nothing leaves the box; one ERROR says why."""
    from ai.llm_client import LLMClient
    from ai.llm_providers import DeepSeekProvider, LocalOpenAIProvider, OpenAIProvider

    _blank_every_cloud_key(monkeypatch)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    _touch_me_not(monkeypatch)
    client = LLMClient()
    client.set_provider_registry(
        {
            "openai": OpenAIProvider(),
            "deepseek": DeepSeekProvider(),
            "local_openai": LocalOpenAIProvider(),
        }
    )

    with caplog.at_level(logging.ERROR, logger="ai.llm_client"):
        reply = await client.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="openai::gpt-4o",
            log_context="a_yaml_routed_lane",
        )

    assert reply == ""
    failures = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(failures) == 1, failures
    assert "a_yaml_routed_lane" in failures[0]
    assert "no brain is configured" in failures[0]
    assert "openai" in failures[0]
    await client.close()


def test_with_a_brain_a_keyless_provider_beside_the_keyed_one_is_left_alone(monkeypatch):
    """The owner's boot: a keyed DeepSeek and an OpenAI entry that never had a
    key. That entry is the fallback provider and has always been the real
    class; with a brain the registry is the very objects it was handed."""
    from ai.llm_provider_registry import (
        NullProvider,
        build_default_provider_registry,
        configure_provider_registry,
    )
    from ai.llm_providers import OpenAIProvider

    _blank_every_cloud_key(monkeypatch)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test-not-a-real-key", raising=False)
    built = build_default_provider_registry()

    registry = configure_provider_registry(
        built, default_provider_name="deepseek", brain_ready=True
    )

    assert set(registry.providers) == set(built)
    assert all(registry.providers[name] is built[name] for name in built)
    assert not any(isinstance(p, NullProvider) for p in registry.providers.values())
    assert type(registry.openai_provider) is OpenAIProvider
    assert registry.openai_provider.api_key == ""


# ── the live probes ────────────────────────────────────────────────────────


def _exploding_probes(monkeypatch):
    from palli_config import providers as providers_module

    async def _boom(*_args, **_kwargs):
        raise AssertionError("setup mode sent a live probe")

    monkeypatch.setattr(providers_module, "validate_provider", _boom)
    monkeypatch.setattr(providers_module, "fetch_balance", _boom)


def test_the_preflight_does_not_pong_a_brain_that_is_not_there(no_brain, monkeypatch, capsys):
    import main as main_module

    monkeypatch.delenv("PALLI_PREFLIGHT_OFFLINE", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    _exploding_probes(monkeypatch)

    assert main_module.live_brain_and_balance() is True

    out = capsys.readouterr().out
    assert "Brain: not asked — no brain is configured yet (setup mode)." in out
    assert "did not answer" not in out


def test_the_doctor_does_not_pong_it_either_and_never_calls_it_a_failed_probe(
    no_brain, monkeypatch
):
    from core import launcher_support

    monkeypatch.delenv("PALLI_PREFLIGHT_OFFLINE", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek", raising=False)
    _exploding_probes(monkeypatch)

    checks = launcher_support.check_llm_brain_and_balance()

    assert [c["code"] for c in checks] == ["llm_brain_answers"]
    assert checks[0]["severity"] == "ok"
    assert "was not asked" in checks[0]["message"]
    assert "setup mode" in checks[0]["message"]


def test_the_doctor_shows_the_punch_lists_own_line_as_a_warning(monkeypatch):
    """Spec §5: `palli_ctl doctor` — the same line, `warning` severity."""
    from core import launcher_support

    cfg = _stranger_config()
    assert cfg.validate() is True
    monkeypatch.setattr(launcher_support, "config", cfg)

    checks = launcher_support.check_brain_configured()

    assert [c["code"] for c in checks] == ["llm_brain_missing"]
    assert checks[0]["severity"] == "warning"
    assert checks[0]["message"] == (
        "LLM — no brain configured; open http://127.0.0.1:3000 to add one "
        "(or set DEEPSEEK_API_KEY in .env.local)"
    )


def test_the_doctor_has_nothing_to_say_about_a_brain_that_is_there(monkeypatch):
    from core import launcher_support

    cfg = _stranger_config()
    cfg.DEEPSEEK_API_KEY = "sk-test-not-a-real-key"
    assert cfg.validate() is True
    monkeypatch.setattr(launcher_support, "config", cfg)

    assert launcher_support.check_brain_configured() == []


def test_the_doctor_says_why_a_door_with_a_token_is_shut(monkeypatch):
    """"No Discord token is configured" would be a lie here, and it would send
    a stranger to fix the one thing that is fine."""
    from core import launcher_support

    _telegram_library(monkeypatch, True)
    cfg = _stranger_config()
    cfg.ENABLE_DISCORD_BOT = True
    cfg.DISCORD_TOKEN = FICTIONAL_DISCORD_TOKEN
    cfg.LOCAL_OWNER_DISCORD_USER_ID = "123456789012345678"  # gitleaks:allow
    cfg.ENABLE_TELEGRAM_BOT = True
    cfg.TELEGRAM_BOT_TOKEN = FICTIONAL_TELEGRAM_TOKEN
    cfg.LOCAL_OWNER_TELEGRAM_USER_ID = "424242424"
    assert cfg.validate() is True
    monkeypatch.setattr(launcher_support, "config", cfg)

    by_code = {c["code"]: c for c in launcher_support.check_doors_waiting_on_a_brain()}

    assert set(by_code) == {"discord_closed_no_brain", "telegram_closed_no_brain"}
    assert {c["severity"] for c in by_code.values()} == {"warning"}
    assert "closed until a brain is configured" in by_code["discord_closed_no_brain"]["message"]
    assert "closed until a brain is configured" in by_code["telegram_closed_no_brain"]["message"]

    # And it keeps saying it after the door succeeds (final wave, important 1).
    # `POST /api/setup/brain` flips `brain_ready` on the running process and
    # deliberately does NOT re-run validate(), so the doors are still shut
    # until the restart — and the line boot wrote already says so ("it opens at
    # the first restart after you add a brain"). A `brain_ready` early return
    # here left the health readout and `palli_ctl doctor` telling a stranger
    # their Discord token was missing, which is the one thing that is fine.
    cfg.brain_ready = True

    after = {c["code"]: c for c in launcher_support.check_doors_waiting_on_a_brain()}

    assert set(after) == {"discord_closed_no_brain", "telegram_closed_no_brain"}
    assert after["discord_closed_no_brain"]["message"] == by_code["discord_closed_no_brain"]["message"]
    assert after["telegram_closed_no_brain"]["message"] == by_code["telegram_closed_no_brain"]["message"]
    # The two checks that read it. Discord's lives in `_check_optional_capabilities`,
    # which computes both lines, so the false one can be asserted absent.
    capability_codes = {c["code"] for c in launcher_support._check_optional_capabilities()}
    assert "discord_closed_no_brain" in capability_codes
    assert "discord_token_missing" not in capability_codes
    # Telegram's is the `_telegram_waiting` lookup inside `build_doctor_report`,
    # whose whole condition is this helper finding that item — so with it
    # present the `telegram_token_missing` branch cannot be reached at all.
    # `build_doctor_report()` itself is not called here: it probes the local
    # model server's doors over HTTP and reads the configured database path,
    # neither of which a hermetic unit test may do. What IS pinned is that
    # nothing either reachable reader says about Telegram after the flip
    # carries that branch's two sentences — if the door line ever regressed
    # into one of them, this is where it would show.
    telegram_said = " ".join(
        c["message"] for c in [*after.values(), *launcher_support._check_optional_capabilities()]
    )
    assert "python-telegram-bot is not installed" not in telegram_said
    assert "TELEGRAM_BOT_TOKEN is not configured" not in telegram_said


# ── a reminder that comes due ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reminder_that_comes_due_with_no_brain_is_held_not_fired_and_not_lost(
    no_brain, monkeypatch
):
    """Both doors are shut in setup mode, so a reminder that fired could not be
    delivered — and three failed deliveries DEAD-LETTER it (binding.py's
    REMINDER_MAX_DELIVERY_ATTEMPTS): marked completed, never said. Held at the
    scheduler it stays due in the database, untouched, until she has a brain."""
    from core.reminders import scheduler as scheduler_module

    touched: list[str] = []

    async def _list_due(**_kwargs):
        touched.append("listed")
        return [{"id": 7, "user_id": 1, "platform": "discord", "reminder_text": "call the shop"}]

    async def _mark_completed(**_kwargs):
        touched.append("completed")

    async def _send(_row):
        touched.append("sent")
        return False

    monkeypatch.setattr(scheduler_module.brain, "list_due_scheduled_reminders", _list_due)
    monkeypatch.setattr(scheduler_module.brain, "mark_scheduled_reminder_completed", _mark_completed)
    target = scheduler_module.ReminderScheduler()
    monkeypatch.setattr(target, "_send_reminder", _send)

    for _ in range(5):  # more passes than the dead-letter cap allows failures
        await target._check_due_reminders()

    assert touched == []
    assert getattr(target, "_delivery_failures", {}) == {}

    # The brain arrives: the same scheduler, no restart, picks the row up.
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    await target._check_due_reminders()
    assert touched[:2] == ["listed", "sent"]


@pytest.mark.asyncio
async def test_a_held_overdue_timer_does_not_put_the_check_loop_on_its_one_second_floor(
    no_brain, monkeypatch
):
    """An overdue timer's next-check delay is the 1 s floor (binding.py). Held,
    that row stays overdue, so the loop would wake every second to open a
    connection and change nothing. While held it sleeps the deferred default
    and does not ask the database at all; a new reminder still wakes it."""
    from core.reminders import scheduler as scheduler_module
    from core.reminders.binding import _DEFERRED_DEFAULT_SLEEP_SECONDS

    opened: list[str] = []

    class _OverdueTimer:
        async def fetchrow(self, _query):
            return (0.0, "timer")

        async def close(self):
            return None

    async def _connection():
        opened.append("opened")
        return _OverdueTimer()

    monkeypatch.setattr(scheduler_module.brain, "get_connection", _connection)
    target = scheduler_module.ReminderScheduler()
    target.is_running = True

    assert await target._compute_next_check_delay_seconds() == _DEFERRED_DEFAULT_SLEEP_SECONDS
    assert _DEFERRED_DEFAULT_SLEEP_SECONDS > 1.0
    assert opened == []

    # With a brain the loop asks again, and an overdue timer is due NOW.
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    assert await target._compute_next_check_delay_seconds() == 1.0
    assert opened == ["opened"]


# ── the lanes: each scheduler skips its tick, and picks up again ───────────
#
# The engines still START in setup mode (core/main.py is untouched), so what is
# pinned here is the TICK: with no brain the lane's work is never reached, and
# the same engine object does it again once `brain_ready` flips — no restart.


def _stop_after(engine, sleeps: list, count: int):
    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= count:
            engine.is_running = False

    return _fake_sleep


def test_the_inner_monologue_waits_like_a_pause(no_brain, monkeypatch):
    import asyncio

    import ai.inner_monologue as im

    engine = im.InnerMonologueEngine()
    engine.is_running = True
    reached: list[str] = []
    sleeps: list[float] = []

    async def _tick():
        reached.append("tick")

    monkeypatch.setattr(engine, "_run_tick", _tick)
    monkeypatch.setattr(
        engine, "_queue_monologue_compaction_if_due", lambda: reached.append("compaction")
    )
    monkeypatch.setattr(engine, "_compute_next_interval", lambda: 900)
    monkeypatch.setattr(im.asyncio, "sleep", _stop_after(engine, sleeps, 3))

    asyncio.run(engine._tick_loop())

    # The boot delay, then one-minute waits: neither the tick nor the
    # compaction (both LLM calls) was reached.
    assert reached == []
    assert sleeps == [30, 60, 60]

    # The brain arrives; the same loop thinks again.
    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    engine.is_running = True
    sleeps.clear()
    asyncio.run(engine._tick_loop())
    assert reached[:2] == ["tick", "compaction"]


@pytest.mark.asyncio
async def test_the_night_cycle_skips_its_pass_and_leaves_the_date_unclaimed(
    no_brain, monkeypatch, tmp_path
):
    import ai.night_cycle as nc

    engine = nc.NightCycleEngine(state_path=tmp_path / "state.json")
    ran: list[str] = []

    async def _run_once(date):
        ran.append(date)
        return {"wrote": True}

    monkeypatch.setattr(nc, "night_cycle_enabled", lambda: True)
    monkeypatch.setattr(engine, "_run_once", _run_once)

    await engine._attempt_pass()

    assert ran == []
    # Unclaimed, so the boot catch-up can still write this night.
    assert not (tmp_path / "state.json").exists()

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    await engine._attempt_pass()
    assert len(ran) == 1


@pytest.mark.asyncio
async def test_the_moment_loop_runs_no_cycle(no_brain, monkeypatch):
    """Setup mode is the organ-off branch: no cycle (and so no arbiter release,
    which only a cycle reaches), while the residual jobs still ride — the stake
    threads and the arbiter's expiry sweep have no LLM in them, and the one job
    that ends in a turn gates itself (next test). The heartbeat refresh matters
    as much as the skip: without it the loop collapses to its 1 s floor (the
    07-24 spin)."""
    import ai.present_moment as pm

    engine = pm.PresentMomentEngine()
    engine.is_running = True
    engine._last_heartbeat_at = 0.0
    reached: list[str] = []

    async def _cycle(woke_on=None):
        reached.append("cycle")
        return {}

    async def _residuals():
        reached.append("residuals")

    monkeypatch.setattr(engine, "_enabled", lambda: True)
    monkeypatch.setattr(engine, "run_cycle_once", _cycle)
    monkeypatch.setattr(engine, "_run_outreach_residuals", _residuals)
    engine._ingest("turn_failed", {})  # a priority wake: it would fire at once

    await engine._loop_once()

    assert reached == ["residuals"]
    assert len(engine._pending) == 0
    assert engine._last_heartbeat_at > 0.0
    assert engine._next_deadline(engine._last_heartbeat_at) > 1.0

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    engine._ingest("turn_failed", {})
    await engine._loop_once()
    assert reached == ["residuals", "cycle", "residuals"]


@pytest.mark.asyncio
async def test_a_proactive_action_waits_unconsumed_instead_of_sending_the_sentence(
    no_brain, monkeypatch
):
    """The one residual job that ends in a turn. With no brain dispatch_turn
    answers with the setup sentence, and this rail would SEND that as her
    outreach and mark the hunch spent."""
    from types import SimpleNamespace

    import core.turn.proactive as pro

    reached: list[str] = []
    hunch = SimpleNamespace(kind="action", text="check the backup finished",
                            action_hint="ls backups", timestamp=1.0)

    async def _candidate(**_kwargs):
        return hunch

    async def _allowed(**_kwargs):
        return True

    async def _act(**_kwargs):
        reached.append("acted")

    async def _consume(**_kwargs):
        reached.append("consumed")

    monkeypatch.setattr(pro, "_load_unconsumed_candidate", _candidate)
    monkeypatch.setattr(pro, "_outreach_allowed", _allowed)
    monkeypatch.setattr(pro, "consider_proactive_action", _act)
    monkeypatch.setattr(pro, "_record_candidate_consumption", _consume)
    monkeypatch.setattr(
        "core.settings_boot.settings_service.snapshot",
        lambda: SimpleNamespace(features=SimpleNamespace(proactive_actions=True)),
    )

    assert await pro._consider_candidate_rail(owner_id=1) is False
    assert reached == []

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    assert await pro._consider_candidate_rail(owner_id=1) is True
    assert reached == ["acted", "consumed"]


def test_the_workbench_neither_sits_down_nor_drains_a_share(no_brain, monkeypatch):
    import asyncio

    import ai.workbench as wb

    engine = wb.WorkbenchEngine()
    engine.is_running = True
    reached: list[str] = []
    waits = {"n": 0}

    async def _no_sleep(_seconds):
        return None

    def _deadline(_now):
        waits["n"] += 1
        if waits["n"] >= 3:
            engine.is_running = False
        return 0.01

    async def _drain():
        reached.append("drain")
        return 0

    async def _session(*, woke_on=None):
        reached.append("session")
        return {"ran": True}

    async def _eligible(_now):
        return True

    monkeypatch.setattr(wb.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(engine, "_next_deadline", _deadline)
    monkeypatch.setattr(engine, "drain_pending_shares", _drain)
    monkeypatch.setattr(engine, "run_session_once", _session)
    monkeypatch.setattr(engine, "_enabled", lambda: True)
    monkeypatch.setattr(engine, "_poll_suggestions_mtime", lambda: None)
    monkeypatch.setattr(engine, "_decide", lambda _now: (True, [{"kind": "heartbeat"}]))
    monkeypatch.setattr(engine, "_eligible", _eligible)

    asyncio.run(engine._loop())
    assert reached == []

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    engine.is_running = True
    waits["n"] = 0
    asyncio.run(engine._loop())
    assert reached[:2] == ["drain", "session"]


def test_the_daydream_decides_not_to_fire(no_brain, monkeypatch):
    import ai.daydream as dd

    engine = dd.DaydreamEngine()
    monkeypatch.setattr(engine, "_enabled", lambda: True)
    monkeypatch.setattr(dd, "_count_today", lambda: 0)
    engine._pending.append({"kind": "wish_planted", "at": 1.0})

    assert engine._decide(1_800_000_000.0) == (False, [])
    assert engine._pending == []
    assert engine._last_heartbeat_at == 1_800_000_000.0

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    engine._pending.append({"kind": "wish_planted", "at": 1.0})
    fire, woke = engine._decide(1_800_000_000.0)
    assert fire is True
    assert [e["kind"] for e in woke] == ["wish_planted"]


@pytest.mark.asyncio
async def test_no_curiosity_dive_is_queued(no_brain, monkeypatch):
    import ai.curiosity_research as cr

    dives: list[str] = []

    async def _dive(*, topic, owner_user_id, modality="web"):
        dives.append(topic)

    monkeypatch.setattr(cr, "research_allowed", lambda modality="web": (True, ""))
    monkeypatch.setattr(cr, "research_curiosity", _dive)

    assert cr.maybe_queue_research(curiosity="the shape of stillness", owner_user_id=1) is False

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    assert cr.maybe_queue_research(curiosity="the shape of stillness", owner_user_id=1) is True
    import asyncio

    await asyncio.sleep(0)
    assert dives == ["the shape of stillness"]


@pytest.mark.asyncio
async def test_an_arrival_is_not_greeted(no_brain, monkeypatch):
    """Event-driven rather than ticked, but the same class: the greeting's
    text is an LLM call, so the lane's entry point reads the same gate."""
    import core.ambient_service as ambient

    service = ambient.AmbientPlannerService()
    reached: list[str] = []

    async def _policy(**_kwargs):
        return {}

    async def _plan(**_kwargs):
        reached.append("planned")
        return {"should_act": False, "suppression_reason": "test"}

    async def _suppressed(**_kwargs):
        reached.append("recorded")

    monkeypatch.setattr(ambient.identity_service, "get_compiled_action_policy", _policy)
    monkeypatch.setattr(service, "_plan_arrival_action", _plan)
    monkeypatch.setattr(service, "_record_suppressed_action", _suppressed)
    arrival = dict(
        owner_user_id=1,
        event={"event_type": "owner_arrived"},
        context_snapshot={},
        previous_context={},
    )

    await service.handle_context_event(**arrival)
    assert reached == []

    monkeypatch.setattr(config, "brain_ready", True, raising=False)
    await service.handle_context_event(**arrival)
    assert reached == ["planned", "recorded"]
