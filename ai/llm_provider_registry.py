from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping

from ai.llm_claude_cli import ClaudeCLIProvider
from ai.llm_codex_cli import CodexCLIProvider
from ai.llm_ollama import OllamaProvider
from ai.llm_openai_compatible import BaseLLMProvider
from ai.llm_providers import (
    AnthropicProvider,
    DeepSeekProvider,
    LocalOpenAIProvider,
    OpenAIProvider,
)
from core.llm_capabilities import PROVIDERS as PROVIDER_CAPABILITIES, Auth
from core.setup_mode import BrainNotConfigured


@dataclass(frozen=True)
class LLMProviderRegistry:
    providers: Dict[str, BaseLLMProvider]
    provider: BaseLLMProvider
    openai_provider: BaseLLMProvider
    deepseek_provider: BaseLLMProvider
    local_openai_provider: BaseLLMProvider
    codex_provider: BaseLLMProvider
    claude_provider: BaseLLMProvider
    anthropic_provider: BaseLLMProvider
    ollama_provider: BaseLLMProvider


def build_default_provider_registry() -> Dict[str, BaseLLMProvider]:
    openai_provider = OpenAIProvider()
    deepseek_provider = DeepSeekProvider()
    local_openai_provider = LocalOpenAIProvider()
    codex_provider = CodexCLIProvider()
    claude_provider = ClaudeCLIProvider()
    anthropic_provider = AnthropicProvider()
    ollama_provider = OllamaProvider()
    return {
        "openai": openai_provider,
        "deepseek": deepseek_provider,
        "local_openai": local_openai_provider,
        "codex": codex_provider,
        "claude": claude_provider,
        "anthropic": anthropic_provider,
        "ollama": ollama_provider,
    }


class NullProvider(BaseLLMProvider):
    """The brain's stand-in while none is configured (first door, task 1, 2026-09-17).

    Constructing DeepSeekProvider() or OpenAIProvider() with a blank key does
    not raise. It builds a provider with no Authorization header, and a call on
    it POSTs to the real endpoint, collects the 401 and returns None — measured
    2026-09-17. Setup mode never talks to a provider, so the keyless one is not
    left in the registry for an ungated path to find: every call here raises
    BrainNotConfigured at once. LLMClient's chat paths log that once, at ERROR,
    with this reason, and return "" as their contract says.

    It lives in the registry and nowhere else, so the registry rebuild the
    setup API runs once a key is saved replaces it like any other provider.

    Not only the default provider (fix round 1): a call is routed by provider
    NAME, from `llm.routing.<task>` in the yaml before the env, so a lane can
    name a provider that is not the default. See `_keyless_cloud_providers`.
    """

    def __init__(self, provider_name: str):
        super().__init__()
        self.provider_name = provider_name
        self.log_label = "no brain"

    def is_configured(self) -> bool:
        return False

    def _refuse(self) -> BrainNotConfigured:
        return BrainNotConfigured(
            f"no brain is configured (setup mode): {self.provider_name} has no key "
            "yet, so nothing was sent — a lane reached the provider without "
            "reading core.setup_mode.lane_idle()"
        )

    async def chat_completion(self, *args: Any, **kwargs: Any) -> None:
        raise self._refuse()

    async def chat_completion_stream(self, *args: Any, **kwargs: Any) -> None:
        raise self._refuse()

    async def vision_completion(self, *args: Any, **kwargs: Any) -> None:
        raise self._refuse()


def _keyless_cloud_providers(providers: Mapping[str, BaseLLMProvider]) -> set[str]:
    """The registry entries that NEED an API key and have none.

    "Needs a key" is the capability matrix's answer (core/llm_capabilities.py,
    `Auth.API_KEY`), so a provider added there is covered here without a second
    list. "Has none" is the provider's own `is_configured()`, never a re-read of
    the env. Only the key-needing rows are asked: a CLI provider's
    `is_configured()` spawns its login probe, and the local ones spend nothing
    — both are left exactly as they are (boot's pre-warm calls
    `llm.ollama_provider.ensure_ready()`). A provider the matrix does not know
    (a plugin's) is left alone too. One that cannot say is treated as keyless:
    in setup mode the safe reading of doubt is "send nothing".
    """
    keyless: set[str] = set()
    for name, provider in providers.items():
        capability = PROVIDER_CAPABILITIES.get(str(name).strip().lower())
        if capability is None or capability.auth is not Auth.API_KEY:
            continue
        try:
            configured = bool(provider.is_configured())
        except Exception:
            configured = False
        if not configured:
            keyless.add(name)
    return keyless


def configure_provider_registry(
    providers: Mapping[str, BaseLLMProvider] | None,
    *,
    default_provider_name: str,
    logger: logging.Logger | None = None,
    brain_ready: bool = True,
) -> LLMProviderRegistry:
    next_providers = dict(providers or {})
    if not next_providers:
        next_providers = build_default_provider_registry()
    if not brain_ready:
        # Setup mode: every provider that needs a key and has none, and the
        # default by name whatever it is. Swapped by NAME, so `provider` below,
        # the openai fallback an unknown route lands on, and every route that
        # resolves to one of them by name all reach the stand-in. With a brain
        # this branch never runs: the owner's keyless OpenAI entry beside a
        # keyed DeepSeek stays the real class it has always been.
        # build_default_provider_registry() itself is untouched — the
        # capability canaries read its classes.
        brainless = _keyless_cloud_providers(next_providers)
        brainless.add(str(default_provider_name or "").strip().lower())
        for name in brainless:
            next_providers[name] = NullProvider(name)
        if logger is not None:
            logger.info(
                "setup mode: no brain configured — calls to %s are refused until a key is added",
                ", ".join(sorted(brainless)),
            )

    primary_provider = next(iter(next_providers.values()))
    openai_provider = next_providers.get("openai") or primary_provider
    deepseek_provider = next_providers.get("deepseek") or openai_provider
    local_openai_provider = next_providers.get("local_openai") or openai_provider
    codex_provider = next_providers.get("codex") or openai_provider
    claude_provider = next_providers.get("claude") or openai_provider
    anthropic_provider = next_providers.get("anthropic") or openai_provider
    ollama_provider = next_providers.get("ollama") or openai_provider
    provider = select_default_provider(
        default_provider_name=default_provider_name,
        providers=next_providers,
        fallback_provider=openai_provider,
        logger=logger,
    )
    return LLMProviderRegistry(
        providers=next_providers,
        provider=provider,
        openai_provider=openai_provider,
        deepseek_provider=deepseek_provider,
        local_openai_provider=local_openai_provider,
        codex_provider=codex_provider,
        claude_provider=claude_provider,
        anthropic_provider=anthropic_provider,
        ollama_provider=ollama_provider,
    )


def select_default_provider(
    *,
    default_provider_name: str,
    providers: Mapping[str, BaseLLMProvider],
    fallback_provider: BaseLLMProvider,
    logger: logging.Logger | None = None,
) -> BaseLLMProvider:
    provider_name = str(default_provider_name or "").strip().lower()
    if provider_name in providers:
        if logger is not None:
            logger.info("Initializing %s provider", provider_name)
        return providers[provider_name]

    fallback_name = str(
        getattr(fallback_provider, "provider_name", "") or default_provider_name or "openai"
    ).strip().lower() or "openai"
    if logger is not None:
        logger.warning(
            "Unknown provider '%s', defaulting to '%s'",
            provider_name or "unconfigured",
            fallback_name,
        )
    return fallback_provider


def build_fallback_provider_name(
    *,
    default_provider_name: str,
    openai_provider: BaseLLMProvider,
) -> str:
    return str(
        getattr(openai_provider, "provider_name", "") or default_provider_name or "openai"
    ).strip().lower() or "openai"


def collect_provider_diagnostics(
    providers: Iterable[BaseLLMProvider],
) -> list[dict[str, Any]]:
    return [provider.diagnostics() for provider in providers]


def collect_replaced_providers(
    *,
    previous_providers: Iterable[BaseLLMProvider],
    next_providers: Iterable[BaseLLMProvider],
) -> list[BaseLLMProvider]:
    next_provider_ids = {id(provider) for provider in next_providers}
    replaced: list[BaseLLMProvider] = []
    seen_provider_ids: set[int] = set()
    for provider in previous_providers:
        provider_id = id(provider)
        if provider_id in next_provider_ids or provider_id in seen_provider_ids:
            continue
        seen_provider_ids.add(provider_id)
        replaced.append(provider)
    return replaced


async def close_provider_registry(
    providers: Iterable[BaseLLMProvider],
) -> None:
    seen_provider_ids: set[int] = set()
    for provider in providers:
        provider_id = id(provider)
        if provider_id in seen_provider_ids:
            continue
        seen_provider_ids.add(provider_id)
        try:
            await provider.close()
        except Exception:
            pass
