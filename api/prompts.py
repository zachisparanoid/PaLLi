"""
Prompt management API for viewing and editing prompt files.
"""

from pathlib import Path
from typing import List, Dict

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from ai.prompt_loader import prompt_loader
from core.config import config
from core.database import brain
from core.web_security import get_user_id_from_request, require_csrf
from core.local_mode import ensure_local_owner, local_auth_bypass_enabled

router = APIRouter()

PROMPTS_DIR = Path("prompts").resolve()


class PromptUpdate(BaseModel):
    content: str


async def _require_admin(request: Request) -> Dict:
    if local_auth_bypass_enabled():
        return await ensure_local_owner()
    user_id = get_user_id_from_request(request)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    user = await brain.get_app_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    role = str(user.get("role", "")).strip().lower()
    admin_ids = set(getattr(config, "ADMIN_USER_IDS", []) or [])
    if role != "admin" and int(user_id) not in admin_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user


def _safe_prompt_path(prompt_name: str) -> Path:
    """Resolve a safe path for a prompt file within the prompts directory."""
    if not prompt_name:
        raise HTTPException(status_code=400, detail="Prompt name is required")

    # Restrict to simple names to avoid path traversal
    if any(ch in prompt_name for ch in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="Invalid prompt name")

    path = (PROMPTS_DIR / f"{prompt_name}.txt").resolve()
    if not str(path).startswith(str(PROMPTS_DIR)):
        raise HTTPException(status_code=400, detail="Invalid prompt path")

    return path


@router.get("")
async def list_prompts(request: Request) -> List[Dict]:
    """List available prompt files."""
    await _require_admin(request)

    if not PROMPTS_DIR.exists():
        raise HTTPException(status_code=404, detail="Prompts directory not found")

    entries: List[Dict] = []
    for path in sorted(PROMPTS_DIR.glob("*.txt")):
        stat = path.stat()
        entries.append(
            {
                "name": path.stem,
                "filename": path.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )

    return entries


@router.get("/{prompt_name}")
async def get_prompt(prompt_name: str, request: Request) -> Dict:
    """Get content of a specific prompt."""
    await _require_admin(request)

    path = _safe_prompt_path(prompt_name)

    if not path.exists():
        raise HTTPException(status_code=404, detail="Prompt not found")

    try:
        with path.open("r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "name": prompt_name,
        "filename": path.name,
        "content": content,
    }


@router.put("/{prompt_name}")
async def update_prompt(prompt_name: str, update: PromptUpdate, request: Request) -> Dict:
    """Update a prompt file and refresh in-memory caches."""
    await _require_admin(request)
    require_csrf(request)

    path = _safe_prompt_path(prompt_name)

    try:
        PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write(update.content or "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Refresh prompt caches
    try:
        prompt_loader.reload_prompt(prompt_name)
    except Exception:
        # Cache reload failures should not break the API write
        pass

    return {"status": "ok"}
