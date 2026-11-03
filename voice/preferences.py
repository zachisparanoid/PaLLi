"""
Shared voice preference helpers for the local-first product line.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from ai.reply_identity_context import build_reply_identity_snapshot

from core.database import brain
from core.identity import identity_service
from core.local_mode import ensure_local_owner, local_mode_enabled


async def resolve_shared_voice_preferences_owner_id(account_user_id: int | None = None) -> int:
    """
    Resolve the canonical identity user id for shared voice preferences.

    In local mode, voice defaults are shared around the local owner. Outside
    local mode, fall back to the linked account user.
    """
    if local_mode_enabled():
        owner = await ensure_local_owner()
        return int(owner.get("palli_user_id") or owner["id"])
    if account_user_id is None:
        raise RuntimeError("A linked account_user_id is required outside local mode")
    return int(account_user_id)


async def get_shared_voice_preferences(
    *,
    account_user_id: int | None = None,
) -> Tuple[int, Dict[str, Any]]:
    owner_user_id = await resolve_shared_voice_preferences_owner_id(account_user_id=account_user_id)
    identity_platform = "owner" if local_mode_enabled() else "local"
    profile_platform = "discord" if identity_platform == "owner" else "local"
    profile = await brain.get_user_profile(owner_user_id, profile_platform)
    await identity_service.ensure_user_identity_bundle(
        user_id=owner_user_id,
        platform=identity_platform,
        profile=profile,
    )
    compiled_identity = identity_service.compile_identity_sync(
        user_id=owner_user_id,
        platform=identity_platform,
    )
    return owner_user_id, _shared_voice_preferences_from_compiled(compiled_identity)


async def update_shared_voice_preferences(
    *,
    updates: Dict[str, Any],
    actor_type: str,
    actor_id: str,
    reason: str,
    account_user_id: int | None = None,
) -> Dict[str, Any]:
    owner_user_id = await resolve_shared_voice_preferences_owner_id(account_user_id=account_user_id)
    identity_platform = "owner" if local_mode_enabled() else "local"
    await identity_service.update_structured_document(
        user_id=owner_user_id,
        platform=identity_platform,
        doc_key="voice_preferences",
        updates=updates,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
    )
    compiled_identity = identity_service.compile_identity_sync(
        user_id=owner_user_id,
        platform=identity_platform,
    )
    return _shared_voice_preferences_from_compiled(compiled_identity)


def _shared_voice_preferences_from_compiled(compiled_identity: Any) -> Dict[str, Any]:
    voice_snapshot = getattr(compiled_identity, "voice_snapshot", None)
    prefs = dict(getattr(voice_snapshot, "preferences", {}) or {})
    action_coupling = dict(getattr(voice_snapshot, "action_coupling", {}) or {})
    reply_identity_snapshot = build_reply_identity_snapshot(compiled_identity)
    prefs["group_voice_behavior"] = str(
        action_coupling.get("group_voice_behavior") or "socially_adaptive"
    )
    prefs["_materialized"] = bool(getattr(voice_snapshot, "materialized", True))
    prefs["_reply_identity_delivery"] = _reply_identity_delivery_payload(
        reply_identity_snapshot.delivery_plan
    )
    return prefs


def _reply_identity_delivery_payload(plan: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
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
        "routine_summary",
    ):
        value = getattr(plan, key, None)
        if value is None:
            continue
        cleaned = " ".join(str(value).strip().split())
        if cleaned:
            payload[key] = cleaned
    return payload
