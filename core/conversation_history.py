from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.voice_turn_decision_trace import build_voice_turn_decision_trace


def get_history_metadata(message: Dict[str, Any]) -> Dict[str, Any]:
    raw_metadata = message.get("metadata")
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if isinstance(raw_metadata, str) and raw_metadata.strip():
        try:
            parsed = json.loads(raw_metadata)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def is_error_fallback_turn(message: Dict[str, Any]) -> bool:
    metadata = get_history_metadata(message)
    message_type = str(metadata.get("type") or "").strip().lower()
    error = str(metadata.get("error") or "").strip().lower()
    if message_type == "error_fallback" or error == "empty_llm_response":
        return True

    role = str(message.get("role") or "").strip().lower()
    content = str(message.get("content") or "").strip().lower()
    return role == "assistant" and "glitched on my end" in content


def sanitize_conversation_history(conversation_history: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    if not conversation_history:
        return []
    return [message for message in conversation_history if not is_error_fallback_turn(message)]


def summarize_history_content(content: Any, *, limit: int = 160) -> str:
    clean = " ".join(str(content or "").split())
    if len(clean) <= max(int(limit or 0), 1):
        return clean
    clipped_limit = max(int(limit or 160), 8)
    return clean[: clipped_limit - 3].rstrip() + "..."


def get_history_voice_turn_decision_trace(message: Dict[str, Any]) -> Dict[str, Any] | None:
    metadata = get_history_metadata(message)
    existing = metadata.get("voice_turn_decision_trace")
    if isinstance(existing, dict) and existing:
        return dict(existing)

    raw_turn_decision = metadata.get("voice_turn_decision")
    return build_voice_turn_decision_trace(
        turn_decision=raw_turn_decision if isinstance(raw_turn_decision, dict) else None,
        fallback_decision_reason=metadata.get("voice_decision_reason"),
        fallback_reply_posture=metadata.get("voice_reply_posture"),
    )


def build_conversation_history_preview_entry(
    message: Dict[str, Any],
    *,
    excerpt_limit: int = 160,
) -> Optional[Dict[str, Any]]:
    role = str(message.get("role") or "").strip().lower() or "unknown"
    metadata = get_history_metadata(message)
    content = str(message.get("content") or "").strip()
    voice_turn_decision_trace = get_history_voice_turn_decision_trace(message)

    if not content and voice_turn_decision_trace is None:
        return None

    role_label = "PaLLi" if role == "assistant" else str(
        message.get("username")
        or metadata.get("username")
        or ("You" if role == "user" else role.title() or "Message")
    ).strip() or "Message"

    preview: Dict[str, Any] = {
        "id": message.get("id"),
        "role": role,
        "role_label": role_label,
        "content": content,
        "excerpt": summarize_history_content(content, limit=excerpt_limit),
        "timestamp": message.get("timestamp"),
        "type": str(metadata.get("type") or "").strip(),
        "response_to": str(metadata.get("response_to") or "").strip(),
    }
    if voice_turn_decision_trace is not None:
        preview["voice_turn_decision_trace"] = voice_turn_decision_trace
    return preview


def build_conversation_history_preview(
    conversation_history: List[Dict[str, Any]] | None,
    *,
    limit: int = 3,
    excerpt_limit: int = 160,
) -> List[Dict[str, Any]]:
    if not conversation_history:
        return []

    preview: List[Dict[str, Any]] = []
    for message in sanitize_conversation_history(conversation_history):
        entry = build_conversation_history_preview_entry(
            message,
            excerpt_limit=excerpt_limit,
        )
        if entry is not None:
            preview.append(entry)

    if limit <= 0:
        return preview
    return preview[-int(limit):]
