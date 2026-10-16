from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Mapping, Optional

from core.config import config

logger = logging.getLogger(__name__)


def _settings_routing():
    """Return the current RoutingConfig from the settings service, or None if not booted."""
    try:
        from core.settings_boot import settings_service
        snapshot = settings_service.snapshot()
        return snapshot.llm if snapshot else None
    except Exception:
        return None


def _settings_default_provider() -> str | None:
    """Return the default provider from settings, or None if not booted."""
    llm = _settings_routing()
    return llm.default_provider if llm else None


TASK_PROVIDER_FIELDS = {
    "response": "LLM_RESPONSE_PROVIDER",
    "analysis": "LLM_ANALYSIS_PROVIDER",
    "memory": "LLM_MEMORY_PROVIDER",
    "decision": "LLM_DECISION_PROVIDER",
    "proactive": "LLM_PROACTIVE_PROVIDER",
    "reflection": "LLM_REFLECTION_PROVIDER",
    "web_search": "LLM_WEB_SEARCH_PROVIDER",
    "vision": "LLM_VISION_PROVIDER",
    "appraisal": "LLM_APPRAISAL_PROVIDER",
}

TASK_MODEL_FIELDS = {
    "response": "LLM_RESPONSE_MODEL",
    "analysis": "LLM_ANALYSIS_MODEL",
    "memory": "LLM_MEMORY_MODEL",
    "decision": "LLM_DECISION_MODEL",
    "proactive": "LLM_PROACTIVE_MODEL",
    "reflection": "LLM_REFLECTION_MODEL",
    "web_search": "LLM_WEB_SEARCH_MODEL",
    "vision": "LLM_VISION_MODEL",
    "appraisal": "LLM_APPRAISAL_MODEL",
    "present_moment": "LLM_PRESENT_MOMENT_MODEL",
}

TASK_ROUTING_SEVERITY = {
    "response": "error",
    "analysis": "error",
    "decision": "error",
    "memory": "warning",
    "proactive": "warning",
    "reflection": "warning",
    "web_search": "info",
    "vision": "info",
    # Post-reply stake appraiser — a missed route degrades quietly (the
    # appraisal is fire-and-forget), so warning, not error.
    "appraisal": "warning",
    "present_moment": "warning",
}


def _provider_label(provider_name: str, provider_diagnostics: Optional[Dict[str, Any]] = None) -> str:
    label = str((provider_diagnostics or {}).get("label") or "").strip()
    if label:
        return label
    normalized = str(provider_name or "").strip().lower()
    if normalized == "openai":
        return "OpenAI"
    if normalized == "local_openai":
        return "Local OpenAI"
    if normalized == "deepseek":
        return "DeepSeek"
    if normalized == "codex":
        return "Codex CLI"
    clean = normalized.replace("_", " ").strip()
    return clean.title() if clean else "Unknown"


def _normalize_route_source(source: Optional[str]) -> str:
    clean = str(source or "").strip().lower()
    return clean or "unknown"


def _default_model_for_provider(provider_name: str, fallback: Optional[str] = None) -> Optional[str]:
    provider = str(provider_name or _settings_default_provider() or getattr(config, "LLM_PROVIDER", "openai")).strip().lower()
    if provider == "deepseek":
        return getattr(config, "DEEPSEEK_MODEL", None) or fallback
    if provider == "local_openai":
        return getattr(config, "LOCAL_LLM_MODEL", None) or fallback
    if provider == "codex":
        return getattr(config, "CODEX_MODEL", None) or fallback
    if provider == "anthropic":
        return getattr(config, "ANTHROPIC_MODEL", None) or fallback
    # 2026-09-02 (DEFECT_RECORD §22): the first minute on the desktop's Ollama —
    # `arbiter_freshness` and `daydream_tick` (no routing entry) resolved to
    # OPENAI_MODEL ("gpt-4o") and 404'd, because this function knew four
    # providers and answered the OpenAI default for every other one, ahead
    # of the caller's own fallback. The default provider's model wins; a
    # provider with no model field of its own defers to the fallback first.
    if provider == "ollama":
        return getattr(config, "OLLAMA_MODEL", None) or fallback
    if provider == "claude":
        return getattr(config, "CLAUDE_CLI_MODEL", None) or fallback
    if provider == "openai":
        return getattr(config, "OPENAI_MODEL", None) or fallback
    return fallback or getattr(config, "OPENAI_MODEL", None)


def _build_model_reference(provider_name: Optional[str], model_name: Optional[str]) -> Optional[str]:
    provider = str(provider_name or "").strip().lower()
    model = str(model_name or "").strip()
    if not model:
        return None
    if not provider:
        return model
    return f"{provider}::{model}"


def parse_model_reference(model_reference: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    raw = str(model_reference or "").strip()
    if not raw:
        return None, None
    if "::" not in raw:
        return None, raw
    provider_name, model_name = raw.split("::", 1)
    provider = provider_name.strip().lower() or None
    model = model_name.strip() or None
    return provider, model


def infer_provider_from_model_name(model_name: Optional[str]) -> Optional[str]:
    model = str(model_name or "").strip().lower()
    if not model:
        return None
    if model.startswith("deepseek-"):
        return "deepseek"
    if model.startswith(("gpt-", "o1", "o3")):
        return "openai"
    return None


def _task_routing_severity(task_name: str) -> str:
    return str(TASK_ROUTING_SEVERITY.get(str(task_name or "").strip().lower(), "warning") or "warning")


def get_llm_model(
    default_openai: Optional[str] = None,
    default_deepseek: Optional[str] = None,
    default_local_openai: Optional[str] = None,
) -> Optional[str]:
    """
    Select an appropriate model name based on the configured LLM provider.

    - For OpenAI, prefer the explicit `default_openai` override, then `config.OPENAI_MODEL`.
    - For DeepSeek, prefer `default_deepseek`, then `config.DEEPSEEK_MODEL`.
    - For unknown providers, fall back to the OpenAI default to preserve existing behavior.
    """
    # Phase 2: prefer settings default provider
    provider = (_settings_default_provider() or getattr(config, "LLM_PROVIDER", "openai")).lower()

    if provider == "deepseek":
        if default_deepseek:
            return default_deepseek
        return getattr(config, "DEEPSEEK_MODEL", None)
    if provider == "local_openai":
        if default_local_openai:
            return default_local_openai
        return getattr(config, "LOCAL_LLM_MODEL", None)

    if provider == "ollama":            # DEFECT_RECORD §22 (2026-09-02)
        return getattr(config, "OLLAMA_MODEL", None) or default_openai or getattr(config, "OPENAI_MODEL", None)
    if provider == "claude":
        return getattr(config, "CLAUDE_CLI_MODEL", None) or default_openai or getattr(config, "OPENAI_MODEL", None)
    if default_openai:
        return default_openai
    return getattr(config, "OPENAI_MODEL", None)


def get_task_provider(task: str, fallback: Optional[str] = None) -> str:
    task_key = (task or "").lower()
    # Phase 2: read from settings YAML first
    llm = _settings_routing()
    if llm is not None:
        entry = getattr(llm.routing, task_key, None)
        if entry is not None:
            return entry.provider
    # Fallback: read from env (pre-Phase 2 behavior)
    field_name = TASK_PROVIDER_FIELDS.get(task_key)
    if field_name:
        configured = str(getattr(config, field_name, "") or "").strip().lower()
        if configured:
            return configured
    return str(fallback or getattr(config, "LLM_PROVIDER", "openai")).strip().lower()


def get_task_provider_details(task: str, fallback: Optional[str] = None) -> Dict[str, str]:
    task_key = (task or "").lower()
    # Phase 2: read from settings YAML first
    llm = _settings_routing()
    if llm is not None:
        entry = getattr(llm.routing, task_key, None)
        if entry is not None:
            return {
                "provider": entry.provider,
                "source": "settings_yaml",
                "field_name": f"llm.routing.{task_key}.provider",
            }
    # Fallback: read from env (pre-Phase 2 behavior)
    field_name = TASK_PROVIDER_FIELDS.get(task_key)
    if field_name:
        configured = str(getattr(config, field_name, "") or "").strip().lower()
        if configured:
            return {
                "provider": configured,
                "source": "task_override",
                "field_name": field_name,
            }
    clean_fallback = str(fallback or "").strip().lower()
    if clean_fallback:
        return {
            "provider": clean_fallback,
            "source": "fallback_provider",
            "field_name": "",
        }
    return {
        "provider": str(_settings_default_provider() or getattr(config, "LLM_PROVIDER", "openai")).strip().lower(),
        "source": "default_provider",
        "field_name": "LLM_PROVIDER",
    }


def get_task_model(task: str, fallback: Optional[str] = None) -> Optional[str]:
    task_key = (task or "").lower()
    # Phase 2: read from settings YAML first
    llm = _settings_routing()
    if llm is not None:
        entry = getattr(llm.routing, task_key, None)
        if entry is not None:
            provider_name = entry.provider
            model_name = entry.model
            return _build_model_reference(provider_name, model_name)
    # Fallback: read from env (pre-Phase 2 behavior)
    provider_name = get_task_provider(task_key)
    field_name = TASK_MODEL_FIELDS.get(task_key)
    explicit_model = getattr(config, field_name, None) if field_name else None
    model = str(explicit_model or "").strip() or _default_model_for_provider(provider_name, fallback=fallback)
    return _build_model_reference(provider_name, model)


def get_task_model_details(task: str, fallback: Optional[str] = None) -> Dict[str, str]:
    task_key = (task or "").lower()
    provider_details = get_task_provider_details(task_key, fallback=fallback)
    provider_name = str(provider_details.get("provider") or "").strip().lower()
    field_name = TASK_MODEL_FIELDS.get(task_key)
    explicit_model = str(getattr(config, field_name, "") or "").strip() if field_name else ""
    if explicit_model:
        model_name = explicit_model
        model_source = "task_override"
        model_field_name = field_name or ""
    else:
        model_name = str(_default_model_for_provider(provider_name, fallback=fallback) or "").strip()
        if model_name:
            if str(fallback or "").strip() and provider_details.get("source") == "fallback_provider":
                model_source = "fallback_model"
            else:
                model_source = "provider_default"
        else:
            model_source = "missing"
        model_field_name = ""
    return {
        **provider_details,
        "model": model_name,
        "model_source": model_source,
        "model_field_name": model_field_name,
        "model_reference": str(_build_model_reference(provider_name, model_name) or ""),
    }


def build_provider_diagnostics_by_name(
    providers: Iterable[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    providers_by_name: Dict[str, Dict[str, Any]] = {}
    for provider in providers or []:
        provider_name = str((provider or {}).get("provider") or "").strip().lower()
        if provider_name:
            providers_by_name[provider_name] = dict(provider)
    return providers_by_name


def describe_model_route(
    model_reference: Optional[str],
    *,
    default_provider_name: str,
    fallback_provider_name: Optional[str] = None,
    provider_source: Optional[str] = None,
    model_source: Optional[str] = None,
    providers_by_name: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    provider_diagnostics_by_name = dict(providers_by_name or {})
    requested_provider_name, requested_model_name = parse_model_reference(model_reference)
    inferred_provider_name = infer_provider_from_model_name(requested_model_name)

    normalized_default_provider = str(default_provider_name or "openai").strip().lower() or "openai"
    normalized_fallback_provider = (
        str(fallback_provider_name or normalized_default_provider or "openai").strip().lower() or "openai"
    )
    requested_provider = str(
        requested_provider_name or inferred_provider_name or normalized_default_provider
    ).strip().lower()
    effective_provider_name = requested_provider
    if effective_provider_name not in provider_diagnostics_by_name:
        effective_provider_name = normalized_fallback_provider

    effective_model_name = str(requested_model_name or "").strip()

    normalized_provider_source = _normalize_route_source(provider_source)
    if normalized_provider_source == "unknown":
        if requested_provider_name:
            normalized_provider_source = "model_reference"
        elif inferred_provider_name:
            normalized_provider_source = "model_inference"
        else:
            normalized_provider_source = "default_provider"

    normalized_model_source = _normalize_route_source(model_source)
    if normalized_model_source == "unknown":
        normalized_model_source = "model_reference" if requested_model_name else "missing"

    requested_model = str(requested_model_name or "").strip()
    requested_reference = str(
        _build_model_reference(requested_provider, requested_model) if requested_model else model_reference or ""
    ).strip()
    effective_reference = str(_build_model_reference(effective_provider_name, effective_model_name) or "").strip()

    requested_provider_diagnostics = provider_diagnostics_by_name.get(requested_provider)
    effective_provider_diagnostics = provider_diagnostics_by_name.get(effective_provider_name)
    requested_provider_known = requested_provider_diagnostics is not None
    effective_provider_known = effective_provider_diagnostics is not None
    requested_provider_configured = bool((requested_provider_diagnostics or {}).get("configured"))
    effective_provider_configured = bool((effective_provider_diagnostics or {}).get("configured"))
    route_changed = requested_reference != effective_reference

    resolution_reason = "matched_requested_route"
    if route_changed:
        if requested_provider and not requested_provider_known:
            resolution_reason = "unknown_provider_fallback"
        elif requested_provider != effective_provider_name:
            resolution_reason = "provider_selection_changed"
        else:
            resolution_reason = "model_selection_changed"

    return {
        "requested_provider": requested_provider,
        "requested_provider_label": _provider_label(requested_provider, requested_provider_diagnostics),
        "requested_provider_known": requested_provider_known,
        "requested_provider_configured": requested_provider_configured,
        "requested_provider_source": normalized_provider_source,
        "requested_model": requested_model,
        "requested_model_source": normalized_model_source,
        "requested_model_reference": requested_reference,
        "effective_provider": effective_provider_name,
        "effective_provider_label": _provider_label(effective_provider_name, effective_provider_diagnostics),
        "effective_provider_known": effective_provider_known,
        "effective_provider_configured": effective_provider_configured,
        "effective_provider_source": (
            "openai_fallback" if effective_provider_name != requested_provider else "requested_provider"
        ),
        "effective_model": effective_model_name,
        "effective_model_reference": effective_reference,
        "route_changed": route_changed,
        "resolution_reason": resolution_reason,
    }


def describe_task_route(
    task_name: str,
    *,
    default_provider_name: str,
    fallback_provider_name: Optional[str] = None,
    fallback: Optional[str] = None,
    providers_by_name: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    details = get_task_model_details(task_name, fallback=fallback)
    route = describe_model_route(
        details.get("model_reference"),
        default_provider_name=default_provider_name,
        fallback_provider_name=fallback_provider_name,
        provider_source=details.get("source"),
        model_source=details.get("model_source"),
        providers_by_name=providers_by_name,
    )
    return {
        **route,
        "task": str(task_name or "").strip().lower(),
        "provider": route["requested_provider"],
        "provider_label": route["requested_provider_label"],
        "provider_known": route["requested_provider_known"],
        "provider_configured": route["requested_provider_configured"],
        "model": route["requested_model"],
    }


def build_task_route_issue(task_name: str, route: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    if not bool(route.get("requested_provider_known")):
        return {
            "task": str(task_name or "").strip().lower(),
            "provider": str(route.get("requested_provider") or ""),
            "model": str(route.get("requested_model") or ""),
            "code": "unknown_provider",
            "severity": _task_routing_severity(task_name),
            "message": (
                f"Task '{str(task_name or '').strip().lower()}' routes to unknown provider "
                f"'{str(route.get('requested_provider') or '')}'."
            ),
        }
    if not bool(route.get("requested_provider_configured")):
        return {
            "task": str(task_name or "").strip().lower(),
            "provider": str(route.get("requested_provider") or ""),
            "model": str(route.get("requested_model") or ""),
            "code": "provider_not_configured",
            "severity": _task_routing_severity(task_name),
            "message": (
                f"Task '{str(task_name or '').strip().lower()}' routes to provider "
                f"'{str(route.get('requested_provider') or '')}' but it is not configured."
            ),
        }
    return None


def build_routing_runtime_status(
    *,
    default_provider_name: str,
    fallback_provider_name: Optional[str] = None,
    providers: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    provider_list = [dict(provider) for provider in providers or []]
    providers_by_name = build_provider_diagnostics_by_name(provider_list)
    normalized_default_provider = str(default_provider_name or "openai").strip().lower() or "openai"

    task_status = {}
    route_issues = []
    for task_name in TASK_MODEL_FIELDS:
        route = describe_task_route(
            task_name,
            default_provider_name=normalized_default_provider,
            fallback_provider_name=fallback_provider_name,
            providers_by_name=providers_by_name,
        )
        issue = build_task_route_issue(task_name, route)
        if issue is not None:
            route_issues.append(dict(issue))
        task_status[task_name] = {
            **route,
            "issue": dict(issue) if issue is not None else None,
        }

    critical_route_failures = [
        issue
        for issue in route_issues
        if str(issue.get("severity") or "").strip().lower() == "error"
    ]
    return {
        "default_provider": normalized_default_provider,
        "default_provider_label": _provider_label(
            normalized_default_provider,
            providers_by_name.get(normalized_default_provider),
        ),
        "default_provider_configured": bool(
            (providers_by_name.get(normalized_default_provider) or {}).get("configured")
        ),
        "default_model_reference": str(
            _build_model_reference(
                normalized_default_provider,
                _default_model_for_provider(normalized_default_provider),
            )
            or ""
        ),
        "providers": provider_list,
        "task_routing": task_status,
        "route_issues": route_issues,
        "routing_ready": not critical_route_failures,
        "routing_issue_count": len(route_issues),
        "critical_route_failure_count": len(critical_route_failures),
    }
