from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from api.web_account import _get_effective_user_id
from api.web_auth import _require_user
from core.config import config
from core.identity.constants import IDENTITY_DOC_LABELS, USER_DOCS
from core.identity.history_diff import build_markdown_diff
from core.identity.document_policy import document_policy_view, identity_governance_view
from core.identity.frontmatter import dump_frontmatter, parse_frontmatter
from core.identity.service import identity_service
from core.web_security import require_csrf
from core.database import brain

router = APIRouter()


class RawIdentityUpdateRequest(BaseModel):
    markdown: str
    reason: Optional[str] = None


class PersonalityProfileUpdateRequest(BaseModel):
    tone: Optional[str] = None
    warmth: Optional[str] = None
    humor: Optional[str] = None
    proactivity_style: Optional[str] = None
    challenge_level: Optional[str] = None
    voice_style: Optional[str] = None
    relationship_style: Optional[str] = None
    response_length: Optional[str] = None
    autonomy_posture: Optional[str] = None


class ActionPreferencesUpdateRequest(BaseModel):
    autonomy_mode: Optional[str] = None
    initiative_level: Optional[str] = None
    allow_bounded_reflection: Optional[bool] = None
    allow_proactive_followups: Optional[bool] = None
    allow_silent_beneficial_actions: Optional[bool] = None
    allow_silent_low_risk_actions: Optional[bool] = None
    allow_privileged_cli_actions: Optional[bool] = None
    allow_package_install_actions: Optional[bool] = None
    allow_schedule_creation: Optional[bool] = None
    allow_email_triage_actions: Optional[bool] = None
    allow_research_and_alert_loops: Optional[bool] = None
    require_confirmation_for_destructive_actions: Optional[bool] = None
    require_confirmation_for_external_spending: Optional[bool] = None
    post_action_reporting_mode: Optional[str] = None
    interruption_threshold: Optional[str] = None
    standing_orders_policy: Optional[str] = None
    followthrough_intensity: Optional[str] = None
    environment_optimization_mode: Optional[str] = None
    background_watch_mode: Optional[str] = None
    external_contact_policy: Optional[str] = None
    social_context_sensitivity: Optional[str] = None
    quiet_hours_mode: Optional[str] = None
    group_voice_behavior: Optional[str] = None


async def _resolve_identity_context(request: Request) -> tuple[Dict[str, Any], int, str]:
    user = await _require_user(request)
    owner_user_id = await identity_service.resolve_local_owner_user_id()
    if owner_user_id is not None:
        return user, owner_user_id, "owner"
    palli_user_id = _get_effective_user_id(user)
    platform = "discord"
    return user, palli_user_id, platform


def _serialize_document(doc) -> Dict[str, Any]:
    return {
        "docKey": doc.doc_key,
        "label": IDENTITY_DOC_LABELS.get(doc.doc_key, doc.doc_key.replace("_", " ").title()),
        "path": doc.path,
        "policy": dict(getattr(doc, "policy", {}) or document_policy_view(doc.doc_key)),
        "frontmatter": doc.frontmatter,
        "content": doc.content,
        "markdown": dump_frontmatter(doc.frontmatter, doc.content),
        "editable": bool(doc.editable),
    }


def _serialize_document_reference(
    doc_key: Optional[str],
    *,
    path: Optional[str] = None,
    version: Optional[int] = None,
    editable: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    key = str(doc_key or "").strip()
    if not key:
        return None
    try:
        policy = document_policy_view(key)
    except KeyError:
        policy = None
    payload: Dict[str, Any] = {
        "docKey": key,
        "label": IDENTITY_DOC_LABELS.get(key, key.replace("_", " ").title()),
        "policy": policy,
    }
    if path is not None:
        payload["path"] = path
    if version is not None:
        payload["version"] = version
    if editable is not None:
        payload["editable"] = bool(editable)
    return payload


def _serialize_history_summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    doc_key = entry.get("doc_key")
    return {
        "doc_key": doc_key,
        "version": entry.get("version"),
        "actor_type": entry.get("actor_type"),
        "actor_id": entry.get("actor_id"),
        "reason": entry.get("reason"),
        "created_at": entry.get("created_at"),
        "document": _serialize_document_reference(
            doc_key,
            version=entry.get("version"),
        ),
    }


def _serialize_history_detail(entry: Dict[str, Any]) -> Dict[str, Any]:
    markdown = str(entry.get("content") or "")
    parsed_frontmatter, content = parse_frontmatter(markdown)
    frontmatter = entry.get("frontmatter")
    if not isinstance(frontmatter, dict):
        frontmatter = parsed_frontmatter
    return {
        **_serialize_history_summary(entry),
        "file_path": entry.get("file_path"),
        "frontmatter": frontmatter,
        "content": content,
        "markdown": markdown,
        "document": _serialize_document_reference(
            entry.get("doc_key"),
            path=entry.get("file_path"),
            version=entry.get("version"),
        ),
    }


def _serialize_history_comparison(doc, *, history_markdown: str) -> Dict[str, Any]:
    current_markdown = dump_frontmatter(doc.frontmatter, doc.content)
    return {
        "target": "current_document",
        "current_label": IDENTITY_DOC_LABELS.get(doc.doc_key, doc.doc_key.replace("_", " ").title()),
        "current_path": doc.path,
        "current_version": doc.frontmatter.get("version"),
        "currentDocument": _serialize_document_reference(
            doc.doc_key,
            path=doc.path,
            version=doc.frontmatter.get("version"),
            editable=bool(doc.editable),
        ),
        **build_markdown_diff(
            history_markdown=history_markdown,
            current_markdown=current_markdown,
        ),
    }


def _require_identity_write_enabled() -> None:
    if not getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True):
        raise HTTPException(status_code=404, detail="Identity architecture is disabled")
    if not getattr(config, "IDENTITY_ENABLE_DASHBOARD_EDITING", True):
        raise HTTPException(status_code=403, detail="Dashboard identity editing is disabled")


@router.get("/account/identity")
async def get_identity_bundle(request: Request):
    if not getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True):
        raise HTTPException(status_code=404, detail="Identity architecture is disabled")
    user, palli_user_id, platform = await _resolve_identity_context(request)
    profile_platform = "discord" if platform == "owner" else platform
    profile = await brain.get_user_profile(palli_user_id, profile_platform)
    bundle = await identity_service.ensure_user_identity_bundle(
        user_id=palli_user_id,
        platform=platform,
        profile=profile,
        actor_type="system",
        actor_id="dashboard_bundle_load",
        reason="dashboard_bundle_load",
    )
    history = await identity_service.list_history(
        user_id=palli_user_id,
        platform=platform,
        limit=40,
    )
    return {
        "userId": palli_user_id,
        "platform": platform,
        "ownerUserId": palli_user_id if platform == "owner" else None,
        "coreDocuments": {
            key: _serialize_document(doc) for key, doc in bundle.core_documents.items()
        },
        "userDocuments": {
            key: _serialize_document(doc) for key, doc in bundle.user_documents.items()
        },
        "history": [_serialize_history_summary(entry) for entry in history],
        "governance": identity_governance_view(),
        "identityEditingEnabled": bool(
            getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True)
            and getattr(config, "IDENTITY_ENABLE_DASHBOARD_EDITING", True)
        ),
        "user": {
            "id": user["id"],
            "email": user.get("email"),
            "discordUserId": user.get("discord_user_id"),
        },
    }


@router.get("/account/identity/compiled")
async def get_compiled_identity_bundle(request: Request):
    if not getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True):
        raise HTTPException(status_code=404, detail="Identity architecture is disabled")
    _, palli_user_id, platform = await _resolve_identity_context(request)
    profile_platform = "discord" if platform == "owner" else platform
    profile = await brain.get_user_profile(palli_user_id, profile_platform)
    compiled = await identity_service.compile_identity(
        user_id=palli_user_id,
        platform=platform,
        ensure_materialized=True,
        profile=profile,
    )
    return compiled.to_dict()


@router.get("/account/identity/document/{doc_key}")
async def get_identity_document(request: Request, doc_key: str):
    if not getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True):
        raise HTTPException(status_code=404, detail="Identity architecture is disabled")
    _, palli_user_id, platform = await _resolve_identity_context(request)
    if doc_key not in IDENTITY_DOC_LABELS:
        raise HTTPException(status_code=404, detail="Unknown identity document")
    bundle = await identity_service.ensure_user_identity_bundle(
        user_id=palli_user_id,
        platform=platform,
    )
    doc = bundle.all_documents.get(doc_key)
    if not doc:
        raise HTTPException(status_code=404, detail="Identity document not found")
    return _serialize_document(doc)


@router.post("/account/identity/document/{doc_key}")
async def update_identity_document(request: Request, doc_key: str, payload: RawIdentityUpdateRequest):
    _require_identity_write_enabled()
    require_csrf(request)
    _, palli_user_id, platform = await _resolve_identity_context(request)
    if doc_key not in USER_DOCS:
        raise HTTPException(status_code=403, detail="Only user-scoped identity documents are editable here")
    try:
        doc = await identity_service.update_raw_document(
            user_id=palli_user_id,
            platform=platform,
            doc_key=doc_key,
            markdown=payload.markdown,
            actor_type="user",
            actor_id=str(palli_user_id),
            reason=payload.reason or "dashboard_raw_edit",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _serialize_document(doc)


@router.post("/account/identity/personality-profile")
async def update_personality_profile(request: Request, payload: PersonalityProfileUpdateRequest):
    _require_identity_write_enabled()
    require_csrf(request)
    _, palli_user_id, platform = await _resolve_identity_context(request)
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    doc = await identity_service.update_structured_document(
        user_id=palli_user_id,
        platform=platform,
        doc_key="personality_profile",
        updates=updates,
        actor_type="user",
        actor_id=str(palli_user_id),
        reason="dashboard_structured_personality_update",
    )
    return _serialize_document(doc)


@router.post("/account/identity/action-preferences")
async def update_action_preferences(request: Request, payload: ActionPreferencesUpdateRequest):
    _require_identity_write_enabled()
    require_csrf(request)
    _, palli_user_id, platform = await _resolve_identity_context(request)
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    doc = await identity_service.update_structured_document(
        user_id=palli_user_id,
        platform=platform,
        doc_key="action_preferences",
        updates=updates,
        actor_type="user",
        actor_id=str(palli_user_id),
        reason="dashboard_structured_action_update",
    )
    return _serialize_document(doc)


@router.get("/account/identity/history")
async def get_identity_history(request: Request, doc_key: Optional[str] = None, limit: int = 50):
    if not getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True):
        raise HTTPException(status_code=404, detail="Identity architecture is disabled")
    _, palli_user_id, platform = await _resolve_identity_context(request)
    if doc_key and doc_key not in IDENTITY_DOC_LABELS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown identity document")
    history = await identity_service.list_history(
        user_id=palli_user_id,
        platform=platform,
        doc_key=doc_key,
        limit=limit,
    )
    return {"history": [_serialize_history_summary(entry) for entry in history]}


@router.get("/account/identity/history/{doc_key}/{version}")
async def get_identity_history_version(request: Request, doc_key: str, version: int):
    if not getattr(config, "ENABLE_IDENTITY_ARCHITECTURE", True):
        raise HTTPException(status_code=404, detail="Identity architecture is disabled")
    _, palli_user_id, platform = await _resolve_identity_context(request)
    if doc_key not in IDENTITY_DOC_LABELS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown identity document")
    entry = await identity_service.get_history_entry(
        user_id=palli_user_id,
        platform=platform,
        doc_key=doc_key,
        version=version,
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Identity document version not found",
        )
    detail = _serialize_history_detail(entry)
    bundle = await identity_service.ensure_user_identity_bundle(
        user_id=palli_user_id,
        platform=platform,
    )
    current_doc = bundle.all_documents.get(doc_key)
    if current_doc:
        detail["comparison"] = _serialize_history_comparison(
            current_doc,
            history_markdown=detail["markdown"],
        )
    return detail
