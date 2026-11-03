from __future__ import annotations

import aiohttp
import asyncio
import logging
from typing import Any, Dict, List, Optional

from ai.credential_pool import CredentialPool
from ai.llm_openai_compatible import (
    BaseLLMProvider,
    CompletionSuccessResult,
    OpenAICompatibleProvider,
    build_openai_completion_success_result,
    is_reasoning_prone_model,
    messages_expect_json,
    retry_completion_budget,
)
from core.config import config
from core.llm_json import safe_parse_json_object


logger = logging.getLogger("ai.llm_client")


def model_rejects_temperature(model: Optional[str]) -> bool:
    """Brains that refuse a ``temperature`` keyword (gpt-5*, and OpenAI's
    o-series: o1*, o3*, o4*): the request must leave it out. ONE rule shared
    by this payload builder and her reply call (core/agent/llm_client.py
    ``_sampling_kwargs``, Batch 4 task 6), so the two can never disagree
    about which brains reject it."""
    return str(model or "").startswith(("gpt-5", "o1", "o3", "o4"))


def build_openai_chat_payload(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
    stream: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    normalized_model = str(model or "")
    if not model_rejects_temperature(normalized_model):
        payload["temperature"] = temperature
    if normalized_model.startswith(("gpt-5", "o1")):
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["max_tokens"] = max_tokens
    return payload


def build_deepseek_chat_payload(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
    stream: bool,
) -> Dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }


def build_openai_vision_payload(
    *,
    model: str,
    prompt: str,
    image_base64: str,
    temperature: float,
    max_tokens: int,
    content_type: str,
) -> Dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{content_type};base64,{image_base64}",
                        },
                    },
                ],
            }
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }


def parse_openai_vision_response_content(
    content: Any,
    *,
    log_label: str,
) -> Dict[str, Any]:
    if isinstance(content, str):
        normalized_content = content
    elif content is None:
        normalized_content = ""
    else:
        normalized_content = str(content)

    parsed = safe_parse_json_object(normalized_content)
    if isinstance(parsed, dict):
        return parsed

    logger.warning("Could not parse %s vision response as JSON: %s", log_label, normalized_content[:200])
    return {
        "description": normalized_content,
        "contextual_understanding": normalized_content,
        "suggested_response": "I see your image, thanks for sharing!",
        "humor_detected": False,
        "emotional_tone": "neutral",
    }


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI API provider."""

    def __init__(
        self,
        *,
        provider_name: str = "openai",
        log_label: str = "OpenAI",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        default_model: str = "",
        supports_vision: bool = True,
    ):
        super().__init__()
        self.provider_name = provider_name
        self.log_label = log_label
        self.base_url = str(base_url or "").rstrip("/")
        self.default_model = default_model or config.OPENAI_MODEL
        self.supports_vision = supports_vision

        # Phase 8c: support comma-separated multi-key env vars. The
        # `api_key` parameter accepts the same format ("k1,k2,k3") so
        # tests and external callers can construct multi-key providers
        # without special syntax.
        if api_key:
            raw_credential_value = api_key
        elif self.provider_name == "openai":
            raw_credential_value = getattr(config, "OPENAI_API_KEY", "")
        else:
            raw_credential_value = ""

        self.credential_pool = CredentialPool.from_env_value(raw_credential_value)
        # Backwards-compat surface: code that grep'd `provider.api_key` still
        # gets the first key; new code reads from the pool via the helpers.
        self.api_key = self.credential_pool.first_key()
        self._base_headers = {"Content-Type": "application/json"}
        # Set self.headers for any pre-Phase-8c callers that still read it
        # directly. Per-request authorization is built fresh from the pool.
        self.headers = self._headers_with_credential(self.api_key)

    def is_configured(self) -> bool:
        if self.provider_name == "local_openai":
            return bool(str(self.base_url).strip() and str(self.default_model).strip())
        return bool(self.api_key)

    def _build_chat_payload(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> Dict[str, Any]:
        return build_openai_chat_payload(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )

    # _initial_completion_state and _parse_completion_success are inherited:
    # the budget-retry fallback moved to OpenAICompatibleProvider (2026-08-04)
    # so DeepSeekProvider — the class the 4 AM organs run on — gets it too.

    async def vision_completion(
        self,
        image_base64: str,
        prompt: str,
        conversation_history: List[Dict] = None,
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 1000,
        content_type: str = "image/jpeg",
    ) -> Optional[Dict]:
        """Send vision completion request to an OpenAI-compatible API."""
        if not self.supports_vision:
            logger.warning("%s provider does not support vision completion - returning None", self.log_label)
            return None
        try:
            session = await self._get_session()
            payload = build_openai_vision_payload(
                model=model,
                prompt=prompt,
                image_base64=image_base64,
                temperature=temperature,
                max_tokens=max_tokens,
                content_type=content_type,
            )

            # Phase 8c: vision endpoints use the same credentials as
            # chat. Acquiring per-request lets the pool rotate even
            # though this path doesn't loop on retry — it still honors
            # cooldowns set by previous chat-completion failures.
            current_credential = await self._acquire_credential()
            request_headers = self._headers_with_credential(current_credential)

            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=request_headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    await self._record_credential_outcome(
                        current_credential, status_code=200, success=True,
                    )
                    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
                    return parse_openai_vision_response_content(content, log_label=self.log_label)

                error_text = await response.text()
                await self._record_credential_outcome(
                    current_credential,
                    status_code=response.status,
                    success=False,
                )
                logger.error("%s Vision API error %s: %s", self.log_label, response.status, error_text)
                return None

        except asyncio.TimeoutError:
            logger.error("%s vision request timeout", self.log_label)
            return None
        except Exception as exc:
            logger.error("%s vision request error: %s", self.log_label, exc)
            return None


class LocalOpenAIProvider(OpenAIProvider):
    """Optional local OpenAI-compatible provider for Ollama/vLLM/LM Studio-style endpoints."""

    def __init__(self, *, base_url: str = "", api_key: str = "", default_model: str = ""):
        # 2026-09-17 (first door, task 2): the keyword arguments let the setup
        # API's probe build this very class with the address and model a
        # stranger just typed, before any of it is saved. Absent — every
        # existing caller — the config is read exactly as before.
        super().__init__(
            provider_name="local_openai",
            log_label="Local LLM",
            base_url=base_url or getattr(config, "LOCAL_LLM_API_BASE", "http://127.0.0.1:11434/v1"),
            api_key=api_key or getattr(config, "LOCAL_LLM_API_KEY", ""),
            default_model=default_model or getattr(config, "LOCAL_LLM_MODEL", ""),
            supports_vision=False,
        )
        # 2026-09-02: Ollama's OpenAI-style endpoint honours reasoning_effort per
        # request ("none" keeps a Qwen brain from thinking its budget away).
        self.reasoning_effort = str(getattr(config, "LOCAL_LLM_REASONING_EFFORT", "") or "").strip().lower()

    def _build_chat_payload(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> Dict[str, Any]:
        payload = super()._build_chat_payload(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=stream,
        )
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek API provider (OpenAI-compatible)."""

    SUPPORTS_THINKING_TOGGLE = True   # thinking: {"type": "disabled"} probed live 2026-08-23

    def __init__(self, *, api_key: str = "", default_model: str = ""):
        super().__init__()
        self.provider_name = "deepseek"
        self.log_label = "DeepSeek"
        self.base_url = "https://api.deepseek.com"
        # Phase 8c: support comma-separated multi-key DEEPSEEK_API_KEY.
        # 2026-09-17 (first door, task 2): `api_key`/`default_model` mirror
        # OpenAIProvider's, so the setup API's probe can build this very class
        # with the key a stranger just pasted instead of writing it onto the
        # live config first. Absent — every existing caller — nothing changes.
        self.credential_pool = CredentialPool.from_env_value(
            api_key or getattr(config, "DEEPSEEK_API_KEY", "")
        )
        self.api_key = self.credential_pool.first_key()
        self._base_headers = {"Content-Type": "application/json"}
        self.headers = self._headers_with_credential(self.api_key)
        self.default_model = default_model or config.DEEPSEEK_MODEL
        self.supports_vision = False

    def is_configured(self) -> bool:
        # THIS instance's key, the way OpenAIProvider answers it. A provider
        # reads its credential once, at construction; re-reading the config
        # here would have said "configured" about a key this object never got
        # (and "not configured" about one it was handed directly).
        return bool(self.api_key)

    def _build_chat_payload(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> Dict[str, Any]:
        return build_deepseek_chat_payload(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
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
        """DeepSeek doesn't support vision yet."""
        logger.warning("DeepSeek provider does not support vision completion - returning None")
        return None


class AnthropicProvider(BaseLLMProvider):
    """Anthropic HTTP API provider (distinct from claude CLI subprocess auth).

    Uses the official `anthropic` Python SDK rather than aiohttp directly so
    we get correct streaming, tool-use parsing, and version-header handling
    for free. The SDK handles the differences between Anthropic's Messages
    API and OpenAI's chat shape (system field is separate from messages,
    response content is a list of typed blocks, etc.).

    Inherits BaseLLMProvider directly (not OpenAICompatibleProvider) because
    Anthropic's API is wire-incompatible — different endpoint shape, different
    auth header (x-api-key not Authorization Bearer), different response
    structure. The SDK abstracts all of that.
    """

    def __init__(self):
        super().__init__()
        self.provider_name = "anthropic"
        self.log_label = "Anthropic"
        self.base_url = getattr(config, "ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.credential_pool = CredentialPool.from_env_value(
            getattr(config, "ANTHROPIC_API_KEY", "")
        )
        self.api_key = self.credential_pool.first_key()
        self._base_headers = {"Content-Type": "application/json"}
        self.headers = self._headers_with_credential(self.api_key)
        self.default_model = getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.supports_vision = False  # Multimodal works but routing through this provider is text-only for now.
        self._sdk_client = None

    def is_configured(self) -> bool:
        return bool(getattr(config, "ANTHROPIC_API_KEY", ""))

    def _get_sdk_client(self, api_key: str):
        """Build (or reuse) the AsyncAnthropic SDK client."""
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic SDK not installed. Run: pip install anthropic"
            ) from exc
        # Rebuild if api_key changed (credential rotation) or first call.
        if self._sdk_client is None or getattr(self._sdk_client, "api_key", None) != api_key:
            self._sdk_client = AsyncAnthropic(api_key=api_key, base_url=self.base_url)
        return self._sdk_client

    @staticmethod
    def _split_system_and_messages(messages: List[Dict[str, Any]]):
        """Anthropic puts `system` in a separate field — extract role=system
        messages and concatenate them; return (system_text, remaining_messages).
        """
        system_chunks: List[str] = []
        remaining: List[Dict[str, Any]] = []
        for msg in messages:
            role = str(msg.get("role", "")).lower()
            content = msg.get("content", "")
            if role == "system":
                if isinstance(content, str) and content.strip():
                    system_chunks.append(content)
            else:
                remaining.append(msg)
        return ("\n\n".join(system_chunks) if system_chunks else None, remaining)

    async def chat_completion(
        self,
        messages: List[Dict],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        log_context: str = "general",
        telemetry_callback=None,
    ) -> Optional[str]:
        import time
        if model is None:
            model = self.default_model
        api_key = await self._acquire_credential()
        if not api_key:
            logger.warning("[%s] no Anthropic API key available (credential pool exhausted)", log_context)
            return None

        system_text, anthropic_messages = self._split_system_and_messages(messages)
        client = self._get_sdk_client(api_key)
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_text:
            kwargs["system"] = system_text

        logger.info("🧠 [%s] Sending Anthropic request (Model: %s, Context: %s)...", self.log_label, model, log_context)
        start = time.monotonic()
        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None) or 0
            logger.error("[%s] Anthropic API error %s: %s", log_context, status_code, exc)
            await self._mark_credential_failure(api_key, status_code=int(status_code) if status_code else None)
            await self._emit_request_telemetry(
                telemetry_callback, model=model, streamed=False, log_context=log_context,
                latency_seconds=time.monotonic() - start, provider_retry_count=0,
                success=False, error=str(exc),
            )
            return None

        # Successful — extract text from the first text block in the response.
        text_chunks: List[str] = []
        for block in getattr(response, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_chunks.append(getattr(block, "text", "") or "")
        response_text = "".join(text_chunks).strip()

        usage = getattr(response, "usage", None)
        usage_dict: Optional[Dict[str, Any]] = None
        if usage is not None:
            usage_dict = {
                "prompt_tokens": getattr(usage, "input_tokens", 0),
                "completion_tokens": getattr(usage, "output_tokens", 0),
                "total_tokens": (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "output_tokens", 0) or 0),
            }

        await self._emit_request_telemetry(
            telemetry_callback, model=model, streamed=False, log_context=log_context,
            latency_seconds=time.monotonic() - start, provider_retry_count=0,
            success=True, usage=usage_dict, finish_reason=getattr(response, "stop_reason", None),
        )
        return response_text if response_text else None

    async def vision_completion(
        self, image_base64, prompt, conversation_history=None, model=None,
        temperature=0.7, max_tokens=1000, content_type="image/jpeg",
    ) -> Optional[Dict]:
        """Vision support for Anthropic models — deferred for now.

        The Anthropic SDK supports image blocks; wiring this up requires
        adapting the conversation_history shape to Anthropic's image-block
        format. Not blocking primary chat completion use.
        """
        logger.warning("AnthropicProvider.vision_completion not yet implemented — returning None")
        return None


__all__ = [
    "AnthropicProvider",
    "DeepSeekProvider",
    "LocalOpenAIProvider",
    "OpenAIProvider",
    "build_deepseek_chat_payload",
    "build_openai_chat_payload",
    "build_openai_completion_success_result",
    "build_openai_vision_payload",
    "parse_openai_vision_response_content",
]
