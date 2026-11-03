"""Names, not just addresses (2026-10-08, the release basics).

The peer gate (core/trusted_networks.py) asks WHO is calling — the socket
peer, which a web page cannot forge. It cannot see DNS rebinding: a page on
an attacker's name points that name at 127.0.0.1, the browser connects from
loopback, the peer passes, and the browser — now sure her backend IS the
attacker's site — hands that page the CSRF cookie. Verified 10-08: GET
/api/web/auth/me addressed to ``rebind-test.example:8000`` got a 200 and a
fresh ``palli_csrf`` cookie. In local mode that cookie is all
/api/chat/message asks of the owner, and the owner's turns can reach the
shell. The no-login dashboard's whole defense was the cookie, and the cookie
went to whoever asked by name.

This gate asks WHICH NAME the request was addressed to. A rebinding page
always arrives under its own name, so: an IP literal passes (127.0.0.1, a
tailnet 100.x, [::1] — an address cannot be rebound), ``localhost`` passes,
and any other name passes only when the owner listed it in
``BACKEND_ALLOWED_HOSTS``. Her web page's proxy (Next, ``changeOrigin``)
rewrites Host to its own target and keeps the browser's name in
X-Forwarded-Host, so that header, when present, must pass the same rule —
every hop of it — or the page on :3000 is a way around this gate. A request
with no Host at all passes: browsers always send one, so a missing Host is
never a rebinding page.

Standard library only, like the peer gate: the local dashboards server imports
its neighbour without the web stack, and this file stays importable the same way.
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

LOCALHOST = "localhost"

_REFUSED_BODY = (
    b'{"detail":"forbidden: this backend answers requests addressed to localhost, an IP address, '
    b'or a name in BACKEND_ALLOWED_HOSTS"}'
)


def bare_host(value: str) -> str:
    """The name or address in a Host-style value: no port, no brackets, lower
    case, no trailing dot. ``"[::1]:8000"`` → ``"::1"``, ``"Builder:3000"`` →
    ``"builder"``. An unbracketed value with more than one colon is a bare IPv6
    address and keeps its colons."""
    host = str(value or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        host = host[1:end] if end != -1 else host[1:]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def parse_allowed_hosts(spec: str) -> frozenset[str]:
    """The names an owner listed, however they wrote them: separated by commas
    or spaces, with or without a scheme, a port or a path. Blank → no extra
    names (never "every name": the only open door here would be a bug)."""
    names: set[str] = set()
    for raw in str(spec or "").replace(",", " ").split():
        token = raw.strip()
        if "://" in token:
            token = token.split("://", 1)[1]
        token = token.split("/", 1)[0]
        host = bare_host(token)
        if host:
            names.add(host)
    return frozenset(names)


def is_allowed_name(value: str, allowed: frozenset[str]) -> bool:
    """True when a request addressed this way may reach her: an IP address,
    ``localhost``, or a listed name."""
    host = bare_host(value)
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return host == LOCALHOST or host in allowed


def _names_in(headers: Iterable[tuple[bytes, bytes]]) -> list[str]:
    """Every name the request carries: Host, then each hop of X-Forwarded-Host.
    Empty values are not names (Next sends an empty X-Forwarded-Host when the
    browser sent no Host)."""
    names: list[str] = []
    for key, value in headers:
        k = key.decode("latin-1").lower()
        if k not in ("host", "x-forwarded-host"):
            continue
        for part in value.decode("latin-1").split(","):
            if part.strip():
                names.append(part.strip())
    return names


class TrustedHostsMiddleware:
    """Pure ASGI (HTTP + WebSocket). A request addressed to a name she does not
    know → 403 JSON; a WebSocket → close before accept (a 403 handshake), the
    same way the peer gate refuses."""

    def __init__(self, app: Callable[..., Awaitable[Any]], *, hosts_spec: str = "") -> None:
        self.app = app
        self.allowed = parse_allowed_hosts(hosts_spec)

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        refused = [n for n in _names_in(scope.get("headers") or ()) if not is_allowed_name(n, self.allowed)]
        if not refused:
            return await self.app(scope, receive, send)
        logger.warning(
            "trusted_hosts: refused %s %s addressed to %s (not localhost, an IP address, or a name in "
            "BACKEND_ALLOWED_HOSTS)",
            scope.get("type"), scope.get("path"), ", ".join(repr(n) for n in refused),
        )
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(_REFUSED_BODY)).encode())]})
        await send({"type": "http.response.body", "body": _REFUSED_BODY})
