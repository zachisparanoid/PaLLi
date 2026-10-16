"""Deterministic reply-signal reading for the voice surface.

voice/presence_policy.py reads a message's cues with classify_reply_signals
and picks a spoken posture with choose_reply_posture. The text-side posture
planner that once lived here (plan_reply_posture, with the tonal register
and relationship calibration it drew on) had no caller left and was deleted
in Batch 8 (2026-10-01).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from core.semantic_routing import normalize_semantic_routing
from voice.types import VoiceReplyPosture

ReplySocialContext = Literal[
    "text",
    "local_private",
    "discord_one_to_one",
    "discord_group",
]

_LOCAL_CONTEXTUAL_PATTERNS = [
    re.compile(r"\b(can you|could you|will you|would you|help me|talk to me|what do you think)\b", re.IGNORECASE),
    re.compile(r"\b(i(?: am|'m) bored|let'?s talk|want to talk|do you want to talk)\b", re.IGNORECASE),
    re.compile(r"\b(what|why|how|when|where|who)\b", re.IGNORECASE),
]
_HELP_CUE_PATTERNS = [
    re.compile(r"\b(can you|could you|would you|will you|help me|what do you think|should i|how do i|do you know)\b", re.IGNORECASE),
    re.compile(r"\b(open|check|find|set|show me|look up|search|explain|remind me|timer|alarm|schedule|fix|debug|install|run)\b", re.IGNORECASE),
]
_CONFUSION_CUE_PATTERNS = [
    re.compile(r"\b(i(?: am|'m)? stuck|i don't get (?:it|this)|not sure why|why is this failing|this isn't working|this is failing)\b", re.IGNORECASE),
    re.compile(r"\b(i can't figure this out|i'm confused|this makes no sense|what am i missing)\b", re.IGNORECASE),
]
_SUPPORT_CUE_PATTERNS = [
    re.compile(r"\b(i(?: am|'m) (stressed|overwhelmed|upset|sad|anxious|scared|frustrated|panicking|not okay))\b", re.IGNORECASE),
    re.compile(r"\b(this is hard|i can'?t do this|i feel awful|i feel bad|i'?m not okay|i don't know what to do)\b", re.IGNORECASE),
]
_SOCIAL_CUE_PATTERNS = [
    re.compile(r"\b(hey|hi|hello|good morning|good evening|what'?s up|how are you)\b", re.IGNORECASE),
]
_SOCIAL_SCRIPT_CUE_PATTERNS = [
    re.compile(
        r"\b("
        r"what should i say|how should i say it|help me (?:write|word|phrase|draft|come up with)|"
        r"write (?:me )?(?:a )?(?:text|message|reply)|draft (?:a )?(?:text|message|reply)|"
        r"come up with (?:an? )?excuse|excuse to tell|what do i tell|what do i say to|"
        r"help me respond|respond to (?:him|her|them|this)|text (?:him|her|them|my .+?) back"
        r")\b",
        re.IGNORECASE,
    ),
]


def _contains_any(patterns: List[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


@dataclass(slots=True)
class ReplySignalClassification:
    is_question: bool = False
    local_contextual: bool = False
    help_cue: bool = False
    confusion_cue: bool = False
    support_cue: bool = False
    social_cue: bool = False
    social_script_cue: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "is_question": self.is_question,
            "local_contextual": self.local_contextual,
            "help_cue": self.help_cue,
            "confusion_cue": self.confusion_cue,
            "support_cue": self.support_cue,
            "social_cue": self.social_cue,
            "social_script_cue": self.social_script_cue,
        }


def classify_reply_signals(
    message: str,
    *,
    semantic_routing: Optional[Dict[str, Any]] = None,
) -> ReplySignalClassification:
    cleaned = str(message or "").strip()
    lower = cleaned.lower()
    if isinstance(semantic_routing, dict):
        normalized_semantics = normalize_semantic_routing(semantic_routing)
        reply_hint = str(
            normalized_semantics.get("reply_posture_hint") or "brief_companion"
        ).strip()
        operator_request = bool(normalized_semantics.get("operator_request"))
        operator_intent = str(
            normalized_semantics.get("operator_intent") or "none"
        ).strip()
        return ReplySignalClassification(
            is_question=lower.endswith("?"),
            local_contextual=bool(cleaned),
            help_cue=reply_hint == "practical_operator" or operator_request,
            confusion_cue=operator_intent in {"repo_fix", "validation_loop"},
            support_cue=reply_hint == "calm_support",
            social_cue=reply_hint == "light_social",
            social_script_cue=reply_hint == "social_script",
        )
    return ReplySignalClassification(
        is_question=lower.endswith("?"),
        local_contextual=bool(cleaned) and (
            lower.endswith("?")
            or " palli" in f" {lower}"
            or " you " in f" {lower} "
            or _contains_any(_LOCAL_CONTEXTUAL_PATTERNS, cleaned)
        ),
        help_cue=_contains_any(_HELP_CUE_PATTERNS, cleaned),
        confusion_cue=_contains_any(_CONFUSION_CUE_PATTERNS, cleaned),
        support_cue=_contains_any(_SUPPORT_CUE_PATTERNS, cleaned),
        social_cue=_contains_any(_SOCIAL_CUE_PATTERNS, cleaned),
        social_script_cue=_contains_any(_SOCIAL_SCRIPT_CUE_PATTERNS, cleaned),
    )


def choose_reply_posture(
    *,
    classification: ReplySignalClassification,
    presence_profile: str = "balanced",
    social_context: ReplySocialContext = "text",
    focus_state: str = "responsive",
    social_context_sensitivity: str = "medium",
    identity_social_permission: str = "guarded",
) -> VoiceReplyPosture:
    if classification.support_cue:
        return "calm_support"
    if classification.social_script_cue:
        return "brief_companion"
    if classification.help_cue or classification.confusion_cue:
        return "practical_operator"
    if (
        classification.social_cue
        and social_context != "discord_group"
        and str(focus_state or "").strip().lower() != "quiet"
        and str(social_context_sensitivity or "").strip().lower() != "high"
        and (
            str(presence_profile or "").strip().lower() == "social"
            or (
                str(identity_social_permission or "").strip().lower() == "warm"
                and social_context in {"text", "local_private"}
            )
        )
    ):
        return "light_social"
    return "brief_companion"
