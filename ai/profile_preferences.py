from __future__ import annotations

import re
from typing import Any, Mapping, Optional

PALLI_ALIAS_TOKENS = {
    "palli",
    "pali",
    "holly",
    "poly",
    "polly",
    "paulie",
}

_ADDRESS_PREFIXES = (
    "hey",
    "hi",
    "hello",
    "yo",
    "ok",
    "okay",
    "thanks",
    "thank you",
    "good morning",
    "morning",
    "good afternoon",
    "afternoon",
    "good evening",
    "evening",
)

_ADDRESS_SUFFIXES = (
    "are you there",
    "you there",
    "can you",
    "will you",
    "what's up",
    "what is up",
)


def _normalize_name_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def is_palli_alias(value: Any) -> bool:
    return _normalize_name_token(value) in PALLI_ALIAS_TOKENS


def _profile_name_matches_candidate(
    candidate: str,
    *,
    profile: Optional[Mapping[str, Any]] = None,
) -> bool:
    candidate_norm = _normalize_name_token(candidate)
    if not candidate_norm or not isinstance(profile, Mapping):
        return False
    for key in ("display_name", "username"):
        raw = profile.get(key)
        if raw and _normalize_name_token(raw) == candidate_norm:
            return True
    return False


def looks_like_palli_addressing(
    candidate: str,
    *,
    recent_user_messages: Optional[str] = None,
) -> bool:
    text = " ".join(str(recent_user_messages or "").split())
    if not text or not str(candidate or "").strip():
        return False

    escaped = re.escape(str(candidate).strip())
    prefix_group = "|".join(re.escape(item) for item in _ADDRESS_PREFIXES)
    suffix_group = "|".join(re.escape(item) for item in _ADDRESS_SUFFIXES)
    patterns = (
        re.compile(rf"\b(?:{prefix_group})\b[\s,!.?\"']*{escaped}\b", re.IGNORECASE),
        re.compile(rf"\b{escaped}\b[\s,!.?\"']*(?:{suffix_group})\b", re.IGNORECASE),
    )
    return any(pattern.search(text) for pattern in patterns)


def sanitize_preferred_name(
    value: Any,
    *,
    profile: Optional[Mapping[str, Any]] = None,
    recent_user_messages: Optional[str] = None,
) -> Optional[str]:
    cleaned = " ".join(str(value or "").strip().split())
    if not cleaned:
        return None

    if not is_palli_alias(cleaned):
        return cleaned

    if _profile_name_matches_candidate(cleaned, profile=profile):
        return cleaned

    if looks_like_palli_addressing(cleaned, recent_user_messages=recent_user_messages):
        return None

    return None


def get_safe_preferred_name(profile: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(profile, Mapping):
        return None
    preferences = profile.get("preferences") or {}
    if not isinstance(preferences, Mapping):
        return None
    return sanitize_preferred_name(
        preferences.get("preferred_name"),
        profile=profile,
    )
