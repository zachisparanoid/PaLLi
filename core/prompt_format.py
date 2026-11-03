"""
Prompt formatting helpers.

PaLLi has prompts that evolved over time with slightly different placeholder names
for the same concept (e.g., `user_message` vs `current_message`). These helpers
add a small compatibility layer so prompt edits are less brittle.
"""

from __future__ import annotations

from typing import Any, Dict


def with_prompt_aliases(values: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a copy of `values` with common legacy aliases populated.

    This does not attempt to "guess" missing data; it only mirrors provided values
    into alternate key names that are known to exist across prompt templates.
    """
    merged: Dict[str, Any] = dict(values or {})

    # Message aliases
    if "user_message" in merged and "current_message" not in merged:
        merged["current_message"] = merged["user_message"]
    if "current_message" in merged and "user_message" not in merged:
        merged["user_message"] = merged["current_message"]
    if "user_message" in merged and "message" not in merged:
        merged["message"] = merged["user_message"]
    if "message" in merged and "user_message" not in merged:
        merged["user_message"] = merged["message"]
    if "message" in merged and "current_message" not in merged:
        merged["current_message"] = merged["message"]
    if "current_message" in merged and "message" not in merged:
        merged["message"] = merged["current_message"]

    # History aliases
    if "conversation_history" in merged and "history_context" not in merged:
        merged["history_context"] = merged["conversation_history"]
    if "history_context" in merged and "conversation_history" not in merged:
        merged["conversation_history"] = merged["history_context"]
    if "recent_history" in merged and "history_context" not in merged:
        merged["history_context"] = merged["recent_history"]
    if "history_context" in merged and "recent_history" not in merged:
        merged["recent_history"] = merged["history_context"]
    if "recent_history_snippet" in merged and "recent_history" not in merged:
        merged["recent_history"] = merged["recent_history_snippet"]
    if "recent_history" in merged and "recent_history_snippet" not in merged:
        merged["recent_history_snippet"] = merged["recent_history"]

    # Time aliases
    if "current_time_context" in merged and "current_time" not in merged:
        merged["current_time"] = merged["current_time_context"]
    if "current_time" in merged and "current_time_context" not in merged:
        merged["current_time_context"] = merged["current_time"]

    # Identity/context aliases
    if "username" in merged and "user" not in merged:
        merged["user"] = merged["username"]
    if "channel_name" in merged and "channel" not in merged:
        merged["channel"] = merged["channel_name"]
    if "channel" in merged and "channel_name" not in merged:
        merged["channel_name"] = merged["channel"]
    if "user_profile" in merged and "profile" not in merged:
        merged["profile"] = merged["user_profile"]
    if "profile" in merged and "user_profile" not in merged:
        merged["user_profile"] = merged["profile"]

    return merged


def format_prompt(template: str, **values: Any) -> str:
    """Format a prompt template with alias compatibility."""
    return (template or "").format(**with_prompt_aliases(values))
