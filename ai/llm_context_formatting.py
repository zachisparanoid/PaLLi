from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from ai.profile_preferences import get_safe_preferred_name
from core.config import config
from core.conversation_history import get_history_metadata, is_error_fallback_turn
from core.time_utils import format_timestamp_for_llm, get_current_time


def format_user_profile_for_decision(profile: Optional[Mapping[str, Any]]) -> str:
    """Format compact user profile context for response decision prompts."""
    if not profile or not isinstance(profile, dict):
        return "No profile information available."

    parts = []

    learning_progress = profile.get("learning_progress", 0.0)
    total_interactions = profile.get("total_interactions", 0)
    parts.append(
        f"Learning progress: {learning_progress:.1%}, Total interactions: {total_interactions}"
    )

    preferences = profile.get("preferences") or {}
    if isinstance(preferences, str):
        try:
            preferences = json.loads(preferences)
        except Exception:
            preferences = {}

    if isinstance(preferences, dict):
        tz_info = preferences.get("time_zone") or {}
        if isinstance(tz_info, dict) and tz_info.get("value"):
            parts.append(f"Time zone: {tz_info.get('value')}")

        preferred_name = get_safe_preferred_name(profile)
        if preferred_name:
            parts.append(f"Preferred name: {preferred_name}")

        pronouns = preferences.get("pronouns")
        if isinstance(pronouns, str) and pronouns.strip():
            parts.append(f"Pronouns: {pronouns.strip()}")

        response_length = preferences.get("response_length")
        if isinstance(response_length, str) and response_length.strip():
            normalized_response_length = response_length.strip().lower()
            if normalized_response_length == "short":
                parts.append("Response length preference: short (1 sentence by default)")
            elif normalized_response_length == "medium":
                parts.append("Response length preference: medium (2-4 sentences when helpful)")
            elif normalized_response_length == "long":
                parts.append("Response length preference: long (multi-sentence explanations when asked)")
            else:
                parts.append(f"Response length preference: {response_length.strip()}")

        humor_style = preferences.get("humor_style")
        if isinstance(humor_style, str) and humor_style.strip():
            parts.append(f"Humor style: {humor_style.strip()}")

        interests = preferences.get("interests") or []
        if isinstance(interests, list) and interests:
            parts.append(f"Top interests: {', '.join(str(item) for item in interests[:5])}")

        topics_to_avoid = preferences.get("topics_to_avoid") or []
        if isinstance(topics_to_avoid, list) and topics_to_avoid:
            parts.append(f"Topics to avoid: {', '.join(str(item) for item in topics_to_avoid[:5])}")

    return "; ".join(parts) if parts else "No significant long-term preferences on file."


def format_conversation_history(conversation_history: Optional[List[Dict[str, Any]]]) -> str:
    """Format recent conversation history for LLM context."""
    if not conversation_history:
        return "No recent conversation history."

    formatted_history = []
    current_time = get_current_time()
    window_size = getattr(config, "LLM_CONTEXT_WINDOW_SIZE", config.CONTEXT_WINDOW_SIZE)

    for message in conversation_history[-window_size:]:
        if is_error_fallback_turn(message):
            continue

        metadata = get_history_metadata(message)
        speaker = message.get("username")
        if not speaker:
            if message.get("role") == "user":
                speaker = metadata.get("username", "User")
            else:
                speaker = "Me"

        content = message.get("content", "")
        if getattr(config, "TRUNCATE_LLM_HISTORY_MESSAGES", True):
            content = content[:200]

        timestamp = message.get("timestamp")
        if timestamp:
            time_str = format_timestamp_for_llm(timestamp, current_time)
            formatted_history.append(f"{speaker} ({time_str}): {content}")
        else:
            formatted_history.append(f"{speaker}: {content}")

    return "\n".join(formatted_history)
