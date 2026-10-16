from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Mapping, Optional

from ai.llm_openai_compatible import BaseLLMProvider, is_text_response_log_context
from ai.llm_routing import (
    build_provider_diagnostics_by_name,
    build_routing_runtime_status,
    describe_model_route,
    infer_provider_from_model_name,
    parse_model_reference,
)


def resolve_provider_and_model(
    model_reference: Optional[str],
    *,
    default_provider_name: str,
    fallback_provider_name: str,
    providers: Mapping[str, BaseLLMProvider],
    fallback_provider: BaseLLMProvider,
    provider_diagnostics: Iterable[Mapping[str, Any]] | None = None,
    provider_source: Optional[str] = None,
    model_source: Optional[str] = None,
    providers_by_name: Optional[Dict[str, Dict[str, Any]]] = None,
    warn_on_unknown: bool = True,
    logger: Optional[logging.Logger] = None,
) -> tuple[BaseLLMProvider, Optional[str], Dict[str, Any]]:
    provider_diagnostics_by_name = providers_by_name or build_provider_diagnostics_by_name(
        provider_diagnostics
        or [provider.diagnostics() for provider in (providers or {}).values()]
    )
    route = describe_model_route(
        model_reference,
        default_provider_name=default_provider_name,
        fallback_provider_name=fallback_provider_name,
        provider_source=provider_source,
        model_source=model_source,
        providers_by_name=provider_diagnostics_by_name,
    )

    requested_provider_name, raw_model = parse_model_reference(model_reference)
    inferred_provider_name = infer_provider_from_model_name(raw_model)
    selected_provider_name = str(
        requested_provider_name or inferred_provider_name or default_provider_name
    ).strip().lower()
    provider_name = str(route.get("effective_provider") or fallback_provider_name).strip().lower()
    provider = providers.get(provider_name)

    if provider is None:
        _warn_unknown_provider(
            logger=logger,
            selected_provider_name=selected_provider_name,
            fallback_provider_name=provider_name,
            warn_on_unknown=warn_on_unknown,
        )
        provider = fallback_provider
    elif warn_on_unknown and str(route.get("resolution_reason") or "") == "unknown_provider_fallback":
        _warn_unknown_provider(
            logger=logger,
            selected_provider_name=selected_provider_name,
            fallback_provider_name=provider_name,
            warn_on_unknown=warn_on_unknown,
        )

    return provider, str(route.get("effective_model") or "").strip() or None, route


def log_route_resolution(
    *,
    logger: logging.Logger,
    log_context: str,
    route: Mapping[str, Any],
    streamed: bool = False,
) -> None:
    requested_reference = str(route.get("requested_model_reference") or "").strip() or "unconfigured"
    effective_reference = str(route.get("effective_model_reference") or "").strip() or "unconfigured"
    provider_source = str(route.get("requested_provider_source") or "unknown")
    model_source = str(route.get("requested_model_source") or "unknown")
    suffix = f" provider_source={provider_source} model_source={model_source}"
    if streamed:
        suffix += " streamed=true"
    message = (
        f"[{log_context}] LLM route requested={requested_reference} "
        f"effective={effective_reference}{suffix}"
    )
    if bool(route.get("route_changed")):
        message += f" resolution={route.get('resolution_reason') or 'changed'}"

    if (
        bool(route.get("route_changed"))
        or is_text_response_log_context(log_context)
        or str(log_context).startswith(
            (
                "voice_reply_generation_",
                "operator_voice_reply_generation_",
                "agentic_finalizer_",
            )
        )
    ):
        logger.info(message)
    else:
        logger.debug(message)


def build_llm_client_runtime_status(
    *,
    default_provider_name: str,
    fallback_provider_name: Optional[str] = None,
    provider_diagnostics: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    return build_routing_runtime_status(
        default_provider_name=default_provider_name,
        fallback_provider_name=fallback_provider_name,
        providers=list(provider_diagnostics or []),
    )


def _warn_unknown_provider(
    *,
    logger: Optional[logging.Logger],
    selected_provider_name: str,
    fallback_provider_name: str,
    warn_on_unknown: bool,
) -> None:
    if not warn_on_unknown or logger is None:
        return
    logger.warning(
        "Unknown provider '%s', defaulting to '%s'",
        selected_provider_name,
        str(fallback_provider_name or "openai").strip().lower() or "openai",
    )
