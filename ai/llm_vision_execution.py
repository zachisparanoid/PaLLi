from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from ai.llm_openai_compatible import BaseLLMProvider
from ai.llm_routing import (
    _build_model_reference,
    _provider_label,
    infer_provider_from_model_name,
    parse_model_reference,
)


logger = logging.getLogger("ai.llm_client")

VisionRouteResolver = Callable[[Optional[str]], tuple[BaseLLMProvider, Optional[str], Dict[str, Any]]]
RouteLogger = Callable[..., None]


@dataclass
class PreparedVisionExecution:
    provider: BaseLLMProvider
    resolved_model: Optional[str]
    route: Dict[str, Any]


def prepare_vision_execution(
    *,
    requested_model_reference: Optional[str],
    default_model_reference: Optional[str],
    log_context: str,
    route_resolver: VisionRouteResolver,
    route_logger: RouteLogger,
    fallback_provider: Optional[BaseLLMProvider],
) -> PreparedVisionExecution:
    model_reference = (
        requested_model_reference
        if requested_model_reference is not None
        else default_model_reference
    )
    provider, resolved_model, route = route_resolver(model_reference)
    provider, resolved_model, route = apply_vision_capability_fallback(
        model_reference=model_reference,
        provider=provider,
        resolved_model=resolved_model,
        route=route,
        fallback_provider=fallback_provider,
    )
    route_logger(log_context=log_context, route=route, streamed=False)
    return PreparedVisionExecution(
        provider=provider,
        resolved_model=resolved_model,
        route=route,
    )


def apply_vision_capability_fallback(
    *,
    model_reference: Optional[str],
    provider: BaseLLMProvider,
    resolved_model: Optional[str],
    route: Mapping[str, Any],
    fallback_provider: Optional[BaseLLMProvider],
) -> tuple[BaseLLMProvider, Optional[str], Dict[str, Any]]:
    route_data = dict(route or {})
    if getattr(provider, "supports_vision", False):
        return provider, resolved_model, route_data

    if fallback_provider is None or fallback_provider is provider:
        logger.warning(
            "%s provider does not support vision and no alternate vision provider is available",
            getattr(provider, "log_label", getattr(provider, "provider_name", "LLM")),
        )
        return provider, resolved_model, route_data

    if not getattr(fallback_provider, "supports_vision", False):
        logger.warning(
            "%s provider does not support vision and fallback provider %s also lacks vision support",
            getattr(provider, "log_label", getattr(provider, "provider_name", "LLM")),
            getattr(fallback_provider, "provider_name", "unknown"),
        )
        return provider, resolved_model, route_data

    provider_name = str(getattr(provider, "provider_name", "") or "").strip().lower()
    fallback_provider_name = str(
        getattr(fallback_provider, "provider_name", "") or ""
    ).strip().lower()
    fallback_model = resolve_vision_fallback_model(
        model_reference=model_reference,
        resolved_model=resolved_model,
        fallback_provider=fallback_provider,
    )

    logger.warning(
        "%s provider does not support vision; falling back to %s",
        getattr(provider, "log_label", provider_name or "LLM"),
        getattr(fallback_provider, "log_label", fallback_provider_name or "fallback provider"),
    )

    route_data.update(
        {
            "unsupported_provider": provider_name,
            "effective_provider": fallback_provider_name,
            "effective_provider_label": _provider_label(fallback_provider_name),
            "effective_provider_source": "vision_capability_fallback",
            "effective_model": str(fallback_model or ""),
            "effective_model_reference": str(
                _build_model_reference(fallback_provider_name, fallback_model) or ""
            ),
            "route_changed": bool(
                provider_name != fallback_provider_name
                or str(route_data.get("effective_model") or "").strip()
                != str(fallback_model or "").strip()
            ),
            "resolution_reason": "vision_capability_fallback",
        }
    )
    return fallback_provider, fallback_model, route_data


def resolve_vision_fallback_model(
    *,
    model_reference: Optional[str],
    resolved_model: Optional[str],
    fallback_provider: BaseLLMProvider,
) -> Optional[str]:
    requested_provider_name, requested_model = parse_model_reference(model_reference)
    inferred_provider_name = infer_provider_from_model_name(requested_model)
    fallback_provider_name = str(
        getattr(fallback_provider, "provider_name", "") or ""
    ).strip().lower()

    if requested_model and fallback_provider_name in {
        str(requested_provider_name or "").strip().lower(),
        str(inferred_provider_name or "").strip().lower(),
    }:
        return requested_model

    fallback_default_model = str(
        getattr(fallback_provider, "default_model", "") or ""
    ).strip()
    if fallback_default_model:
        return fallback_default_model

    return str(resolved_model or "").strip() or None
