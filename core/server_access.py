"""Authentication boundary for the single-owner server deployment.

The frontend proxies to a loopback backend. Network membership alone therefore
cannot authenticate dashboard users. A server key exchanges for the existing
HttpOnly session cookie; this gate also protects API routes without their own
dashboard dependency. Paired mobile clients keep their existing credentials.
"""
from __future__ import annotations

import ipaddress
import hmac


def server_access_enabled() -> bool:
    from core.config import config

    return bool(str(getattr(config, "DASHBOARD_ACCESS_TOKEN", "") or "").strip())


def is_loopback(host: str) -> bool:
    if str(host).lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(str(host).strip("[]"))
        mapped = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or bool(mapped and mapped.is_loopback)
    except ValueError:
        return False


def server_key_authenticated(request) -> bool:
    """The local CLI can use its configured key without a browser session."""
    from core.config import config

    peer = request.client.host if request.client else ""
    supplied = request.headers.get("x-palli-server-key", "")
    expected = str(getattr(config, "DASHBOARD_ACCESS_TOKEN", "") or "")
    return bool(expected and supplied and is_loopback(peer)) and hmac.compare_digest(
        supplied.encode(), expected.encode(),
    )


class ServerAccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in {"http", "websocket"} or not server_access_enabled():
            return await self.app(scope, receive, send)

        from starlette.requests import HTTPConnection
        from starlette.responses import JSONResponse
        from core.mobile_auth import bearer_token, device_for_request
        from core.web_security import get_user_id_from_request

        path = scope.get("path", "")
        method = scope.get("method", "")
        request = HTTPConnection(scope)
        public = {
            ("GET", "/api/web/auth/me"),
            ("POST", "/api/web/auth/server-login"),
            # Redemption requires the existing short-lived, single-use pair code.
            # Creating a pair code is deliberately NOT public.
            ("POST", "/api/mobile/pair"),
        }
        peer = scope.get("client") or ("", 0)
        local_probe = method == "GET" and path == "/api/health" and is_loopback(peer[0])
        if ((method, path) in public and not request.headers.get("x-palli-server-key")) or local_probe:
            return await self.app(scope, receive, send)

        if bearer_token(request) is not None:
            authenticated = await device_for_request(request) is not None
        elif request.headers.get("x-palli-server-key") is not None:
            authenticated = server_key_authenticated(request)
            if authenticated:
                from core.local_mode import ensure_local_owner

                owner = await ensure_local_owner()
                request.state.server_owner_id = int(owner["id"])
                request.state.server_key_authenticated = True
        else:
            authenticated = get_user_id_from_request(request) is not None
        if authenticated:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            {"detail": "Connect with your server access key first."},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
