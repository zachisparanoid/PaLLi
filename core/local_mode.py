from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from core.config import config
from core.database import brain

logger = logging.getLogger(__name__)


def local_mode_enabled() -> bool:
    mode = str(getattr(config, "PALLI_DEPLOYMENT_MODE", "local") or "local").strip().lower()
    return mode in {"local", "local_single_user"}


def local_auth_bypass_enabled() -> bool:
    return local_mode_enabled() and bool(getattr(config, "LOCAL_DASHBOARD_NO_AUTH", True))


def local_operator_open_discord_enabled() -> bool:
    """
    In local mode, Discord should work without SaaS-era account or guild linking.

    Linked accounts and guild mappings can still provide optional feature
    overrides, but they are not prerequisites for core local operation.
    """
    return local_mode_enabled()


async def ensure_local_owner() -> Dict[str, Any]:
    email = str(getattr(config, "LOCAL_OWNER_EMAIL", "local@palli.local") or "local@palli.local").strip()
    display_name = (
        str(getattr(config, "LOCAL_OWNER_DISPLAY_NAME", "Local Operator") or "Local Operator").strip()
        or "Local Operator"
    )
    now = time.time()

    user = await brain.get_app_user_by_email(email)
    if not user:
        user_id = await brain.create_app_user(
            email=email,
            password_hash=None,
            google_sub=None,
            role="admin",
        )
        await brain.update_app_user(
            user_id,
            {
                "palli_user_id": user_id,
                "role": "admin",
                "has_access": True,
                "email_verified": True,
                "email_verified_at": now,
                "age_confirmed": True,
                "age_confirmed_at": now,
                "tos_accepted_at": now,
                "privacy_accepted_at": now,
                "tos_version": getattr(config, "TOS_VERSION", "local"),
                "privacy_version": getattr(config, "PRIVACY_VERSION", "local"),
            },
        )
        user = await brain.get_app_user_by_id(user_id)
    else:
        updates: Dict[str, Any] = {}
        if user.get("role") != "admin":
            updates["role"] = "admin"
        if not user.get("palli_user_id"):
            updates["palli_user_id"] = int(user["id"])
        if not user.get("has_access"):
            updates["has_access"] = True
        if not user.get("email_verified"):
            updates["email_verified"] = True
            updates["email_verified_at"] = user.get("email_verified_at") or now
        if not user.get("age_confirmed"):
            updates["age_confirmed"] = True
            updates["age_confirmed_at"] = user.get("age_confirmed_at") or now
        if not user.get("tos_accepted_at"):
            updates["tos_accepted_at"] = now
            updates["tos_version"] = getattr(config, "TOS_VERSION", "local")
        if not user.get("privacy_accepted_at"):
            updates["privacy_accepted_at"] = now
            updates["privacy_version"] = getattr(config, "PRIVACY_VERSION", "local")
        if not user.get("discord_user_id"):
            raw_discord = str(getattr(config, "LOCAL_OWNER_DISCORD_USER_ID", "") or "").strip()
            if raw_discord:
                try:
                    int(raw_discord)
                except ValueError:
                    logger.warning(
                        "LOCAL_OWNER_DISCORD_USER_ID=%r is not numeric; ignoring",
                        raw_discord,
                    )
                else:
                    updates["discord_user_id"] = raw_discord
        # Telegram door (2026-09-14, task 1): the same seeding, the same
        # never-overwrite rule. A non-numeric value is logged and dropped —
        # a Telegram user id is digits, and a placeholder that LOOKS set
        # would leave him a guest on his own door.
        if not user.get("telegram_user_id"):
            raw_telegram = str(getattr(config, "LOCAL_OWNER_TELEGRAM_USER_ID", "") or "").strip()
            if raw_telegram:
                try:
                    int(raw_telegram)
                except ValueError:
                    logger.warning(
                        "LOCAL_OWNER_TELEGRAM_USER_ID=%r is not numeric; ignoring",
                        raw_telegram,
                    )
                else:
                    updates["telegram_user_id"] = raw_telegram
        if updates:
            await brain.update_app_user(int(user["id"]), updates)
            user = await brain.get_app_user_by_id(int(user["id"]))

    if not user:
        raise RuntimeError("Failed to resolve local owner")

    palli_user_id = int(user.get("palli_user_id") or user["id"])
    profile = await brain.get_user_profile(palli_user_id, "discord")
    profile_updates: Dict[str, Any] = {}
    if not profile.get("display_name"):
        profile_updates["display_name"] = display_name
    if not profile.get("username"):
        profile_updates["username"] = display_name

    preferences = dict(profile.get("preferences") or {})
    preference_updates: Dict[str, Any] = {}
    if "voice_enabled" not in preferences:
        preference_updates["voice_enabled"] = True
    if "proactive_consent" not in preferences:
        preference_updates["proactive_consent"] = {
            "enabled": True,
            "source": "local_default",
            "updated_at": now,
        }
    if preference_updates:
        profile_updates["preferences"] = {**preferences, **preference_updates}

    if profile_updates:
        await brain.update_user_profile(palli_user_id, "discord", profile_updates)

    refreshed = await brain.get_app_user_by_id(int(user["id"]))
    if refreshed:
        return refreshed
    return user


async def get_local_owner_refs() -> Dict[str, Optional[int]]:
    """
    Return the canonical ids that represent the local owner across surfaces.
    """
    if not local_mode_enabled():
        return {
            "app_user_id": None,
            "palli_user_id": None,
            "discord_user_id": None,
            "telegram_user_id": None,
        }

    owner = await ensure_local_owner()
    app_user_id = int(owner.get("id") or 0) or None
    palli_user_id = int(owner.get("palli_user_id") or owner.get("id") or 0) or None

    discord_user_id = None
    raw_discord_user_id = owner.get("discord_user_id")
    if raw_discord_user_id not in {None, ""}:
        try:
            discord_user_id = int(raw_discord_user_id)
        except Exception:
            discord_user_id = None

    telegram_user_id = None
    raw_telegram_user_id = owner.get("telegram_user_id")
    if raw_telegram_user_id not in {None, ""}:
        try:
            telegram_user_id = int(raw_telegram_user_id)
        except Exception:
            telegram_user_id = None

    return {
        "app_user_id": app_user_id,
        "palli_user_id": palli_user_id,
        "discord_user_id": discord_user_id,
        "telegram_user_id": telegram_user_id,
    }


async def resolve_local_owner_observer_user_id_for_subject(
    *,
    user_id: Optional[int] = None,
    platform: Optional[str] = None,
    account_user_id: Optional[int] = None,
) -> Optional[int]:
    """
    Return the local owner's app user id if the given subject belongs to the
    local owner. Otherwise return ``None``.
    """
    refs = await get_local_owner_refs()
    owner_app_user_id = refs.get("app_user_id")
    if owner_app_user_id is None:
        return None

    owner_candidates = {
        int(candidate)
        for candidate in (refs.get("app_user_id"), refs.get("palli_user_id"))
        if candidate is not None
    }
    owner_discord_user_id = refs.get("discord_user_id")
    owner_telegram_user_id = refs.get("telegram_user_id")
    normalized_platform = str(platform or "").strip().lower()

    try:
        if account_user_id is not None and int(account_user_id) in owner_candidates:
            return owner_app_user_id
    except Exception:
        pass

    try:
        subject_user_id = int(user_id) if user_id is not None else None
    except Exception:
        subject_user_id = None

    if subject_user_id is None:
        return owner_app_user_id if not normalized_platform else None

    if normalized_platform in {"local", "owner"} and subject_user_id in owner_candidates:
        return owner_app_user_id
    if normalized_platform == "discord" and owner_discord_user_id is not None and subject_user_id == owner_discord_user_id:
        return owner_app_user_id
    # Telegram door (2026-09-14, task 1). Per-door, never pooled: his Discord
    # snowflake is not his Telegram id, and a stranger whose Telegram id
    # happens to equal it is still a stranger.
    if normalized_platform == "telegram" and owner_telegram_user_id is not None and subject_user_id == owner_telegram_user_id:
        return owner_app_user_id
    if not normalized_platform and subject_user_id in owner_candidates:
        return owner_app_user_id
    return None
