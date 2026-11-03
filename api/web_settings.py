"""REST API for PaLLi settings service.

Exposes snapshot (read), propose (preview), apply (commit),
and rollback (undo) endpoints. Used by the dashboard and
by the Phase 4 chat-configuration agent tools.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel as PydanticBaseModel

from api.web_auth import _require_user
from core.settings_boot import settings_service
from core.settings_service import SettingsServiceError
from core.web_security import require_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/web/settings", tags=["settings"])


class ProposeRequest(PydanticBaseModel):
    changes: Dict[str, Any]
    reason: str = ""


class ApplyRequest(PydanticBaseModel):
    change_id: str


class RollbackRequest(PydanticBaseModel):
    rollback_token: str


@router.get("/snapshot")
async def get_snapshot(http_request: Request, section: Optional[str] = None):
    """Return the current settings snapshot (or a section of it)."""
    await _require_user(http_request)
    try:
        snapshot = settings_service.snapshot()
    except SettingsServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    data = snapshot.model_dump(mode="json")
    if section:
        value = data.get(section)
        if value is None:
            raise HTTPException(status_code=404, detail=f"section {section!r} not found")
        return {section: value}
    return data


@router.get("/providers")
async def list_providers(http_request: Request):
    """Return the provider catalog with their models."""
    await _require_user(http_request)
    snapshot = settings_service.snapshot()
    return snapshot.model_dump(mode="json").get("providers", {})


@router.post("/propose")
async def propose_change(req: ProposeRequest, http_request: Request):
    """Validate a proposed settings change and return a preview diff."""
    await _require_user(http_request)
    require_csrf(http_request)
    try:
        result = settings_service.propose(req.changes, reason=req.reason)
    except SettingsServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "change_id": result.change_id,
        "diff": result.diff,
        "warnings": result.warnings,
    }


@router.post("/apply")
async def apply_change(req: ApplyRequest, http_request: Request):
    """Apply a previously proposed settings change."""
    await _require_user(http_request)
    require_csrf(http_request)
    try:
        result = settings_service.apply(req.change_id)
    except SettingsServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "applied": result.applied,
        "rollback_token": result.rollback_token,
    }


@router.post("/rollback")
async def rollback_change(req: RollbackRequest, http_request: Request):
    """Roll back a recently applied settings change."""
    await _require_user(http_request)
    require_csrf(http_request)
    try:
        restored = settings_service.rollback(req.rollback_token)
    except SettingsServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "rolled_back": True,
        "version": restored.version,
    }
