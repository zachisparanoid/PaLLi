"""
Web auth + security helpers for PaLLi Local dashboard/account flows.

This module intentionally keeps the logic small and dependency-light:
- Password hashing via passlib[bcrypt]
- Cookie-based JWT sessions
- Double-submit CSRF protection for state-changing endpoints
"""

from __future__ import annotations

import secrets
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, Response, status
from jose import JWTError, jwt

from core.config import config
from core.local_mode import local_auth_bypass_enabled

# Great Excision stage 5 (2026-07-09): the password-hash/complexity helpers
# (passlib/bcrypt) went with the cloud-auth endpoints — the local dashboard
# runs in owner-bypass mode and no login surface exists. Session-JWT +
# CSRF plumbing below remains LIVE (cookie verify for non-bypass callers,
# CSRF double-submit for the dashboard).


def _now() -> int:
    return int(time.time())


def create_session_jwt(user_id: int, email: str) -> str:
    if not config.JWT_SECRET:
        raise RuntimeError("JWT_SECRET must be set to use web auth")

    iat = _now()
    exp = iat + int(getattr(config, "SESSION_COOKIE_MAX_AGE_SECONDS", 1209600))
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": iat,
        "exp": exp,
        "typ": "palli_session",
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def decode_session_jwt(token: str) -> Dict[str, Any]:
    if not config.JWT_SECRET:
        raise RuntimeError("JWT_SECRET must be set to use web auth")
    return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookies(response: Response, session_jwt: str, csrf_token: str) -> None:
    # Session cookie: httpOnly, protects the JWT from XSS.
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=session_jwt,
        max_age=int(getattr(config, "SESSION_COOKIE_MAX_AGE_SECONDS", 1209600)),
        httponly=True,
        secure=bool(getattr(config, "SESSION_COOKIE_SECURE", False)),
        samesite="lax",
        path="/",
    )

    # CSRF cookie: readable by JS; client must echo back via X-CSRF-Token header.
    response.set_cookie(
        key=config.CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=int(getattr(config, "SESSION_COOKIE_MAX_AGE_SECONDS", 1209600)),
        httponly=False,
        secure=bool(getattr(config, "SESSION_COOKIE_SECURE", False)),
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key=config.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(key=config.CSRF_COOKIE_NAME, path="/")


def require_csrf(request: Request) -> None:
    # The mobile door (2026-10-13, spec §3 rule 3): a request that proved
    # itself with a device token carries no ambient browser credential, and
    # an ambient credential is the only thing CSRF defends.
    if getattr(getattr(request, "state", None), "mobile_device", None) is not None:
        return
    # 2026-06-11: CSRF is now enforced even in local-auth-bypass mode. Bypass
    # mode means NO authentication, so the double-submit cookie is the ONLY
    # thing standing between a malicious webpage (opened on any LAN/tailnet
    # device that can reach this backend) and state-changing endpoints —
    # including chat, which can reach local_shell. The no-login dashboard
    # gets its cookie bootstrapped by GET /auth/me. CSRF_PROTECT_LOCAL=false
    # restores the old skip (emergency hatch only).
    if local_auth_bypass_enabled() and not bool(
        getattr(config, "CSRF_PROTECT_LOCAL", True)
    ):
        return
    cookie_token = request.cookies.get(config.CSRF_COOKIE_NAME)
    header_token = request.headers.get("x-csrf-token") or request.headers.get("X-CSRF-Token")
    if not cookie_token or not header_token or cookie_token != header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )


def get_user_id_from_request(request: Request) -> Optional[int]:
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = decode_session_jwt(token)
        sub = payload.get("sub")
        if not sub:
            return None
        return int(sub)
    except (JWTError, ValueError):
        return None


