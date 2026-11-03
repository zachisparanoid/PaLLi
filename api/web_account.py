"""
Local dashboard account and profile endpoints.
"""

from __future__ import annotations

import json
import re
import time
import logging
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from core.config import config
from core.database import brain
from api import guided_setup_dashboard
from core.identity.service import identity_service
from core.local_mode import local_auth_bypass_enabled
from core.memory_service import (
    ForgetRowDeleteError,
    ForgetSourceWriteError,
    forget_user_memory,
    memory_workspace_service,
)
from core.setup_presets import (
    GUIDED_SETUP_AUTONOMY_PRESETS,
    GUIDED_SETUP_CHECKIN_OPTIONS,
    GUIDED_SETUP_CHECKIN_POSTURES,
    GUIDED_SETUP_RELATIONSHIP_STYLES,
    GUIDED_SETUP_STYLE_PRESETS,
    GUIDED_SETUP_VOICE_POSTURES,
)
from core.web_security import require_csrf
from core.rate_limit import limiter
from api.web_auth import _require_user, _public_user

router = APIRouter()

logger = logging.getLogger(__name__)


DISCORD_ID_RE = re.compile(r"^\d{6,20}$")


class LinkAccountsRequest(BaseModel):
    discord_user_id: Optional[str] = None


async def _require_runtime_access_user(request: Request):
    user = await _require_user(request)
    if not user.get("has_access"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PaLLi runtime access required",
        )
    return user


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    interests: Optional[str] = None
    check_in_freq: Optional[str] = None
    voice_enabled: Optional[bool] = None
    proactive_enabled: Optional[bool] = None
    personality_preset: Optional[str] = None


class GuidedSetupRequest(BaseModel):
    name: str
    companion_style: str
    autonomy_preset: str
    checkin_posture: Optional[str] = None
    voice_posture: Optional[str] = None
    relationship_style: Optional[str] = None
    proactive_enabled: Optional[bool] = True
    check_in_freq: Optional[str] = "normal"
    voice_enabled: Optional[bool] = True


from core.web_security import require_csrf

class PurgeDataRequest(BaseModel):
    confirm_text: str
    targets: List[str]


async def _resolve_setup_identity_target(user: Dict[str, Any]) -> tuple[int, str]:
    owner_user_id = await identity_service.resolve_local_owner_user_id()
    if owner_user_id is not None:
        return int(owner_user_id), "owner"
    return int(_get_effective_user_id(user)), "discord"


async def _resolve_memory_owner_user_id(user: Dict[str, Any]) -> int:
    owner_user_id = await identity_service.resolve_local_owner_user_id(account_user_id=int(user["id"]))
    if owner_user_id is not None:
        return int(owner_user_id)
    return int(_get_effective_user_id(user))


def _profile_storage_platform(identity_platform: str) -> str:
    return "discord" if identity_platform == "owner" else identity_platform


@router.get("/account/audit-logs")
async def get_audit_logs(request: Request):
    user = await _require_user(request)
    logs = await brain.get_audit_logs(user["id"])
    return {"logs": logs}

def _get_effective_user_id(user: Dict) -> int:
    """
    Resolve the effective PaLLi user ID.
    If the user has linked a Discord account, we use that ID (converted to int)
    because that is where the bot stores memories.
    Otherwise, we fall back to the palli_user_id (app user ID).
    """
    if user.get("discord_user_id"):
        try:
            return int(user["discord_user_id"])
        except ValueError:
            pass
    return user.get("palli_user_id") or user["id"]


def _get_memory_owner_id(user: Dict) -> int:
    """Resolve the user id that owns this owner's stored memories.

    The agent loop writes memories under the app owner id
    (core/agent/dispatch.py::_resolve_local_owner_id -> app_user_id). In local
    single-owner mode the owner's linked Discord id (which _get_effective_user_id
    prefers) is NOT where memories live, so the Memory Galaxy list/delete must use
    the app id to match writes. Cloud/multi-user keeps the effective-id behavior.
    """
    if local_auth_bypass_enabled():
        return int(user.get("palli_user_id") or user["id"])
    return int(_get_effective_user_id(user))


@router.get("/account/links")
async def get_links(request: Request):
    user = await _require_user(request)
    connections = await brain.get_platform_connections(int(user["id"]))
    space_connections = await brain.list_platform_space_connections(account_user_id=int(user["id"]))
    return {"user": _public_user(user), "platform_connections": connections, "space_connections": space_connections}

import random
import string

@router.post("/account/links")
async def update_links(request: Request, payload: LinkAccountsRequest):
    require_csrf(request)
    user = await _require_user(request)

    response_data = {"status": "ok", "actions": []}

    # Handle Discord Linking
    if payload.discord_user_id is not None:
        discord_user_id = payload.discord_user_id.strip()
        if discord_user_id:
            # Manual Discord ID linking is intentionally disabled. Use Discord OAuth
            # (recommended) or the pairing-code flow.
            if discord_user_id == (user.get("discord_user_id") or ""):
                # No-op: keep existing link unchanged.
                pass
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Discord linking must be done via Discord OAuth (Settings → Connect via Discord).",
                )
        else:
            # Unlink if empty
            await brain.update_app_user(user["id"], {"discord_user_id": None})

    # Refresh user to get latest state
    updated_user = await brain.get_app_user_by_id(user["id"])
    response_data["user"] = _public_user(updated_user) if updated_user else None
    
    return response_data

@router.get("/account/profile")
async def get_profile(request: Request):
    """Get the rich user profile (interests, settings, etc)."""
    user = await _require_user(request)
    # 2026-06-11: same wrong-ID class as the Memory Galaxy fix — in local
    # mode profile data is written under the app owner id, not the linked
    # Discord id that _get_effective_user_id prefers.
    palli_user_id = _get_memory_owner_id(user)
    
    # We default to 'discord' as the primary personality platform for now
    profile = await brain.get_user_profile(palli_user_id, "discord")
    
    preferences = profile.get("preferences", {})

    consent = preferences.get("proactive_consent")
    proactive_enabled = False
    if isinstance(consent, dict):
        enabled = consent.get("enabled")
        if isinstance(enabled, bool):
            proactive_enabled = enabled
    elif isinstance(consent, bool):
        proactive_enabled = consent

    freq_raw = preferences.get("check_in_frequency", "normal")
    freq_map = {
        "hourly": "high",
        "daily": "normal",
        "weekly": "low",
        "never": "low",
    }
    check_in_freq = freq_map.get(freq_raw, freq_raw or "normal")

    # Human-readable recent style snapshot (situational; updated periodically)
    recent_style = None
    comm_style = profile.get("communication_style", {}) or {}
    if isinstance(comm_style, str):
        try:
            comm_style = json.loads(comm_style)
        except Exception:
            comm_style = {}
    if isinstance(comm_style, dict):
        recent = comm_style.get("recent")
        if isinstance(recent, dict):
            summary = (recent.get("summary") or "").strip()
            rationale = (recent.get("rationale") or "").strip()
            guidance = (recent.get("response_guidance") or "").strip()

            # Keep payload compact and predictable for the dashboard
            recent_style = {
                "summary": summary,
                "rationale": rationale,
                "responseGuidance": guidance,
                "updatedAt": recent.get("updated_at"),
                "windowMessages": recent.get("window_messages"),
                "confidence": recent.get("confidence"),
            }
            recent_style = {k: v for k, v in recent_style.items() if v not in ("", None, [])}
            if not recent_style:
                recent_style = None

    return {
        "name": profile.get("display_name") or profile.get("username") or "",
        "interests": preferences.get("user_interests_raw", ""),
        "checkInFreq": check_in_freq,
        "voiceEnabled": preferences.get("voice_enabled", True),
        "proactiveEnabled": proactive_enabled,
        "personalityPreset": profile.get("personality_preset") or "default",
        "recentStyle": recent_style,
    }


@router.get("/account/setup")
async def get_guided_setup(request: Request):
    user = await _require_user(request)
    subject_user_id, identity_platform = await _resolve_setup_identity_target(user)
    profile_platform = _profile_storage_platform(identity_platform)
    profile = await brain.get_user_profile(subject_user_id, profile_platform)
    state = await guided_setup_dashboard.get_guided_setup_state(
        account_user_id=int(user["id"]),
        subject_user_id=subject_user_id,
        identity_platform=identity_platform,
        profile=profile,
        status_actor_id="guided_setup_api",
        status_reason="guided_setup_api",
    )

    return {
        "setupCompleted": bool(state.setup_completed),
        "currentStep": state.current_step,
        "completedSteps": list(state.completed_steps),
        "contract": guided_setup_dashboard.build_guided_setup_contract(state=state),
        "draft": guided_setup_dashboard.build_guided_setup_draft(
            profile=state.profile,
            bundle=state.bundle,
        ),
        "identityPlatform": identity_platform,
        "ownerUserId": subject_user_id if identity_platform == "owner" else None,
    }


@router.post("/account/purge")
@limiter.limit("2/minute")
async def purge_account_data(request: Request, payload: PurgeDataRequest):
    require_csrf(request)
    user = await _require_user(request)

    if (payload.confirm_text or "").strip().upper() != "DELETE":
        raise HTTPException(status_code=400, detail="Confirmation text did not match")

    targets = [t.strip() for t in (payload.targets or []) if t and t.strip()]
    if not targets:
        raise HTTPException(status_code=400, detail="No data categories selected")

    table_map = {
        "conversations": ["conversations"],
        "memories": ["user_memory"],
        "images": ["images"],
        "profile": ["user_profiles"],
        "analytics": ["learning_analytics", "reflection_runs"],
        "reminders": ["scheduled_reminders"],
        "proactive": ["proactive_actions"],
        "audit": ["audit_logs"],
        "auth_sessions": ["sessions"],
        "reset_tokens": ["password_reset_tokens"],
        "pairing_codes": ["pairing_codes"],
    }

    tables: List[str] = []
    for target in targets:
        mapped = table_map.get(target)
        if not mapped:
            raise HTTPException(status_code=400, detail=f"Unknown data category: {target}")
        tables.extend(mapped)

    ids = {user["id"]}
    palli_user_id = user.get("palli_user_id")
    if isinstance(palli_user_id, int):
        ids.add(palli_user_id)

    discord_id = user.get("discord_user_id")
    if discord_id:
        try:
            ids.add(int(discord_id))
        except ValueError:
            pass

    counts = await brain.purge_user_data(list(ids), tables)

    await brain.log_audit_event(
        user_id=user["id"],
        action="data_purged",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details=f"Purged data targets={targets} for user_ids={sorted(ids)}",
    )

    return {"ok": True, "deleted": counts}


@router.get("/account/conversations/export")
async def export_conversations(request: Request):
    """
    Download the user's stored conversation history as a JSON file.

    This exports only the requesting user's data (based on app user id + linked
    Discord id, if present).
    """
    import json
    import time

    user = await _require_user(request)

    ids = {user["id"]}
    palli_user_id = user.get("palli_user_id")
    if isinstance(palli_user_id, int):
        ids.add(palli_user_id)

    discord_id = user.get("discord_user_id")
    if discord_id:
        try:
            ids.add(int(discord_id))
        except ValueError:
            pass

    export = await brain.export_user_conversations(list(ids))
    payload = json.dumps(export, ensure_ascii=False, indent=2)

    filename = f"palli_conversations_{user['id']}_{int(time.time())}.json"
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/account/profile")
async def update_profile(request: Request, payload: ProfileUpdateRequest):
    """Update the rich user profile."""
    require_csrf(request)
    user = await _require_user(request)
    # 2026-06-11: write to the same id reads use (see get_profile note).
    palli_user_id = _get_memory_owner_id(user)
    
    # Fetch existing to merge
    current = await brain.get_user_profile(palli_user_id, "discord")
    prefs = current.get("preferences", {})
    
    updates = {}
    
    if payload.name is not None:
        updates["display_name"] = payload.name
        # Also update username if not set?
        if not current.get("username"):
            updates["username"] = payload.name
            
    if payload.personality_preset is not None:
        updates["personality_preset"] = payload.personality_preset

    prefs_updates = {}
    if payload.interests is not None:
        # Store raw text for dashboard text area
        prefs_updates["user_interests_raw"] = payload.interests
        # Also parse into list for bot logic
        interest_list = [x.strip() for x in payload.interests.replace(",", " ").split() if len(x) > 3]
        prefs_updates["interests"] = interest_list
        
    if payload.check_in_freq is not None:
        prefs_updates["check_in_frequency"] = payload.check_in_freq
        
    if payload.voice_enabled is not None:
        prefs_updates["voice_enabled"] = payload.voice_enabled

    if payload.proactive_enabled is not None:
        prefs_updates["proactive_consent"] = {
            "enabled": bool(payload.proactive_enabled),
            "source": "dashboard",
            "updated_at": time.time(),
        }

    if prefs_updates:
        # Merge with existing preferences
        merged_prefs = {**prefs, **prefs_updates}
        updates["preferences"] = merged_prefs

    if updates:
        await brain.update_user_profile(palli_user_id, "discord", updates)
        
    return {"status": "ok"}


@router.post("/account/setup")
async def complete_guided_setup(request: Request, payload: GuidedSetupRequest):
    require_csrf(request)
    user = await _require_user(request)

    name = (payload.name or "").strip()
    if len(name) < 1 or len(name) > 64:
        raise HTTPException(status_code=400, detail="Name must be between 1 and 64 characters.")
    if payload.companion_style not in GUIDED_SETUP_STYLE_PRESETS:
        raise HTTPException(status_code=400, detail="Unknown companion style.")
    if payload.autonomy_preset not in GUIDED_SETUP_AUTONOMY_PRESETS:
        raise HTTPException(status_code=400, detail="Unknown autonomy preset.")
    if payload.checkin_posture is not None and payload.checkin_posture not in GUIDED_SETUP_CHECKIN_POSTURES:
        raise HTTPException(status_code=400, detail="Unknown check-in posture.")
    if payload.check_in_freq is not None and payload.check_in_freq not in GUIDED_SETUP_CHECKIN_OPTIONS:
        raise HTTPException(status_code=400, detail="Unknown check-in frequency.")
    if payload.voice_posture is not None and payload.voice_posture not in GUIDED_SETUP_VOICE_POSTURES:
        raise HTTPException(status_code=400, detail="Unknown voice posture.")
    relationship_style = str(payload.relationship_style or "").strip().lower()
    if relationship_style and relationship_style not in GUIDED_SETUP_RELATIONSHIP_STYLES:
        raise HTTPException(status_code=400, detail="Unknown relationship style.")

    subject_user_id, identity_platform = await _resolve_setup_identity_target(user)
    profile_platform = _profile_storage_platform(identity_platform)
    now = time.time()
    checkin_posture = str(payload.checkin_posture or "").strip().lower() or guided_setup_dashboard.derive_dashboard_checkin_posture(
        proactive_enabled=bool(payload.proactive_enabled),
        check_in_freq=str(payload.check_in_freq or "normal"),
    )
    voice_posture = str(payload.voice_posture or "").strip().lower() or guided_setup_dashboard.derive_dashboard_voice_posture(
        voice_enabled=bool(payload.voice_enabled),
    )
    checkin_preset = GUIDED_SETUP_CHECKIN_POSTURES[checkin_posture]
    voice_preset = GUIDED_SETUP_VOICE_POSTURES[voice_posture]

    current_profile = await brain.get_user_profile(subject_user_id, profile_platform)
    current_preferences = dict(current_profile.get("preferences") or {})
    merged_preferences = {
        **current_preferences,
        "voice_enabled": bool(voice_preset["profile"]["voice_enabled"]),
        "check_in_frequency": checkin_preset["check_in_freq"],
        "proactive_consent": {
            "enabled": bool(checkin_preset["proactive_enabled"]),
            "source": "guided_setup",
            "updated_at": now,
        },
    }

    profile_updates: Dict[str, Any] = {
        "display_name": name,
        "personality_preset": GUIDED_SETUP_STYLE_PRESETS[payload.companion_style]["profile_preset"],
        "preferences": merged_preferences,
    }
    if not str(current_profile.get("username") or "").strip():
        profile_updates["username"] = name

    profile_ids = {int(subject_user_id)}
    effective_user_id = _get_effective_user_id(user)
    profile_ids.add(int(effective_user_id))
    raw_discord_user_id = user.get("discord_user_id")
    if raw_discord_user_id:
        try:
            profile_ids.add(int(raw_discord_user_id))
        except ValueError:
            pass

    for profile_user_id in profile_ids:
        await brain.update_user_profile(profile_user_id, "discord", profile_updates)

    await identity_service.update_structured_document(
        user_id=subject_user_id,
        platform=identity_platform,
        doc_key="personality_profile",
        updates=dict(GUIDED_SETUP_STYLE_PRESETS[payload.companion_style]["personality"]),
        actor_type="user",
        actor_id=str(user["id"]),
        reason="guided_setup_personality",
    )
    await identity_service.update_structured_document(
        user_id=subject_user_id,
        platform=identity_platform,
        doc_key="action_preferences",
        updates=dict(GUIDED_SETUP_AUTONOMY_PRESETS[payload.autonomy_preset]),
        actor_type="user",
        actor_id=str(user["id"]),
        reason="guided_setup_autonomy",
    )
    await identity_service.update_structured_document(
        user_id=subject_user_id,
        platform=identity_platform,
        doc_key="voice_preferences",
        updates=dict(voice_preset["voice_preferences"]),
        actor_type="user",
        actor_id=str(user["id"]),
        reason="guided_setup_voice",
    )
    if relationship_style:
        await identity_service.update_structured_document(
            user_id=subject_user_id,
            platform=identity_platform,
            doc_key="personality_profile",
            updates={"relationship_style": GUIDED_SETUP_RELATIONSHIP_STYLES[relationship_style]},
            actor_type="user",
            actor_id=str(user["id"]),
            reason="guided_setup_relationship",
        )

    await guided_setup_dashboard.persist_guided_setup_completion(
        account_user_id=int(user["id"]),
        completed_targets=[
            "user_name",
            "companion_style",
            "autonomy_preset",
            "checkin_posture",
            "voice_posture",
            *(
                ["relationship_style"]
                if relationship_style
                else []
            ),
        ],
    )

    refreshed_profile = await brain.get_user_profile(subject_user_id, profile_platform)
    state = await guided_setup_dashboard.get_guided_setup_state(
        account_user_id=int(user["id"]),
        subject_user_id=subject_user_id,
        identity_platform=identity_platform,
        profile=refreshed_profile,
        status_actor_id="guided_setup_api",
        status_reason="guided_setup_api",
    )

    return {
        "status": "ok",
        "setupCompleted": bool(state.setup_completed),
        "currentStep": state.current_step,
        "completedSteps": list(state.completed_steps),
        "contract": guided_setup_dashboard.build_guided_setup_contract(state=state),
        "draft": guided_setup_dashboard.build_guided_setup_draft(
            profile=state.profile,
            bundle=state.bundle,
        ),
    }


@router.post("/account/test-ping")
async def send_test_ping(request: Request):
    require_csrf(request)
    user = await _require_runtime_access_user(request)
    
    discord_user_id = user.get("discord_user_id")
    if not discord_user_id:
        raise HTTPException(status_code=400, detail="Discord not linked")
        
    await brain.queue_job("send_test_ping", {
        "user_id": discord_user_id,
        "username": user.get("email", "User").split("@")[0] # Simple username from email
    })
    
    return {"status": "ok", "message": "Ping queued"}


@router.get("/account/memory")
async def get_memories(request: Request):
    """Get list of long-term memories for the user."""
    user = await _require_user(request)
    palli_user_id = _get_memory_owner_id(user)
    
    memories = await brain.get_user_memories(
        user_id=palli_user_id,
        memory_type=None,
        min_importance=0.1,
        # Deliberately raw (spec build note #2): this listing is the owner's
        # only view/delete surface over superseded originals, which are
        # permanent by owner decision #1.
        include_superseded=True,
    )

    # "Style" is tracked as a recent snapshot on the user profile, not displayed as long-term memory nodes.
    memories = [
        m
        for m in memories
        if (m.get("memory_type") or "").strip().lower() != "style"
    ]
    
    return {"memories": memories}


@router.get("/account/memory/overview")
async def get_memory_overview(request: Request):
    user = await _require_user(request)
    owner_user_id = await _resolve_memory_owner_user_id(user)
    return await memory_workspace_service.build_overview(owner_user_id=int(owner_user_id))


@router.get("/account/memory/search")
async def search_memory_workspace(request: Request, q: str = "", limit: int = 8):
    user = await _require_user(request)
    owner_user_id = await _resolve_memory_owner_user_id(user)
    return await memory_workspace_service.search(
        owner_user_id=int(owner_user_id),
        query=str(q or ""),
        limit=max(1, min(int(limit or 8), 24)),
    )


@router.get("/account/memory/days")
async def get_memory_days(request: Request, limit: int = 30):
    user = await _require_user(request)
    owner_user_id = await _resolve_memory_owner_user_id(user)
    return {
        "days": await memory_workspace_service.list_days(
            owner_user_id=int(owner_user_id),
            limit=max(1, min(int(limit or 30), 180)),
        )
    }


@router.get("/account/memory/day/{day}")
async def get_memory_day(day: str, request: Request):
    user = await _require_user(request)
    owner_user_id = await _resolve_memory_owner_user_id(user)
    try:
        payload = await memory_workspace_service.get_day(owner_user_id=int(owner_user_id), day=str(day or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@router.get("/account/memory/docs")
async def get_memory_docs(request: Request):
    user = await _require_user(request)
    owner_user_id = await _resolve_memory_owner_user_id(user)
    return await memory_workspace_service.get_docs(owner_user_id=int(owner_user_id))


@router.delete("/account/memory/{memory_id}")
async def delete_memory(request: Request, memory_id: int):
    """Forget a specific memory - the row and the document entry behind it.

    Week 2 task 6 (2026-09-10): deleting the row alone let the next
    preferences rebuild put the fact back. ``forget_user_memory`` erases
    the source FIRST and reports which frontmatter key (if any) went with
    it, so the answer says what was actually forgotten. If the document
    write fails nothing has been deleted, and this answers 500 rather
    than "ok" — a failed forget must read as a failed forget. Review fix
    2: each failure is named for what it actually was, and anything else
    keeps its own generic 500 rather than wearing a label that is untrue.
    """
    require_csrf(request)
    user = await _require_user(request)
    palli_user_id = _get_memory_owner_id(user)

    try:
        result = await forget_user_memory(user_id=palli_user_id, memory_id=memory_id)
    except ForgetSourceWriteError as exc:
        logger.warning(
            "delete_memory: the source document write failed forgetting memory %s "
            "for owner %s: %s",
            memory_id, palli_user_id, exc,
        )
        raise HTTPException(
            status_code=500,
            detail="could not forget: the source document write failed",
        ) from exc
    except ForgetRowDeleteError as exc:
        logger.warning(
            "delete_memory: the source was erased but memory %s for owner %s could "
            "not be deleted: %s",
            memory_id, palli_user_id, exc,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "the memory's source was erased but the row could not be deleted; "
                "the next rebuild clears it"
            ),
        ) from exc

    # A row that vanished between the read and the delete is NOT "not
    # found" when its source was erased on the way: the forget holds,
    # because the fact can no longer be rebuilt.
    if not result.get("deleted_row") and result.get("source_removed") is None:
        raise HTTPException(status_code=404, detail="Memory not found or not owned by user")

    return {
        "status": "ok",
        "deleted_row": bool(result.get("deleted_row")),
        "source_removed": result.get("source_removed"),
    }


import time

class GenerateCodeResponse(BaseModel):
    code: str
    expires_in: int

class PlatformLinkCodeRequest(BaseModel):
    platform: str

@router.post("/account/link/discord/code", response_model=GenerateCodeResponse)
async def generate_pairing_code(request: Request):
    """
    Generate a short-lived 6-character code that the user can use 
    in Discord to link their account via `/link_account <code>`.
    """
    require_csrf(request)
    user = await _require_user(request)
    user_id = user["id"]

    # Generate a code (e.g. 8X92MK)
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    # Expiration (15 minutes)
    expires_in = 15 * 60
    expires_at = time.time() + expires_in
    
    await brain.store_pairing_code(code, user_id, expires_at)
    
    return {
        "code": code,
        "expires_in": expires_in
    }


@router.post("/account/link/{platform}/code", response_model=GenerateCodeResponse)
async def generate_platform_pairing_code(request: Request, platform: str):
    require_csrf(request)
    user = await _require_user(request)
    clean_platform = str(platform or "").strip().lower()
    if clean_platform not in {"discord", "telegram", "slack"}:
        raise HTTPException(status_code=400, detail="Unsupported platform")

    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    expires_in = 15 * 60
    expires_at = time.time() + expires_in
    await brain.store_pairing_code(code, int(user["id"]), expires_at)
    return {"code": code, "expires_in": expires_in}
