from __future__ import annotations

from typing import Any, Dict, Optional


ALLOWED_OPERATOR_INTENTS = frozenset(
    {
        "none",
        "workspace_operator",
        "repo_fix",
        "validation_loop",
        "browser_operator",
        "desktop_operator",
        "machine_workflow",
    }
)
ALLOWED_WORKFLOW_DIALOGUE_INTENTS = frozenset(
    {
        "none",
        "continue",
        "status_query",
        "review_open",
        "review_wins",
        "reset",
    }
)
ALLOWED_REPLY_POSTURE_HINTS = frozenset(
    {
        "brief_companion",
        "light_social",
        "practical_operator",
        "calm_support",
        "social_script",
    }
)
ALLOWED_REMINDER_EXPLICITNESS = frozenset({"none", "explicit", "implicit"})
ALLOWED_SEARCH_RECENCY = frozenset({"stable", "time_sensitive"})


def default_semantic_routing() -> Dict[str, Any]:
    return {
        "agent_request": False,
        "operator_request": False,
        "operator_intent": "none",
        "workflow_dialogue_intent": "none",
        "reply_posture_hint": "brief_companion",
        "reminder_explicitness": "none",
        "search_recency": "stable",
        "settings_change_request": False,
    }


def has_semantic_routing(combined_decision: Optional[Dict[str, Any]]) -> bool:
    return isinstance(combined_decision, dict) and isinstance(
        combined_decision.get("semantic_routing"),
        dict,
    )


def semantic_routing_from_combined(
    combined_decision: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(combined_decision, dict):
        return default_semantic_routing()
    return normalize_semantic_routing(
        combined_decision.get("semantic_routing"),
        execution_path=combined_decision.get("execution_path"),
        intent=combined_decision.get("intent"),
        should_search=combined_decision.get("should_search"),
    )


def normalize_semantic_routing(
    value: Any,
    *,
    execution_path: Any = None,
    intent: Any = None,
    should_search: Any = None,
) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    execution_path_text = str(execution_path or "").strip().lower()
    intent_text = str(intent or "").strip().lower()

    agent_request = _coerce_bool(
        raw.get("agent_request"),
        default=execution_path_text == "agent_loop",
    )
    operator_request = _coerce_bool(raw.get("operator_request"), default=False)
    settings_change_request = _coerce_bool(raw.get("settings_change_request"), default=False)

    operator_intent = _normalize_choice(
        raw.get("operator_intent"),
        allowed=ALLOWED_OPERATOR_INTENTS,
        default="none",
    )
    if operator_intent != "none":
        operator_request = True
    elif operator_request:
        operator_intent = "workspace_operator"

    workflow_dialogue_intent = _normalize_choice(
        raw.get("workflow_dialogue_intent"),
        allowed=ALLOWED_WORKFLOW_DIALOGUE_INTENTS,
        default="none",
    )

    reply_posture_hint = _normalize_choice(
        raw.get("reply_posture_hint"),
        allowed=ALLOWED_REPLY_POSTURE_HINTS,
        default=_default_reply_posture_hint(intent_text),
    )

    reminder_explicitness = _normalize_choice(
        raw.get("reminder_explicitness"),
        allowed=ALLOWED_REMINDER_EXPLICITNESS,
        default=("explicit" if intent_text in {"deferred_request", "reminder_request"} else "none"),
    )

    search_recency = _normalize_choice(
        raw.get("search_recency"),
        allowed=ALLOWED_SEARCH_RECENCY,
        default=("time_sensitive" if _coerce_bool(should_search, default=False) and intent_text == "search_web" else "stable"),
    )

    return {
        "agent_request": agent_request,
        "operator_request": operator_request,
        "operator_intent": operator_intent,
        "workflow_dialogue_intent": workflow_dialogue_intent,
        "reply_posture_hint": reply_posture_hint,
        "reminder_explicitness": reminder_explicitness,
        "search_recency": search_recency,
        "settings_change_request": settings_change_request,
    }


def semantic_agent_request(combined_decision: Optional[Dict[str, Any]]) -> bool:
    return bool(semantic_routing_from_combined(combined_decision).get("agent_request"))


def semantic_operator_request(combined_decision: Optional[Dict[str, Any]]) -> bool:
    return bool(
        semantic_routing_from_combined(combined_decision).get("operator_request")
    )


def semantic_operator_intent(combined_decision: Optional[Dict[str, Any]]) -> str:
    return str(
        semantic_routing_from_combined(combined_decision).get("operator_intent")
        or "none"
    )


def semantic_workflow_dialogue_intent(
    combined_decision: Optional[Dict[str, Any]],
) -> str:
    return str(
        semantic_routing_from_combined(combined_decision).get(
            "workflow_dialogue_intent"
        )
        or "none"
    )


def semantic_reply_posture_hint(combined_decision: Optional[Dict[str, Any]]) -> str:
    return str(
        semantic_routing_from_combined(combined_decision).get("reply_posture_hint")
        or "brief_companion"
    )


def semantic_reminder_explicitness(
    semantic_routing: Optional[Dict[str, Any]],
) -> str:
    if isinstance(semantic_routing, dict):
        return str(
            normalize_semantic_routing(semantic_routing).get("reminder_explicitness")
            or "none"
        )
    return "none"


def semantic_search_recency(combined_decision: Optional[Dict[str, Any]]) -> str:
    return str(
        semantic_routing_from_combined(combined_decision).get("search_recency")
        or "stable"
    )


def _normalize_choice(value: Any, *, allowed: set[str] | frozenset[str], default: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in allowed:
        return normalized
    return default


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0", ""}:
            return False
    return default


def _default_reply_posture_hint(intent: str) -> str:
    if intent in {"task_help", "planning"}:
        return "practical_operator"
    if intent == "emotional_support":
        return "calm_support"
    if intent == "smalltalk":
        return "light_social"
    if intent == "creative_request":
        return "brief_companion"
    return "brief_companion"
