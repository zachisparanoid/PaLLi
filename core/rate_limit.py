"""
Shared SlowAPI rate limiter for the FastAPI backend.

Keep this in a standalone module so API routers can import `limiter` without
creating circular imports with `core/main.py`.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from core.config import config


def _first_forwarded_ip(request: Request) -> str | None:
    """
    Return the first X-Forwarded-For IP when present and syntactically valid.

    Header format may be a comma-separated chain: client, proxy1, proxy2.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if not forwarded_for:
        return None

    candidate = forwarded_for.split(",")[0].strip()
    if not candidate:
        return None

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def get_client_ip(request: Request) -> str:
    """
    Extract client IP address with trusted-proxy semantics.

    - By default, use the direct socket peer IP.
    - Only honor X-Forwarded-For when the direct peer is a trusted proxy.
    """
    direct_ip = get_remote_address(request)
    if not direct_ip:
        direct_ip = request.client.host if request.client else "unknown"

    trusted_proxies = set(getattr(config, "TRUSTED_PROXY_IPS", []) or [])
    if direct_ip in trusted_proxies:
        forwarded_ip = _first_forwarded_ip(request)
        if forwarded_ip:
            return forwarded_ip

    return direct_ip


# Create rate limiter with IP-based key.
limiter = Limiter(key_func=get_client_ip)
