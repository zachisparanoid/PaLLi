"""
Auth endpoints that remain relevant to PaLLi Local.

Local mode bypasses normal web auth for the primary dashboard, but optional
Discord OAuth still uses this router.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status

from core.config import config
from core.database import brain
from core.web_security import (
    clear_auth_cookies,
    create_session_jwt,
    get_user_id_from_request,
    new_csrf_token,
    set_auth_cookies,
)
from core.local_mode import ensure_local_owner, local_auth_bypass_enabled, local_mode_enabled
from core.mobile_auth import bearer_token, device_for_request

logger = logging.getLogger(__name__)


router = APIRouter()


def _public_auth_surface() -> Dict[str, Any]:
    local_no_auth = local_auth_bypass_enabled()
    return {
        "dashboard_auth_mode": "open" if local_no_auth else "session",
        "cloud_auth_routes_enabled": not local_no_auth,
        "account_security_routes_enabled": not local_no_auth,
    }


def _me_payload(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The /auth/me body, one shape for every way the owner got here.

    `local_mode` (first door, 2026-09-17, task 4) is how the dashboard learns it
    is running on a self-hosted install and hides the SaaS-era account pages.
    It is read from `local_mode_enabled()` — the one function `GET
    /api/setup/status` reads it from too, so the two surfaces can never disagree
    about the same install. It is deliberately NOT derived from `auth_surface`:
    LOCAL_DASHBOARD_NO_AUTH can be off on a perfectly local install.
    """
    return {
        "user": _public_user(user) if user else None,
        "auth_surface": _public_auth_surface(),
        "local_mode": local_mode_enabled(),
    }


def _public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    # Keep the owner-facing payload local-first and omit dormant hosted-access fields.
    return {
        "id": user["id"],
        "email": user["email"],
        "email_verified": bool(user.get("email_verified")),
        "email_verified_at": user.get("email_verified_at"),
        "mfa_enabled": bool(user.get("mfa_enabled")),
        "age_confirmed": bool(user.get("age_confirmed")),
        "age_confirmed_at": user.get("age_confirmed_at"),
        "tos_accepted_at": user.get("tos_accepted_at"),
        "privacy_accepted_at": user.get("privacy_accepted_at"),
        "tos_version": user.get("tos_version"),
        "privacy_version": user.get("privacy_version"),
        "discord_user_id": user.get("discord_user_id"),
        "role": user.get("role", "user"),
    }


async def _owner_for_device(device: Dict[str, Any]) -> Dict[str, Any]:
    if local_mode_enabled():
        return await ensure_local_owner()
    user = await brain.get_app_user_by_id(int(device["owner_id"]))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def _require_user(request: Request) -> Dict[str, Any]:
    # The mobile door (2026-10-13, spec §3 rule 2): a request that PRESENTS a
    # device token is judged by it alone. A bad or revoked one is a 401 —
    # never a fall-through to the no-login owner below.
    if bearer_token(request) is not None:
        device = await device_for_request(request)
        if device is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This phone is not paired.")
        return await _owner_for_device(device)
    if local_auth_bypass_enabled():
        return await ensure_local_owner()
    user_id = get_user_id_from_request(request)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = await brain.get_app_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


@router.post("/auth/logout")
async def logout(response: Response):
    if not local_auth_bypass_enabled():
        clear_auth_cookies(response)
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request, response: Response):
    if local_auth_bypass_enabled():
        user = await ensure_local_owner()
        # CSRF bootstrap for the no-login dashboard (2026-06-11): bypass mode
        # never runs a login flow, so this is where the double-submit cookie
        # gets minted. require_csrf now enforces in bypass mode; without this
        # cookie every mutating call from the dashboard would 403.
        if not request.cookies.get(config.CSRF_COOKIE_NAME):
            response.set_cookie(
                key=config.CSRF_COOKIE_NAME,
                value=new_csrf_token(),
                max_age=int(getattr(config, "SESSION_COOKIE_MAX_AGE_SECONDS", 1209600)),
                httponly=False,
                secure=bool(getattr(config, "SESSION_COOKIE_SECURE", False)),
                samesite="lax",
                path="/",
            )
        return _me_payload(user)
    user_id = get_user_id_from_request(request)
    if not user_id:
        return _me_payload(None)
    user = await brain.get_app_user_by_id(user_id)
    if not user:
        clear_auth_cookies(response)
        return _me_payload(None)

    # Ensure a CSRF cookie exists for authenticated sessions.
    if not request.cookies.get(config.CSRF_COOKIE_NAME):
        csrf = new_csrf_token()
        session_jwt = create_session_jwt(user_id=user["id"], email=user["email"])
        set_auth_cookies(response, session_jwt, csrf)

    return _me_payload(user)


@router.post("/auth/discord/start")
async def discord_start(request: Request):
    """Start Discord OAuth2 flow for account linking (POST with CSRF protection)."""
    from core.web_security import require_csrf
    require_csrf(request)

    if not config.DISCORD_CLIENT_ID or not config.DISCORD_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Discord OAuth not configured")

    user = await _require_user(request)
    user_id = int(user["id"])

    redirect_uri = config.DISCORD_OAUTH_REDIRECT_URL or f"{config.BACKEND_URL}/api/web/auth/discord/callback"
    state = new_csrf_token()

    logger.info(f"Discord OAuth initiated for user {user_id}")

    params = {
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent"
    }
    oauth_url = f"https://discord.com/api/oauth2/authorize?{urlencode(params)}"

    # Return the OAuth URL and set the state cookie
    # Frontend will redirect after receiving response
    resp = Response(
        content=json.dumps({"redirect_url": oauth_url}),
        media_type="application/json",
        status_code=200
    )
    resp.set_cookie(
        "palli_discord_oauth_state",
        state,
        max_age=600,
        httponly=True,
        secure=bool(getattr(config, "SESSION_COOKIE_SECURE", False)),
        samesite="lax",
        path="/",
    )
    return resp


@router.get("/auth/discord/callback")
async def discord_callback(request: Request, response: Response, code: Optional[str] = None, state: Optional[str] = None):
    """Handle Discord OAuth2 callback and link account."""
    if not config.DISCORD_CLIENT_ID or not config.DISCORD_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Discord OAuth not configured")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth parameters")

    cookie_state = request.cookies.get("palli_discord_oauth_state")
    logger.info(f"OAuth Callback - Cookies: {list(request.cookies.keys())}")
    logger.info(f"OAuth Callback - Cookie State: {cookie_state}, Query State: {state}")

    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    # Verify user is still logged in
    app_user = await _require_user(request)
    user_id = int(app_user["id"])

    redirect_uri = config.DISCORD_OAUTH_REDIRECT_URL or f"{config.BACKEND_URL}/api/web/auth/discord/callback"

    async with httpx.AsyncClient(timeout=15) as client:
        # Exchange code for token
        token_resp = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": config.DISCORD_CLIENT_ID,
                "client_secret": config.DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            logger.error(f"Discord token exchange failed: {token_resp.text}")
            raise HTTPException(status_code=400, detail="Discord token exchange failed")
        
        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        # Fetch user info from Discord
        user_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if user_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch Discord user info")
        
        discord_user = user_resp.json()
        discord_id = discord_user.get("id")

    if not discord_id:
        raise HTTPException(status_code=400, detail="Missing Discord user ID")

    # Prevent a single Discord ID from being linked to multiple PaLLi accounts.
    existing = await brain.get_app_user_by_discord_id(str(discord_id))
    if existing and existing.get("id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Discord account is already linked to another PaLLi account.",
        )

    # Link the account
    await brain.update_app_user(user_id, {"discord_user_id": discord_id})
    logger.info(f"✅ User {user_id} linked to Discord ID {discord_id}")

    # If the user already set up their profile on the website (display name, preferences, etc),
    # copy those settings onto the Discord user profile row. The bot and dashboard use the
    # Discord ID once linked, so without this the user may see "Unknown" until they re-save.
    try:
        source_id = app_user.get("palli_user_id") or app_user["id"]
        source_profile = await brain.get_user_profile(int(source_id), "discord")
        sync_updates: Dict[str, Any] = {}

        display_name = (source_profile.get("display_name") or "").strip()
        username = (source_profile.get("username") or "").strip()
        if display_name:
            sync_updates["display_name"] = display_name
        if username:
            sync_updates["username"] = username

        prefs = source_profile.get("preferences")
        if isinstance(prefs, dict) and prefs:
            sync_updates["preferences"] = prefs

        preset = (source_profile.get("personality_preset") or "").strip()
        if preset:
            sync_updates["personality_preset"] = preset

        if sync_updates:
            await brain.update_user_profile(int(discord_id), "discord", sync_updates)
            logger.info(f"Synced profile settings to Discord profile for user {user_id}")
    except Exception as e:
        logger.warning(f"Could not sync profile settings during Discord linking for user {user_id}: {e}")

    # Queue confirmation DM
    await brain.queue_job("send_linked_confirmation_dm", {
        "platform": "discord",
        "user_id": discord_id,
        "username": discord_user.get('username')
    })

    # Log audit event
    await brain.log_audit_event(
        user_id=user_id,
        action="discord_linked",
        ip_address=request.client.host,
        user_agent=request.headers.get("user-agent"),
        details=f"Linked Discord account: {discord_user.get('username')}#{discord_user.get('discriminator', '0')}"
    )

    # Clear state cookie
    response.delete_cookie("palli_discord_oauth_state", path="/")

    # Redirect back to dashboard
    response.status_code = 302
    response.headers["Location"] = f"{config.FRONTEND_URL}/dashboard"
    return response
