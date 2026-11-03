"""
LLM client with multi-provider support (OpenAI, DeepSeek, etc.)
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ai.llm_openai_compatible import BaseLLMProvider, is_reasoning_prone_model
from ai.llm_provider_registry import (
    build_default_provider_registry,
    build_fallback_provider_name,
    close_provider_registry,
    collect_replaced_providers,
    collect_provider_diagnostics,
    configure_provider_registry,
    select_default_provider,
)
from ai.llm_providers import DeepSeekProvider, LocalOpenAIProvider, OpenAIProvider
from ai.llm_provider_resolution import (
    build_llm_client_runtime_status,
    log_route_resolution as log_llm_route_resolution,
    resolve_provider_and_model as resolve_llm_provider_and_model,
)
from ai.llm_route_execution import (
    LLMProviderRequest,
    execute_llm_provider_request,
    prepare_llm_route_execution,
)
from ai.llm_vision_execution import prepare_vision_execution
from ai.llm_runtime_tracking import (
    normalize_owner_user_id,
    runtime_activity_owner_scope as llm_runtime_activity_owner_scope,
)
from core.agent.payload_masking import for_provider
from core.config import config
from core.setup_mode import brain_ready
from ai.llm_routing import (
    get_task_model,
)

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified LLM client that routes to configured provider"""

    def __init__(self):
        self.default_provider_name = getattr(config, "LLM_PROVIDER", "openai").lower()
        self.set_provider_registry({})

    def set_provider_registry(self, providers: Dict[str, BaseLLMProvider]) -> None:
        # Setup mode (first door, task 1, 2026-09-17): with no brain the default
        # provider is a NullProvider. Read HERE, fresh, because this is the one
        # funnel every install passes through — this constructor, the plugin
        # runtime's boot install, a settings hot-reload, and the setup API's
        # rebuild once a key is saved, which is what swaps the real one back.
        registry = configure_provider_registry(
            providers,
            default_provider_name=self.default_provider_name,
            logger=logger,
            brain_ready=brain_ready(),
        )
        self.providers = registry.providers
        self.provider = registry.provider
        self.openai_provider = registry.openai_provider
        self.deepseek_provider = registry.deepseek_provider
        self.local_openai_provider = registry.local_openai_provider
        self.codex_provider = registry.codex_provider
        self.claude_provider = registry.claude_provider
        self.anthropic_provider = registry.anthropic_provider
        self.ollama_provider = registry.ollama_provider

    async def activate_brain(self, provider_name: str) -> None:
        """A brain arrived — swap the real providers in, without a restart.

        The setup API's half of the hot swap (first door, task 2, 2026-09-17).
        The caller puts the key on `config` and flips `config.brain_ready`
        FIRST, because both are read here: every provider reads its credential
        at construction, and `set_provider_registry` reads the verdict fresh to
        decide whether to keep the setup-mode stand-in. `default_provider_name`
        is read once, in `__init__`, so it is set here rather than left at
        whatever the boot with no brain chose.
        """
        self.default_provider_name = (
            str(provider_name or "").strip().lower() or self.default_provider_name
        )
        await self.replace_provider_registry(build_default_provider_registry())

    async def replace_provider_registry(self, providers: Dict[str, BaseLLMProvider]) -> None:
        previous_providers = list(getattr(self, "providers", {}).values())
        self.set_provider_registry(providers)
        replaced_providers = collect_replaced_providers(
            previous_providers=previous_providers,
            next_providers=self.providers.values(),
        )
        await close_provider_registry(replaced_providers)

    @staticmethod
    def _normalize_owner_user_id(value: Any) -> Optional[int]:
        return normalize_owner_user_id(value)

    def runtime_activity_owner_scope(self, *, owner_user_id: Optional[int]) -> Any:
        return llm_runtime_activity_owner_scope(
            owner_user_id=self._normalize_owner_user_id(owner_user_id)
        )

    def _initialize_provider(self) -> BaseLLMProvider:
        return select_default_provider(
            default_provider_name=self.default_provider_name,
            providers=self.providers,
            fallback_provider=self.openai_provider,
            logger=logger,
        )

    def _routing_fallback_provider_name(self) -> str:
        return build_fallback_provider_name(
            default_provider_name=self.default_provider_name,
            openai_provider=self.openai_provider,
        )

    def _provider_diagnostics(self) -> List[Dict[str, Any]]:
        return collect_provider_diagnostics(self.providers.values())

    def _resolve_provider_and_model(
        self,
        model_reference: Optional[str],
        *,
        provider_source: Optional[str] = None,
        model_source: Optional[str] = None,
        providers_by_name: Optional[Dict[str, Dict[str, Any]]] = None,
        warn_on_unknown: bool = True,
    ) -> tuple[BaseLLMProvider, Optional[str], Dict[str, Any]]:
        return resolve_llm_provider_and_model(
            model_reference,
            default_provider_name=self.default_provider_name,
            fallback_provider_name=self._routing_fallback_provider_name(),
            providers=self.providers,
            fallback_provider=self.openai_provider,
            provider_diagnostics=self._provider_diagnostics(),
            provider_source=provider_source,
            model_source=model_source,
            providers_by_name=providers_by_name,
            warn_on_unknown=warn_on_unknown,
            logger=logger,
        )

    def _log_route_resolution(
        self,
        *,
        log_context: str,
        route: Dict[str, Any],
        streamed: bool = False,
    ) -> None:
        log_llm_route_resolution(
            logger=logger,
            log_context=log_context,
            route=route,
            streamed=streamed,
        )

    def get_runtime_status(self) -> Dict[str, Any]:
        return build_llm_client_runtime_status(
            default_provider_name=self.default_provider_name,
            fallback_provider_name=self._routing_fallback_provider_name(),
            provider_diagnostics=self._provider_diagnostics(),
        )

    async def chat_completion(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 3000,
        log_context: str = "general",
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> str:
        """
        Send chat completion request via configured provider.

        This method never raises on upstream/provider failures and never returns None.
        On failure it returns an empty string so callers can treat it as a non-response.
        """
        # Batch 1 (2026-09-26): every background lane's request leaves through
        # here — redacted (known literals) then masked (secret-shaped strings
        # -> [MASKED:<hex>]). A copy; the caller's list is untouched.
        messages = for_provider(messages)
        execution = prepare_llm_route_execution(
            requested_model_reference=model,
            default_model_reference=get_task_model("response"),
            log_context=log_context,
            streamed=False,
            telemetry_callback=telemetry_callback,
            default_provider_name=self.default_provider_name,
            route_resolver=self._resolve_provider_and_model,
            route_logger=self._log_route_resolution,
        )
        return await execute_llm_provider_request(
            execution,
            LLMProviderRequest(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                log_context=log_context,
            ),
        )

    async def chat_completion_stream(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 3000,
        log_context: str = "general",
        on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
        telemetry_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> str:
        """
        Send a streamed chat completion request via the configured provider.

        This method mirrors `chat_completion`: it never raises on upstream/provider
        failures and never returns None.
        """
        messages = for_provider(messages)   # Batch 1: the same seam as chat_completion
        execution = prepare_llm_route_execution(
            requested_model_reference=model,
            default_model_reference=get_task_model("response"),
            log_context=log_context,
            streamed=True,
            telemetry_callback=telemetry_callback,
            default_provider_name=self.default_provider_name,
            route_resolver=self._resolve_provider_and_model,
            route_logger=self._log_route_resolution,
        )
        return await execute_llm_provider_request(
            execution,
            LLMProviderRequest(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                log_context=log_context,
                streamed=True,
                on_delta=on_delta,
            ),
        )

    async def vision_completion(
        self,
        image_base64: str,
        prompt: str,
        conversation_history: List[Dict] = None,
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        content_type: str = "image/jpeg",
    ) -> Optional[Dict]:
        """Send vision completion request via configured provider"""
        # Batch 1 (2026-09-26): the words are masked; the image bytes never
        # are (masking inside base64 would corrupt the picture).
        prompt = for_provider(prompt)
        conversation_history = for_provider(conversation_history)
        execution = prepare_vision_execution(
            requested_model_reference=model,
            default_model_reference=get_task_model("vision"),
            log_context="vision_completion",
            route_resolver=self._resolve_provider_and_model,
            route_logger=self._log_route_resolution,
            fallback_provider=self.openai_provider,
        )
        return await execution.provider.vision_completion(
            image_base64,
            prompt,
            conversation_history,
            execution.resolved_model,
            temperature,
            max_tokens,
            content_type,
        )

    async def close(self):
        """Close all provider sessions"""
        await close_provider_registry(self.providers.values())


llm = LLMClient()
