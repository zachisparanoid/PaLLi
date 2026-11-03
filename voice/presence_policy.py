"""
Shared voice presence policy for live spoken behavior.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from ai.reply_posture import classify_reply_signals, choose_reply_posture
from voice.types import VoiceSocialContext, VoiceTurnDecision


def _cooldown_seconds(*, social_context: VoiceSocialContext, initiative_source: str) -> float:
    if initiative_source in {"direct_address", "followup_window", "repair_prompt"}:
        return 0.0
    if social_context == "local_private":
        return 6.0
    if social_context == "discord_one_to_one":
        return 10.0
    return 14.0


def classify_transcript(transcript: str) -> Dict[str, bool]:
    return classify_reply_signals(transcript).to_dict()


def _normalize_identity_signals(
    identity_delivery_plan: Mapping[str, object] | None,
) -> dict[str, str]:
    if not isinstance(identity_delivery_plan, Mapping):
        return {}

    signals: dict[str, str] = {}
    for key in (
        "social_permission",
        "voice_pacing",
        "voice_brevity",
        "question_bias",
        "detail_bias",
        "relationship_posture",
        "communication_rhythm",
        "followthrough_style",
        "proactive_cadence",
    ):
        value = identity_delivery_plan.get(key)
        if value is None:
            continue
        cleaned = " ".join(str(value).strip().split())
        if cleaned:
            signals[key] = cleaned
    return signals


def _identity_allows_social_presence(
    *,
    identity_signals: Mapping[str, str],
    social_context: VoiceSocialContext,
    classification: Any,
) -> bool:
    if not identity_signals or social_context != "local_private" or not classification.social_cue:
        return False
    if str(identity_signals.get("social_permission") or "").strip().lower() != "warm":
        return False
    communication_rhythm = str(identity_signals.get("communication_rhythm") or "").strip().lower()
    relationship_posture = str(identity_signals.get("relationship_posture") or "").strip().lower()
    return communication_rhythm in {"ongoing", "frequent", "daily"} or any(
        marker in relationship_posture
        for marker in ("warm", "supportive", "companion", "present")
    )


def _identity_prefers_quiet_passive_presence(
    identity_signals: Mapping[str, str],
) -> bool:
    if not identity_signals:
        return False
    social_permission = str(identity_signals.get("social_permission") or "").strip().lower()
    followthrough_style = str(identity_signals.get("followthrough_style") or "").strip().lower()
    question_bias = str(identity_signals.get("question_bias") or "").strip().lower()
    voice_brevity = str(identity_signals.get("voice_brevity") or "").strip().lower()
    return (
        social_permission == "guarded"
        or followthrough_style in {"quiet", "quiet_reliable"}
        or question_bias == "avoid"
        or voice_brevity == "tight"
    )


def _make_turn_decision(
    should_reply: bool,
    decision_reason: str,
    *,
    initiative_source: str = "none",
    reply_posture: str = "brief_companion",
    confidence: float = 0.0,
    should_mirror_text: bool = False,
    identity_signals: Mapping[str, Any] | None = None,
) -> VoiceTurnDecision:
    return VoiceTurnDecision(
        should_reply=should_reply,
        decision_reason=decision_reason,
        initiative_source=initiative_source,  # type: ignore[arg-type]
        reply_posture=reply_posture,  # type: ignore[arg-type]
        confidence=confidence,
        should_mirror_text=should_mirror_text,
        identity_signals=dict(identity_signals or {}),
    )


def _has_strong_help_shape(transcript: str, *, classification) -> bool:
    cleaned = str(transcript or "").strip().lower()
    if classification.confusion_cue:
        return True
    if classification.help_cue and (classification.is_question or classification.local_contextual):
        return True
    return any(
        phrase in cleaned
        for phrase in (
            "can you help",
            "could you help",
            "help me",
            "can you fix",
            "why is this failing",
            "what am i missing",
        )
    )


class VoicePresencePolicy:
    @staticmethod
    def decide_turn(
        *,
        transcript: str,
        social_context: VoiceSocialContext,
        interaction_mode: str,
        presence_profile: str,
        focus_state: str,
        group_voice_behavior: str,
        identity_delivery_plan: Mapping[str, object] | None,
        explicit_addressed: bool,
        within_followup_window: bool,
        recent_unsolicited_seconds: float | None,
        recent_assistant_seconds: float | None,
    ) -> VoiceTurnDecision:
        classification = classify_reply_signals(transcript)
        identity_signals = _normalize_identity_signals(identity_delivery_plan)
        posture = choose_reply_posture(
            classification=classification,
            presence_profile=presence_profile,
            social_context=social_context,
            focus_state=focus_state,
            social_context_sensitivity="high" if social_context == "discord_group" else "medium",
            identity_social_permission=identity_signals.get("social_permission", "guarded"),
        )
        mirror = posture in {"calm_support", "practical_operator"}
        allow_identity_social_presence = (
            str(interaction_mode or "").strip().lower() == "passive"
            and _identity_allows_social_presence(
                identity_signals=identity_signals,
                social_context=social_context,
                classification=classification,
            )
        )

        if explicit_addressed:
            return _make_turn_decision(
                True,
                "direct_address",
                initiative_source="direct_address",
                reply_posture=posture,
                confidence=1.0,
                should_mirror_text=mirror,
                identity_signals=identity_signals,
            )

        if within_followup_window:
            return _make_turn_decision(
                True,
                "followup_window",
                initiative_source="followup_window",
                reply_posture=posture,
                confidence=0.95,
                should_mirror_text=mirror,
                identity_signals=identity_signals,
            )

        if str(focus_state or "").strip().lower() == "quiet":
            return _make_turn_decision(
                False,
                "focus_quiet",
                confidence=0.0,
                identity_signals=identity_signals,
            )

        if recent_assistant_seconds is not None:
            source = (
                "support_cue"
                if classification.support_cue
                else "confusion_cue"
                if classification.confusion_cue
                else "passive_context"
            )
            if recent_assistant_seconds < _cooldown_seconds(social_context=social_context, initiative_source=source):
                return _make_turn_decision(
                    False,
                    "recent_assistant_turn",
                    confidence=0.0,
                    identity_signals=identity_signals,
                )

        if recent_unsolicited_seconds is not None and recent_unsolicited_seconds < _cooldown_seconds(
            social_context=social_context,
            initiative_source="passive_context",
        ):
            return _make_turn_decision(
                False,
                "cooldown_active",
                confidence=0.0,
                identity_signals=identity_signals,
            )

        interaction = str(interaction_mode or "").strip().lower()
        if social_context == "local_private":
            if interaction == "direct_address":
                return _make_turn_decision(
                    True,
                    "direct_address",
                    initiative_source="direct_address",
                    reply_posture=posture,
                    confidence=1.0,
                    should_mirror_text=mirror,
                    identity_signals=identity_signals,
                )
            if interaction == "passive" and classification.local_contextual:
                initiative_source = (
                    "support_cue"
                    if classification.support_cue
                    else "confusion_cue"
                    if classification.confusion_cue
                    else "passive_context"
                )
                return _make_turn_decision(
                    True,
                    "local_contextual_turn",
                    initiative_source=initiative_source,
                    reply_posture=posture,
                    confidence=0.85,
                    should_mirror_text=mirror,
                    identity_signals=identity_signals,
                )
            if allow_identity_social_presence:
                return _make_turn_decision(
                    True,
                    "identity_social_presence",
                    initiative_source="passive_context",
                    reply_posture=posture,
                    confidence=0.78,
                    should_mirror_text=mirror,
                    identity_signals=identity_signals,
                )
            return _make_turn_decision(
                False,
                "insufficient_confidence",
                confidence=0.0,
                identity_signals=identity_signals,
            )

        if social_context == "discord_one_to_one":
            if classification.support_cue:
                return _make_turn_decision(
                    True,
                    "serious_moment_support",
                    initiative_source="support_cue",
                    reply_posture="calm_support",
                    confidence=0.92,
                    should_mirror_text=True,
                    identity_signals=identity_signals,
                )
            if classification.confusion_cue:
                return _make_turn_decision(
                    True,
                    "one_to_one_confusion_cue",
                    initiative_source="confusion_cue",
                    reply_posture="practical_operator",
                    confidence=0.9,
                    should_mirror_text=True,
                    identity_signals=identity_signals,
                )
            if classification.help_cue:
                return _make_turn_decision(
                    True,
                    "one_to_one_help_cue",
                    initiative_source="confusion_cue" if classification.confusion_cue else "passive_context",
                    reply_posture="practical_operator",
                    confidence=0.88,
                    should_mirror_text=True,
                    identity_signals=identity_signals,
                )
            return _make_turn_decision(
                False,
                "insufficient_confidence",
                confidence=0.0,
                identity_signals=identity_signals,
            )

        normalized_group_behavior = str(group_voice_behavior or "").strip().lower()
        if normalized_group_behavior == "reserved":
            return _make_turn_decision(
                False,
                "group_not_addressed",
                confidence=0.0,
                identity_signals=identity_signals,
            )
        if classification.support_cue:
            return _make_turn_decision(
                True,
                "serious_moment_support",
                initiative_source="support_cue",
                reply_posture="calm_support",
                confidence=0.86 if normalized_group_behavior == "socially_adaptive" else 0.84,
                should_mirror_text=True,
                identity_signals=identity_signals,
            )
        if classification.confusion_cue:
            return _make_turn_decision(
                True,
                "group_confusion_cue",
                initiative_source="confusion_cue",
                reply_posture="practical_operator",
                confidence=0.84 if normalized_group_behavior == "socially_adaptive" else 0.82,
                should_mirror_text=True,
                identity_signals=identity_signals,
            )
        if classification.help_cue and _has_strong_help_shape(transcript, classification=classification):
            return _make_turn_decision(
                True,
                "group_help_cue",
                initiative_source="passive_context",
                reply_posture="practical_operator",
                confidence=0.83 if normalized_group_behavior == "socially_adaptive" else 0.8,
                should_mirror_text=True,
                identity_signals=identity_signals,
            )
        return _make_turn_decision(
            False,
            "group_not_addressed",
            confidence=0.0,
            identity_signals=identity_signals,
        )

    @staticmethod
    def decide_passive_interjection(
        *,
        transcript: str,
        social_context: VoiceSocialContext,
        presence_profile: str,
        focus_state: str,
        group_voice_behavior: str,
        identity_delivery_plan: Mapping[str, object] | None,
        analyzer_decision: Dict[str, object],
        recent_unsolicited_seconds: float | None,
        recent_assistant_seconds: float | None,
    ) -> VoiceTurnDecision:
        classification = classify_reply_signals(transcript)
        identity_signals = _normalize_identity_signals(identity_delivery_plan)
        posture = choose_reply_posture(
            classification=classification,
            presence_profile=presence_profile,
            social_context=social_context,
            focus_state=focus_state,
            social_context_sensitivity="high" if social_context == "discord_group" else "medium",
            identity_social_permission=identity_signals.get("social_permission", "guarded"),
        )
        confidence = float(analyzer_decision.get("confidence") or 0.0)
        allow_identity_social_presence = _identity_allows_social_presence(
            identity_signals=identity_signals,
            social_context=social_context,
            classification=classification,
        )
        generic_passive_context = not (
            classification.support_cue
            or classification.confusion_cue
            or _has_strong_help_shape(transcript, classification=classification)
            or allow_identity_social_presence
        )

        if str(focus_state or "").strip().lower() == "quiet":
            return _make_turn_decision(
                False,
                "focus_quiet",
                confidence=confidence,
                identity_signals=identity_signals,
            )

        if recent_assistant_seconds is not None and recent_assistant_seconds < _cooldown_seconds(
            social_context=social_context,
            initiative_source="passive_context",
        ):
            return _make_turn_decision(
                False,
                "recent_assistant_turn",
                confidence=confidence,
                identity_signals=identity_signals,
            )

        if recent_unsolicited_seconds is not None and recent_unsolicited_seconds < _cooldown_seconds(
            social_context=social_context,
            initiative_source="passive_context",
        ):
            return _make_turn_decision(
                False,
                "cooldown_active",
                confidence=confidence,
                identity_signals=identity_signals,
            )

        min_confidence = 0.75 if social_context == "local_private" else 0.82
        if confidence < min_confidence:
            return _make_turn_decision(
                False,
                "insufficient_confidence",
                confidence=confidence,
                identity_signals=identity_signals,
            )

        normalized_group_behavior = str(group_voice_behavior or "").strip().lower()
        if social_context == "discord_group" and normalized_group_behavior == "reserved":
            return _make_turn_decision(
                False,
                "group_not_addressed",
                confidence=confidence,
                identity_signals=identity_signals,
            )
        if social_context == "discord_group" and not (
            classification.support_cue
            or classification.confusion_cue
            or _has_strong_help_shape(transcript, classification=classification)
        ):
            return _make_turn_decision(
                False,
                "group_not_addressed",
                confidence=confidence,
                identity_signals=identity_signals,
            )
        if generic_passive_context and _identity_prefers_quiet_passive_presence(identity_signals):
            return _make_turn_decision(
                False,
                "identity_prefers_quiet",
                initiative_source="passive_context",
                reply_posture=posture,
                confidence=confidence,
                identity_signals=identity_signals,
            )

        return _make_turn_decision(
            True,
            (
                "serious_moment_support"
                if classification.support_cue
                else "group_confusion_cue"
                if social_context == "discord_group" and classification.confusion_cue
                else "identity_social_presence"
                if allow_identity_social_presence
                else "local_contextual_turn"
            ),
            initiative_source=(
                "support_cue"
                if classification.support_cue
                else "confusion_cue"
                if classification.confusion_cue
                else "passive_context"
            ),
            reply_posture=posture,
            confidence=confidence,
            should_mirror_text=posture in {"calm_support", "practical_operator"},
            identity_signals=identity_signals,
        )
