from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.semantic_routing import normalize_semantic_routing
from core.llm_json import safe_parse_json_object
from core.prompt_format import format_prompt
from core.time_utils import get_current_time_context

logger = logging.getLogger(__name__)


ALLOWED_LENGTH_BUCKETS = frozenset({"tiny", "short", "medium", "long"})
ALLOWED_EXECUTION_PATHS = frozenset({"pipeline", "agent_loop"})
ALLOWED_INTENTS = frozenset(
    {
        "acknowledgement",
        "smalltalk",
        "question_answering",
        "task_help",
        "planning",
        "creative_request",
        "emotional_support",
        "deferred_request",
        "reminder_request",
        "boundary_request",
        "other",
        "general_conversation",
        "search_web",
        "settings_change",
    }
)
INTENT_ALIASES = {
    "creative": "creative_request",
    "creative_writing": "creative_request",
    "support": "emotional_support",
    "emotional": "emotional_support",
    "deferred": "deferred_request",
    "timer": "deferred_request",
    "alarm": "deferred_request",
    "reminder": "reminder_request",
    "boundary": "boundary_request",
    "web_search": "search_web",
    "settings": "settings_change",
    "config": "settings_change",
    "configure": "settings_change",
    "switch": "settings_change",
    "set": "settings_change",
}

SHOULD_RESPOND_SYSTEM_MESSAGE = (
    "You are analyzing whether to respond to a message and how long the reply should be. "
    "Respond with strict JSON only."
)
COMBINED_DECISION_SYSTEM_MESSAGE = (
    "You are deciding whether to respond and whether to run a web search. "
    "Respond with strict JSON only."
)


@dataclass(frozen=True)
class DecisionPromptContext:
    user_message: str
    history_context: str
    user_profile_context: str
    username: str = "User"
    platform: str = "unknown"
    channel_name: str = "general"
    is_dm: bool = False


def build_should_respond_messages(
    prompt_template: str,
    *,
    context: DecisionPromptContext,
) -> List[Dict[str, str]]:
    full_prompt = format_prompt(
        prompt_template,
        username=context.username,
        platform=context.platform,
        channel_name=context.channel_name,
        is_dm=str(context.is_dm),
        conversation_history=context.history_context,
        current_message=context.user_message,
        decision_criteria=_decision_criteria(is_dm=context.is_dm),
        user_profile=context.user_profile_context,
    )
    return [
        {"role": "system", "content": SHOULD_RESPOND_SYSTEM_MESSAGE},
        {"role": "user", "content": full_prompt},
    ]


def build_combined_decision_messages(
    prompt_template: str,
    *,
    context: DecisionPromptContext,
    current_time_context: Optional[str] = None,
    now: Optional[datetime] = None,
    current_sentiment_tag: str = "neutral",
    current_sentiment_read: str = "(no baseline available)",
    current_sentiment_age: str = "no baseline yet",
) -> List[Dict[str, str]]:
    resolved_now = now or datetime.now()
    full_prompt = format_prompt(
        prompt_template,
        username=context.username,
        platform=context.platform,
        channel_name=context.channel_name,
        is_dm=str(context.is_dm),
        conversation_history=context.history_context,
        current_message=context.user_message,
        decision_criteria=_decision_criteria(is_dm=context.is_dm),
        user_profile=context.user_profile_context,
        current_time_context=current_time_context or get_current_time_context(),
        current_day=resolved_now.strftime("%A"),
        current_date=resolved_now.strftime("%B %d, %Y"),
        current_sentiment_tag=current_sentiment_tag,
        current_sentiment_read=current_sentiment_read,
        current_sentiment_age=current_sentiment_age,
    )
    return [
        {"role": "system", "content": COMBINED_DECISION_SYSTEM_MESSAGE},
        {"role": "user", "content": full_prompt},
    ]


def build_should_respond_fallback(reason: str) -> Dict[str, Any]:
    clean_reason = str(reason or "Decision unavailable").strip() or "Decision unavailable"
    return {
        "should_respond": True,
        "confidence": 0.5,
        "length_bucket": "short",
        "reason": clean_reason,
        "reasoning": clean_reason,
    }


def parse_should_respond_response(response: str) -> Dict[str, Any]:
    result = safe_parse_json_object(response)
    if not isinstance(result, dict):
        raise ValueError(f"Decision response was not a JSON object: {type(result)}")

    normalized = dict(result)
    reasoning = _first_text(normalized.get("reasoning"), normalized.get("reason"))
    if reasoning:
        normalized["reasoning"] = reasoning

    normalized["should_respond"] = _coerce_bool(normalized.get("should_respond"), default=True)
    normalized["confidence"] = _coerce_float(normalized.get("confidence"), default=0.5)
    normalized["length_bucket"] = normalize_length_bucket(normalized.get("length_bucket"))
    return normalized


def parse_combined_decision_response(
    response: str,
    *,
    user_message: str,
) -> Dict[str, Any]:
    result = safe_parse_json_object(response)
    if result is None:
        raise ValueError("Invalid JSON in combined decision response")
    if not isinstance(result, dict):
        raise ValueError(f"Combined decision response was not a JSON object: {type(result)}")

    should_respond = _coerce_bool(result.get("should_respond"), default=True)
    response_confidence = _coerce_float(
        result.get("response_confidence", result.get("confidence")),
        default=0.5,
    )
    response_reasoning = _first_text(
        result.get("response_reasoning"),
        result.get("reasoning"),
        result.get("reason"),
    )
    length_bucket = normalize_length_bucket(result.get("length_bucket"))

    should_search = _coerce_bool(result.get("should_search"), default=False)
    search_confidence = _coerce_float(result.get("search_confidence"), default=0.0)
    search_query = _coerce_text(result.get("search_query"))
    search_reasoning = _first_text(
        result.get("search_reasoning"),
        result.get("search_reason"),
        result.get("reason"),
    )

    if not should_respond:
        should_search = False
        search_query = ""

    intent = normalize_intent(result.get("intent"), should_search=should_search)
    intent_confidence = _coerce_float(
        result.get("intent_confidence"),
        default=(search_confidence if should_search else response_confidence),
    )
    intent_reasoning = _coerce_text(result.get("intent_reasoning")) or (
        search_reasoning if should_search else response_reasoning
    )

    extracted_info = result.get("extracted_info")
    if not isinstance(extracted_info, dict):
        extracted_info = {}

    execution_path = normalize_execution_path(result.get("execution_path"))
    agent_goal = _coerce_text(result.get("agent_goal"))
    if execution_path == "agent_loop" and not agent_goal:
        agent_goal = str(user_message or "").strip()

    agent_confidence = _coerce_float(result.get("agent_confidence"), default=0.0)
    agent_reasoning = _coerce_text(result.get("agent_reasoning")) or "No execution-path reasoning provided."
    semantic_routing = normalize_semantic_routing(
        result.get("semantic_routing"),
        execution_path=execution_path,
        intent=intent,
        should_search=should_search,
    )

    # Stage 1 delegation fields (backward-compatible: default to safe values
    # when the LLM response omits them).
    delegation_tier_raw = result.get("delegation_tier", 0)
    try:
        delegation_tier_int = int(delegation_tier_raw)
    except (TypeError, ValueError):
        delegation_tier_int = 0
    if delegation_tier_int not in (0, 1, 2):
        delegation_tier_int = 0

    delegation_task_hint = _coerce_text(result.get("delegation_task_hint", ""))
    delegation_autonomy_context = _coerce_text(
        result.get("delegation_autonomy_context", "user_request")
    ) or "user_request"

    # delegation_checklist — chain-of-thought scaffolding the LLM fills in
    # BEFORE picking delegation_tier. Exposing it here keeps the reasoning
    # visible in logs/metadata even though it doesn't currently drive any
    # behavior on its own. Backward-compatible: defaults to all-false if
    # the LLM omits the field (e.g. older prompt version).
    checklist_raw = result.get("delegation_checklist")
    if not isinstance(checklist_raw, dict):
        checklist_raw = {}
    delegation_checklist = {
        "requires_multi_step_tools": _coerce_bool(
            checklist_raw.get("requires_multi_step_tools"), default=False
        ),
        "requires_local_access": _coerce_bool(
            checklist_raw.get("requires_local_access"), default=False
        ),
        "benefits_from_ground_truth": _coerce_bool(
            checklist_raw.get("benefits_from_ground_truth"), default=False
        ),
    }

    followup_raw = result.get("delegation_followup_reference", None)
    delegation_followup_reference: Any
    if followup_raw is None:
        delegation_followup_reference = None
    else:
        text = str(followup_raw).strip()
        delegation_followup_reference = text or None

    # ─── Checklist-derived tier enforcement ──────────────────────────
    #
    # The combined_decision prompt defines a four-rule derivation from
    # delegation_checklist to delegation_tier. But LLM compliance with
    # that rule is unreliable in practice — 2026-04-22 multi-step test showed
    # the classifier outputting tier=0 or tier=1 even when a task was
    # clearly multi-step (requires_multi_step_tools would have been
    # true if the LLM had filled the checklist honestly).
    #
    # This parser-side enforcement takes the LLM's raw tier choice as
    # advisory and deterministically re-derives the tier from the
    # checklist fields. If the LLM picked a lower tier than the
    # checklist implies, we override up — the routing ladder then
    # dispatches based on the enforced value. The LLM's original choice
    # is preserved in `delegation_tier_llm_raw` for diagnostics.
    #
    # Direction of enforcement: checklist WINS over LLM tier choice for
    # upgrades (tier up). We never downgrade — if the LLM says tier=2
    # but the checklist doesn't imply it, that's surprising but we
    # trust the LLM (Claude Code being invoked on a simple task is
    # wasteful but not dangerous; the opposite is the failure mode we
    # actually see in production).
    delegation_tier_llm_raw = delegation_tier_int
    derived_tier = _derive_tier_from_checklist(delegation_checklist)
    if derived_tier > delegation_tier_int:
        logger.warning(
            "combined_decision: overriding tier %s → %s based on checklist "
            "(multi_step=%s, local_access=%s, ground_truth=%s); "
            "LLM mis-applied the derivation rule.",
            delegation_tier_int,
            derived_tier,
            delegation_checklist["requires_multi_step_tools"],
            delegation_checklist["requires_local_access"],
            delegation_checklist["benefits_from_ground_truth"],
        )
        delegation_tier_int = derived_tier

    # Tier-2 exclusivity normalization.
    #
    # When the LLM sets delegation_tier=2, it should not simultaneously
    # raise operator/settings/agent flags — those drive the in-house
    # routing rungs, and tier=2 means "hand this off to Claude Code
    # instead." In practice the LLM sometimes sets both, which then
    # causes routing-ladder conflicts (see 2026-04-22 multi-step miss:
    # operator rung intercepted a tier=2 project-analysis request with
    # a canned empty-state response). Normalize here so downstream code
    # doesn't have to arbitrate.
    #
    # The routing ladder has its own Tier-2 priority guard as a belt-
    # and-suspenders second layer; this parser-side normalization is
    # the deterministic fix at the source.
    if delegation_tier_int == 2:
        semantic_routing = {
            **semantic_routing,
            "agent_request": False,
            "operator_request": False,
            "operator_intent": "none",
            "workflow_dialogue_intent": "none",
            "settings_change_request": False,
        }

    # ─── Phase 3: per-turn sentiment refinement ───────────────────
    # The LLM's sentiment payload is routed through ai.sentiment.inference
    # so invalid tags coerce to neutral and confidence clamps to [0, 1].
    # `shift` is parser-local (confirmed|shifted, default confirmed) —
    # it lives in the parser output but isn't part of SentimentReading
    # because the state row doesn't need it; only the tick log does.
    from ai.sentiment.inference import build_reading_from_llm_output
    raw_sentiment = result.get("sentiment")
    reading = build_reading_from_llm_output(
        payload=raw_sentiment,
        source="per_turn_refinement",
        updated_at=0.0,  # caller stamps the real timestamp when writing
    )
    shift_raw = (raw_sentiment or {}).get("shift") if isinstance(raw_sentiment, dict) else None
    shift = "confirmed"
    if isinstance(shift_raw, str) and shift_raw.strip().lower() == "shifted":
        shift = "shifted"
    sentiment_block = {
        "tag": reading.tag,
        "read": reading.read,
        "confidence": reading.confidence,
        "shift": shift,
    }

    parsed = {
        "should_respond": should_respond,
        "response_confidence": response_confidence,
        "length_bucket": length_bucket,
        "response_reasoning": response_reasoning,
        "should_search": should_search,
        "search_confidence": search_confidence,
        "search_query": search_query,
        "search_reasoning": search_reasoning,
        "intent": intent,
        "intent_confidence": intent_confidence,
        "intent_reasoning": intent_reasoning,
        "execution_path": execution_path,
        "agent_goal": agent_goal,
        "agent_confidence": agent_confidence,
        "agent_reasoning": agent_reasoning,
        "extracted_info": extracted_info,
        "semantic_routing": semantic_routing,
        "delegation_checklist": delegation_checklist,
        "delegation_tier": delegation_tier_int,
        "delegation_tier_llm_raw": delegation_tier_llm_raw,
        "delegation_task_hint": delegation_task_hint,
        "delegation_autonomy_context": delegation_autonomy_context,
        "delegation_followup_reference": delegation_followup_reference,
        "sentiment": sentiment_block,
    }

    # Classifier observability — one-line summary of the routing-relevant
    # fields so we can grep the logs to see what the LLM actually decided
    # for any given message. Without this, diagnosing misclassifications
    # required restart-and-retry against inferred behavior.
    logger.info(
        "combined_decision: tier=%s checklist={multi_step=%s, local_access=%s, ground_truth=%s} "
        "intent=%s execution_path=%s agent_goal=%r settings_change=%s operator_request=%s",
        delegation_tier_int,
        delegation_checklist["requires_multi_step_tools"],
        delegation_checklist["requires_local_access"],
        delegation_checklist["benefits_from_ground_truth"],
        intent,
        execution_path,
        (agent_goal or "")[:80],
        semantic_routing.get("settings_change_request"),
        semantic_routing.get("operator_request"),
    )

    return parsed


def _derive_tier_from_checklist(checklist: Dict[str, bool]) -> int:
    """Apply the four-rule derivation from delegation_checklist → tier.

    Mirrors the rule written in prompts/combined_decision.txt. Used by
    the parser to enforce tier consistency deterministically (the LLM
    is advisory, the checklist is binding when it implies a higher
    tier).

    Rules, first match wins:
      1. requires_multi_step_tools=True → tier 2
      2. requires_local_access=True     → tier 2
      3. benefits_from_ground_truth=True (assume one in-house tool
         could ground; parser can't know for sure, so lean toward
         tier 1 when this is the only signal)  → tier 1
      4. otherwise                      → tier 0

    Rule 3 is a lower-bound inference — this helper is only used for
    upgrades (if it returns a tier higher than the LLM chose, we
    override). It's never used to downgrade.
    """
    if checklist.get("requires_multi_step_tools"):
        return 2
    if checklist.get("requires_local_access"):
        return 2
    if checklist.get("benefits_from_ground_truth"):
        return 1
    return 0


def normalize_length_bucket(value: Any, *, default: str = "short") -> str:
    normalized = _coerce_text(value).lower()
    if normalized in ALLOWED_LENGTH_BUCKETS:
        return normalized
    return default


def normalize_execution_path(value: Any, *, default: str = "pipeline") -> str:
    normalized = _coerce_text(value).lower()
    if normalized in ALLOWED_EXECUTION_PATHS:
        return normalized
    return default


def normalize_intent(value: Any, *, should_search: bool) -> str:
    normalized = _coerce_text(value).lower()
    if normalized:
        normalized = INTENT_ALIASES.get(normalized, normalized)
    if normalized in ALLOWED_INTENTS:
        return normalized
    return "search_web" if should_search else "general_conversation"


def _decision_criteria(*, is_dm: bool) -> str:
    if is_dm:
        return "Direct message - typically should respond unless clearly rhetorical"
    return "Standard response criteria"


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


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def _coerce_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _coerce_text(value)
        if text:
            return text
    return ""
