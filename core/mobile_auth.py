"""Who a request is on the mobile door: a device token in the Authorization
header (spec §3, 2026-10-13).

``bearer_token`` tells "no token presented" (None) from "a token presented"
(a string, maybe blank). The difference is the security rule: a request that
presents a token is judged by that token alone, and a bad one is refused —
it never falls through to the no-login owner.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from core.mobile_device_store import MobileDeviceStore


def bearer_token(request: Any) -> Optional[str]:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, value = str(header).partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    return value.strip()


def device_store() -> MobileDeviceStore:
    # Resolved at call time, so a test (or palli_ctl) that points
    # core.database.brain at another database is honoured.
    from core import database

    return MobileDeviceStore(database.brain)


async def device_for_request(request: Any) -> Optional[Dict[str, Any]]:
    """The device the request's token names, remembered on ``request.state``
    for ``require_csrf``; None for no token, a blank one, or an unknown or
    revoked one."""
    token = bearer_token(request)
    if not token:
        return None
    device = await device_store().device_for_token(token)
    if device is not None:
        request.state.mobile_device = device
    return device


def authenticated_device(request: Any) -> Optional[Dict[str, Any]]:
    state = getattr(request, "state", None)
    return getattr(state, "mobile_device", None) if state is not None else None
