from __future__ import annotations

from dataclasses import dataclass

import pytest

from ai.llm_client import LLMClient
from ai.llm_vision_execution import (
    PreparedVisionExecution,
    prepare_vision_execution,
    resolve_vision_fallback_model,
)


@dataclass
class _FakeProvider:
    provider_name: str
    default_model: str
    supports_vision: bool
    log_label: str


def test_prepare_vision_execution_falls_back_to_openai_default_model_for_unsupported_provider() -> None:
    local_provider = _FakeProvider(
        provider_name="local_openai",
        default_model="qwen3:14b",
        supports_vision=False,
        log_label="Local LLM",
    )
    openai_provider = _FakeProvider(
        provider_name="openai",
        default_model="gpt-4o",
        supports_vision=True,
        log_label="OpenAI",
    )
    captured: dict[str, object] = {}

    def fake_route_resolver(model_reference):
        captured["requested_model_reference"] = model_reference
        return local_provider, "qwen3:14b", {
            "requested_model_reference": model_reference,
            "effective_provider": "local_openai",
            "effective_model": "qwen3:14b",
            "effective_model_reference": "local_openai::qwen3:14b",
        }

    def fake_route_logger(*, log_context, route, streamed=False):
        captured["log_context"] = log_context
        captured["route"] = route
        captured["streamed"] = streamed

    execution = prepare_vision_execution(
        requested_model_reference=None,
        default_model_reference="local_openai::qwen3:14b",
        log_context="vision_completion",
        route_resolver=fake_route_resolver,
        route_logger=fake_route_logger,
        fallback_provider=openai_provider,
    )

    assert execution.provider is openai_provider
    assert execution.resolved_model == "gpt-4o"
    assert captured["requested_model_reference"] == "local_openai::qwen3:14b"
    assert captured["log_context"] == "vision_completion"
    assert captured["streamed"] is False
    assert captured["route"]["unsupported_provider"] == "local_openai"
    assert captured["route"]["effective_provider"] == "openai"
    assert captured["route"]["effective_model"] == "gpt-4o"
    assert captured["route"]["effective_model_reference"] == "openai::gpt-4o"
    assert captured["route"]["resolution_reason"] == "vision_capability_fallback"


def test_resolve_vision_fallback_model_preserves_requested_openai_model() -> None:
    openai_provider = _FakeProvider(
        provider_name="openai",
        default_model="gpt-4o",
        supports_vision=True,
        log_label="OpenAI",
    )

    assert (
        resolve_vision_fallback_model(
            model_reference="openai::gpt-4.1-mini",
            resolved_model="qwen3:14b",
            fallback_provider=openai_provider,
        )
        == "gpt-4.1-mini"
    )
    assert (
        resolve_vision_fallback_model(
            model_reference="gpt-4.1-mini",
            resolved_model="qwen3:14b",
            fallback_provider=openai_provider,
        )
        == "gpt-4.1-mini"
    )


@pytest.mark.asyncio
async def test_llm_client_vision_completion_delegates_to_prepared_execution(monkeypatch) -> None:
    client = LLMClient()
    captured: dict[str, object] = {}

    class _VisionProvider:
        async def vision_completion(
            self,
            image_base64,
            prompt,
            conversation_history,
            model,
            temperature,
            max_tokens,
            content_type,
        ):
            captured["image_base64"] = image_base64
            captured["prompt"] = prompt
            captured["conversation_history"] = conversation_history
            captured["model"] = model
            captured["temperature"] = temperature
            captured["max_tokens"] = max_tokens
            captured["content_type"] = content_type
            return {"description": "ok"}

    monkeypatch.setattr(
        "ai.llm_client.prepare_vision_execution",
        lambda **kwargs: PreparedVisionExecution(
            provider=_VisionProvider(),
            resolved_model="gpt-4o",
            route={"effective_provider": "openai"},
        ),
    )

    result = await client.vision_completion(
        image_base64="abc123",
        prompt="Describe the image",
        conversation_history=[{"role": "user", "content": "Earlier"}],
        model="local_openai::qwen3:14b",
        temperature=0.1,
        max_tokens=200,
        content_type="image/png",
    )

    assert result == {"description": "ok"}
    assert captured == {
        "image_base64": "abc123",
        "prompt": "Describe the image",
        "conversation_history": [{"role": "user", "content": "Earlier"}],
        "model": "gpt-4o",
        "temperature": 0.1,
        "max_tokens": 200,
        "content_type": "image/png",
    }
