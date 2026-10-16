from __future__ import annotations

from starlette.requests import Request

from core import rate_limit


def _request_with_client_ip(client_ip: str, x_forwarded_for: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if x_forwarded_for is not None:
        headers.append((b"x-forwarded-for", x_forwarded_for.encode("utf-8")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": (client_ip, 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_trusted_proxy_uses_forwarded_client_ip(monkeypatch):
    request = _request_with_client_ip(
        "10.0.0.10",
        x_forwarded_for="203.0.113.9, 10.0.0.10",
    )
    monkeypatch.setattr(rate_limit.config, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
    monkeypatch.setattr(rate_limit, "get_remote_address", lambda _request: "10.0.0.10")

    assert rate_limit.get_client_ip(request) == "203.0.113.9"


def test_untrusted_peer_ignores_forwarded_header(monkeypatch):
    request = _request_with_client_ip(
        "198.51.100.20",
        x_forwarded_for="203.0.113.9",
    )
    monkeypatch.setattr(rate_limit.config, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
    monkeypatch.setattr(rate_limit, "get_remote_address", lambda _request: "198.51.100.20")

    assert rate_limit.get_client_ip(request) == "198.51.100.20"


def test_trusted_proxy_with_invalid_forwarded_ip_falls_back_to_direct(monkeypatch):
    request = _request_with_client_ip(
        "10.0.0.10",
        x_forwarded_for="definitely-not-an-ip",
    )
    monkeypatch.setattr(rate_limit.config, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
    monkeypatch.setattr(rate_limit, "get_remote_address", lambda _request: "10.0.0.10")

    assert rate_limit.get_client_ip(request) == "10.0.0.10"
