from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def humanize_voice_decision_token(value: Any) -> str:
    clean = str(value or "").strip().replace("_", " ")
    return clean or "unknown"


def safe_voice_decision_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    clean = str(value).strip().lower()
    if clean in {"true", "1", "yes", "on"}:
        return True
    if clean in {"false", "0", "no", "off"}:
        return False
    return None


def safe_voice_decision_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_voice_turn_behavior(
    *,
    should_reply: bool | None,
    initiative_source: str,
    reply_posture: str,
    decision_reason: str,
) -> str:
    if should_reply is False:
        return "stayed_quiet"
    if initiative_source == "repair_prompt" or decision_reason == "repair_needed":
        return "repair_prompt"
    if should_reply is True and reply_posture == "light_social":
        return "light_social_presence"
    if should_reply is True and initiative_source in {"passive_context", "support_cue", "confusion_cue"}:
        return "interjected"
    if should_reply is True and initiative_source == "followup_window":
        return "followup_response"
    if should_reply is True and initiative_source == "direct_address":
        return "direct_response"
    if should_reply is True:
        return "responded"
    return "decision_recorded"


def voice_turn_behavior_label(behavior: str) -> str:
    return {
        "stayed_quiet": "Stayed quiet",
        "repair_prompt": "Repair prompt",
        "light_social_presence": "Light social presence",
        "interjected": "Interjected",
        "followup_response": "Follow-up response",
        "direct_response": "Direct response",
        "responded": "Responded",
        "decision_recorded": "Decision recorded",
    }.get(str(behavior or "").strip(), "Decision recorded")


def build_voice_identity_cues(identity_signals: Mapping[str, Any]) -> list[dict[str, str]]:
    cues: list[dict[str, str]] = []
    for key, value in identity_signals.items():
        clean_key = str(key or "").strip()
        clean_value = str(value or "").strip()
        if not clean_key or not clean_value:
            continue
        cues.append(
            {
                "key": clean_key,
                "label": humanize_voice_decision_token(clean_key),
                "value": clean_value,
                "value_label": humanize_voice_decision_token(clean_value),
            }
        )
    return cues


def build_voice_turn_decision_trace(
    *,
    turn_decision: Mapping[str, Any] | None = None,
    fallback_decision_reason: Any = None,
    fallback_initiative_source: Any = None,
    fallback_reply_posture: Any = None,
    fallback_confidence: Any = None,
    fallback_should_mirror_text: Any = None,
    fallback_identity_signals: Any = None,
) -> dict[str, Any] | None:
    raw_turn_decision = dict(turn_decision) if isinstance(turn_decision, Mapping) else {}

    should_reply = safe_voice_decision_bool(raw_turn_decision.get("should_reply"))
    decision_reason = str(
        raw_turn_decision.get("decision_reason")
        or fallback_decision_reason
        or ""
    ).strip()
    initiative_source = str(
        raw_turn_decision.get("initiative_source")
        or fallback_initiative_source
        or "none"
    ).strip() or "none"
    reply_posture = str(
        raw_turn_decision.get("reply_posture")
        or fallback_reply_posture
        or "brief_companion"
    ).strip() or "brief_companion"
    confidence = safe_voice_decision_float(
        raw_turn_decision.get("confidence")
        if raw_turn_decision.get("confidence") is not None
        else fallback_confidence
    )
    should_mirror_text = safe_voice_decision_bool(
        raw_turn_decision.get("should_mirror_text")
        if raw_turn_decision.get("should_mirror_text") is not None
        else fallback_should_mirror_text
    )
    raw_identity_signals = raw_turn_decision.get("identity_signals")
    if isinstance(raw_identity_signals, Mapping):
        identity_signals = dict(raw_identity_signals)
    elif isinstance(fallback_identity_signals, Mapping):
        identity_signals = dict(fallback_identity_signals)
    else:
        identity_signals = {}

    if should_reply is None and not decision_reason and not identity_signals and not raw_turn_decision:
        return None

    behavior = classify_voice_turn_behavior(
        should_reply=should_reply,
        initiative_source=initiative_source,
        reply_posture=reply_posture,
        decision_reason=decision_reason,
    )
    behavior_label = voice_turn_behavior_label(behavior)
    decision_reason_label = humanize_voice_decision_token(decision_reason) if decision_reason else ""
    initiative_source_label = humanize_voice_decision_token(initiative_source)
    reply_posture_label = humanize_voice_decision_token(reply_posture)
    summary = behavior_label
    if decision_reason_label:
        summary += f" because {decision_reason_label}"
    if initiative_source not in {"", "none"} and initiative_source_label != decision_reason_label:
        summary += f" via {initiative_source_label}"

    trace: dict[str, Any] = {
        "behavior": behavior,
        "behavior_label": behavior_label,
        "summary": summary,
        "decision_reason": decision_reason,
        "decision_reason_label": decision_reason_label,
        "initiative_source": initiative_source,
        "initiative_source_label": initiative_source_label,
        "reply_posture": reply_posture,
        "reply_posture_label": reply_posture_label,
        "identity_signals": dict(identity_signals),
        "identity_cues": build_voice_identity_cues(identity_signals),
    }
    if should_reply is not None:
        trace["should_reply"] = should_reply
    if confidence is not None:
        trace["confidence"] = confidence
        trace["confidence_label"] = f"{max(min(confidence, 1.0), 0.0):.0%}"
    if should_mirror_text is not None:
        trace["should_mirror_text"] = should_mirror_text
        trace["mirror_policy_label"] = "Mirror to text" if should_mirror_text else "No text mirror"
    return trace
