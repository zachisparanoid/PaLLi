from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ai.llm_openai_compatible import BaseLLMProvider
from ai.llm_runtime_tracking import LLMRuntimeActivityRecorder


logger = logging.getLogger("ai.llm_client")

LLMTelemetryCallback = Callable[[Dict[str, Any]], Awaitable[None]]
RouteResolver = Callable[[Optional[str]], tuple[BaseLLMProvider, Optional[str], Dict[str, Any]]]
RouteLogger = Callable[..., None]
DeltaCallback = Callable[[str], Awaitable[None]]


@dataclass
class LLMProviderRequest:
    messages: List[Dict[str, Any]]
    temperature: float
    max_tokens: int
    log_context: str
    streamed: bool = False
    on_delta: Optional[DeltaCallback] = None


@dataclass
class PreparedLLMRouteExecution:
    requested_model_reference: Optional[str]
    provider: BaseLLMProvider
    resolved_model: Optional[str]
    resolved_provider_name: str
    route: Dict[str, Any]
    runtime_activity: LLMRuntimeActivityRecorder


def prepare_llm_route_execution(
    *,
    requested_model_reference: Optional[str],
    default_model_reference: Optional[str],
    log_context: str,
    streamed: bool,
    telemetry_callback: Optional[LLMTelemetryCallback],
    default_provider_name: str,
    route_resolver: RouteResolver,
    route_logger: RouteLogger,
) -> PreparedLLMRouteExecution:
    model_reference = requested_model_reference if requested_model_reference is not None else default_model_reference
    provider, resolved_model, route = route_resolver(model_reference)
    route_logger(log_context=log_context, route=route, streamed=streamed)
    resolved_provider_name = str(
        route.get("effective_provider") or getattr(provider, "provider_name", "") or default_provider_name
    ).strip()
    runtime_activity = LLMRuntimeActivityRecorder(
        telemetry_callback=telemetry_callback,
        requested_model_reference=model_reference,
        resolved_provider=resolved_provider_name,
        resolved_model=resolved_model,
    )
    return PreparedLLMRouteExecution(
        requested_model_reference=model_reference,
        provider=provider,
        resolved_model=resolved_model,
        resolved_provider_name=resolved_provider_name,
        route=dict(route or {}),
        runtime_activity=runtime_activity,
    )


async def execute_llm_provider_request(
    execution: PreparedLLMRouteExecution,
    request: LLMProviderRequest,
) -> str:
    try:
        content = await _invoke_provider(
            provider=execution.provider,
            request=request,
            resolved_model=execution.resolved_model,
            telemetry_callback=execution.runtime_activity.callback,
        )
    except Exception as exc:
        failure_label = "streaming chat completion" if request.streamed else "chat completion"
        logger.error("[%s] LLM %s failed: %s", request.log_context, failure_label, exc, exc_info=True)
        await execution.runtime_activity.record_failure_if_missing(
            log_context=request.log_context,
            streamed=request.streamed,
            error=str(exc),
        )
        return ""

    if content is None:
        if request.streamed:
            logger.warning("[%s] LLM provider returned None content for stream", request.log_context)
        else:
            logger.warning("[%s] LLM provider returned None content", request.log_context)
        await execution.runtime_activity.record_failure_if_missing(
            log_context=request.log_context,
            streamed=request.streamed,
            error="provider_returned_none",
        )
        return ""

    if not isinstance(content, str):
        try:
            return str(content)
        except Exception:
            return ""

    return content


async def _invoke_provider(
    *,
    provider: BaseLLMProvider,
    request: LLMProviderRequest,
    resolved_model: Optional[str],
    telemetry_callback: LLMTelemetryCallback,
) -> Optional[str]:
    if request.streamed:
        return await provider.chat_completion_stream(
            request.messages,
            resolved_model,
            request.temperature,
            request.max_tokens,
            request.log_context,
            request.on_delta,
            telemetry_callback,
        )
    return await provider.chat_completion(
        request.messages,
        resolved_model,
        request.temperature,
        request.max_tokens,
        request.log_context,
        telemetry_callback,
    )
