"""The backend off the LAN (item 7b, 2026-08-19).

The API bound 0.0.0.0:8000 with the dashboard in no-auth mode — open to
the whole LAN (the 08-17 code read's "LAN-open backend = the auth
boundary"). Owner's call: reachable from loopback and his tailnet only.
Since 2026-09-08 the BIND is loopback by default, so this gate is the
second line, not the first: setting ``BACKEND_HOST=0.0.0.0`` is the
opt-in for a tailnet/LAN dashboard (one server, the Tailscale address
included), and this ASGI gate answers 403 to any peer outside
``BACKEND_ALLOWED_NETWORKS`` — default loopback + Tailscale's CGNAT range
(100.64.0.0/10) — and refuses websockets at the handshake. The socket
peer address is the truth (no X-Forwarded-For: there is no proxy here).
A BLANK list = open (documented; the old behavior, explicit) — and that is
the only thing that opens it. A list that fails to parse LOCKS the door
(week 2, task 4): every bad token used to be dropped with a log line
nobody reads, so one typo emptied the list and an empty list meant open.
The setting whose whole job is keeping strangers out failed in their
favour. Now the bad tokens are carried out of the parser in a
``NetworkPolicy`` — boot puts them on the punch list, the gate refuses
every IP peer when nothing parsed, and a bad token never widens the door.
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Tuple

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_NETWORKS = "127.0.0.0/8,::1/128,100.64.0.0/10"

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class NetworkPolicy:
    """What one BACKEND_ALLOWED_NETWORKS spec means.

    ``networks`` — the tokens that parsed; the peers that may reach her.
    ``rejected`` — the tokens that did not, kept so boot can name them.
    ``open``     — DERIVED, never given: True only when nothing parsed and
                   nothing was rejected, i.e. nobody wrote a gate. As a
                   constructor field it was a representable open door built
                   from a failed spec (``NetworkPolicy(rejected=("x",),
                   open=True)``) — exactly the state this whole change
                   exists to make impossible. A spec with rejected tokens
                   and no networks is not open, it is locked:
                   ``is_trusted`` refuses every IP peer.
    """

    networks: Tuple[Network, ...] = ()
    rejected: Tuple[str, ...] = ()

    @property
    def open(self) -> bool:
        return not self.networks and not self.rejected


def parse_networks(spec: str) -> NetworkPolicy:
    networks: list[Network] = []
    rejected: list[str] = []
    for raw in str(spec or "").split(","):
        token = raw.strip()
        # Separators without a token — "", " ", ",,," — are not a gate anybody
        # wrote, so a spec made only of them is blank, and blank is open.
        if not token:
            continue
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            rejected.append(token)
    return NetworkPolicy(networks=tuple(networks), rejected=tuple(rejected))


def is_trusted(host: Any, policy: NetworkPolicy) -> bool:
    """True when the peer may reach the backend.

    A blank spec is open. A peer that is not an IP at all (test
    transports, unix sockets) is not a network peer and passes — an
    attacker cannot forge a non-IP socket address. A spec that was
    written but parsed to nothing is a locked door: no IP peer passes,
    loopback included.
    """
    if policy.open:
        return True
    try:
        ip = ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        return True
    if not policy.networks:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in n for n in policy.networks)


class TrustedNetworksMiddleware:
    """Pure ASGI (HTTP + WebSocket). Denied HTTP → 403 JSON; denied
    WebSocket → close before accept (a 403 handshake)."""

    def __init__(self, app: Callable[..., Awaitable[Any]], *, networks_spec: str = DEFAULT_ALLOWED_NETWORKS) -> None:
        self.app = app
        self.policy = parse_networks(networks_spec)
        # Once, here — not once per request, which would bury the line it is
        # trying to be. Boot's punch list has already refused to start on the
        # cases that matter (core/config_schema.py:_validate_trusted_networks);
        # this is for anyone who builds the middleware some other way.
        if self.policy.rejected:
            logger.warning(
                "trusted_networks: %s in BACKEND_ALLOWED_NETWORKS is not a CIDR; %s",
                ", ".join(repr(token) for token in self.policy.rejected),
                "nothing parsed, so every IP peer is refused"
                if not self.policy.networks
                else "the tokens that parsed are the whole list",
            )

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        client = scope.get("client")
        host = client[0] if isinstance(client, (tuple, list)) and client else None
        if host is None or is_trusted(host, self.policy):
            return await self.app(scope, receive, send)
        logger.warning("trusted_networks: refused %s peer %s %s", scope.get("type"), host, scope.get("path"))
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"detail":"forbidden: this backend answers loopback and tailnet peers only"}'
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
