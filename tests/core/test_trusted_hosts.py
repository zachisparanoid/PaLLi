"""Names, not just addresses (2026-10-08, the release basics).

The peer gate (core/trusted_networks.py) cannot see DNS rebinding: a web page
on an attacker's name points that name at 127.0.0.1, the browser connects from
loopback, the peer passes, and the browser — now sure her backend IS the
attacker's site — hands that page her CSRF cookie. Verified live on 10-08: GET
/api/web/auth/me addressed to a made-up name got a 200 and a fresh palli_csrf
cookie; in local mode that cookie is all /api/chat/message asks of the owner,
and the owner's turns can reach the shell. The Host gate refuses any request
addressed to a name she does not know, X-Forwarded-Host included — that is
where her own web page's proxy keeps the browser's name.

Names are RFC 2606 examples; `evil.example` stands for the rebinding page and
`builder` for a name an owner might use on their own network."""
from __future__ import annotations

import httpx
import pytest

from core import trusted_hosts as th


def test_bare_host_strips_port_brackets_case_and_trailing_dot():
    assert th.bare_host("127.0.0.1:8000") == "127.0.0.1"
    assert th.bare_host("[::1]:8000") == "::1"
    assert th.bare_host("[::1]") == "::1"
    assert th.bare_host("::1") == "::1"
    assert th.bare_host("LocalHost.:3000") == "localhost"
    assert th.bare_host("  Builder:3000 ") == "builder"
    assert th.bare_host("") == ""


def test_parse_allowed_hosts_reads_names_however_they_are_written():
    assert th.parse_allowed_hosts("") == frozenset()
    assert th.parse_allowed_hosts(" , ,") == frozenset()
    assert th.parse_allowed_hosts("builder, Palli.Example:3000 http://box.example/path") == frozenset(
        {"builder", "palli.example", "box.example"}
    )


def test_addresses_and_localhost_pass_any_other_name_must_be_listed():
    nobody = frozenset()
    for ok in ("127.0.0.1", "127.0.0.1:8000", "localhost", "localhost:3000", "[::1]:8000",
               "100.101.102.103:8000", "192.0.2.28"):
        assert th.is_allowed_name(ok, nobody), ok
    # An address cannot be rebound; a name can, including names that LOOK local.
    for bad in ("evil.example", "evil.example:8000", "localhost.evil.example",
                "127.0.0.1.nip.io", "builder"):
        assert not th.is_allowed_name(bad, nobody), bad
    assert th.is_allowed_name("builder:3000", frozenset({"builder"}))
    assert th.is_allowed_name("BUILDER.", frozenset({"builder"}))


async def _ok_app(scope, receive, send):
    """The smallest thing the gate can stand in front of: always 200."""
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"2")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def _status(mw, headers: dict[str, str]) -> int:
    transport = httpx.ASGITransport(app=mw, client=("127.0.0.1", 51000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/health", headers=headers)
    return response.status_code


@pytest.mark.asyncio
@pytest.mark.parametrize(("headers", "expected"), [
    ({"host": "127.0.0.1:8000"}, 200),
    ({"host": "localhost:8000"}, 200),
    ({"host": "evil.example:8000"}, 403),                                          # rebinding, straight at :8000
    ({"host": "127.0.0.1:8000", "x-forwarded-host": "localhost:3000"}, 200),       # her page's proxy, everyday use
    ({"host": "127.0.0.1:8000", "x-forwarded-host": "evil.example:3000"}, 403),    # rebinding through her page's proxy
    ({"host": "127.0.0.1:8000", "x-forwarded-host": "localhost:3000, evil.example"}, 403),  # any hop
    ({"host": "builder:8000"}, 403),                                               # a name nobody listed
])
async def test_the_gate_reads_host_and_forwarded_host(headers, expected):
    mw = th.TrustedHostsMiddleware(_ok_app, hosts_spec="")
    assert await _status(mw, headers) == expected, headers


@pytest.mark.asyncio
async def test_a_listed_name_passes_both_ways():
    mw = th.TrustedHostsMiddleware(_ok_app, hosts_spec="builder")
    assert await _status(mw, {"host": "builder:8000"}) == 200
    assert await _status(mw, {"host": "127.0.0.1:8000", "x-forwarded-host": "builder:3000"}) == 200
    assert await _status(mw, {"host": "evil.example:8000"}) == 403


@pytest.mark.asyncio
async def test_a_request_with_no_host_and_the_lifespan_pass_through():
    """Browsers always send Host, so a request without one is never a rebinding
    page (an HTTP/1.0 tool, a bare ASGI caller); lifespan is not a request."""
    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    mw = th.TrustedHostsMiddleware(app)
    await mw({"type": "http", "path": "/", "headers": []}, None, None)
    await mw({"type": "lifespan"}, None, None)
    assert seen == ["http", "lifespan"]


@pytest.mark.asyncio
async def test_a_websocket_addressed_to_an_unknown_name_is_refused_at_the_handshake():
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send_):
        raise AssertionError("a refused handshake must never reach her")

    mw = th.TrustedHostsMiddleware(app)
    await mw({"type": "websocket", "path": "/api/chat/ws/stream",
              "headers": [(b"host", b"127.0.0.1:8000"), (b"x-forwarded-host", b"evil.example:3000")]},
             None, send)
    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
async def test_her_backend_refuses_the_request_that_got_a_cookie_on_10_08(monkeypatch):
    """The whole app, not the gate alone: the request that was handed a fresh
    palli_csrf cookie on 10-08 now gets a 403 and no cookie, and the same
    backend still answers when addressed by its address."""
    from core.main import app

    monkeypatch.setattr("core.main.backend_bootstrap.health_status", lambda: "ok")
    monkeypatch.setattr("core.main.backend_bootstrap.snapshot",
                        lambda: {"status": "ok", "counts": {"degraded": 0, "failed": 0}})
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        refused = await client.get("/api/web/auth/me", headers={"host": "rebind-test.example:8000"})
        through_her_page = await client.get(
            "/api/web/auth/me",
            headers={"host": "127.0.0.1:8000", "x-forwarded-host": "rebind-test.example:3000"},
        )
        answered = await client.get("/api/health", headers={"host": "127.0.0.1:8000"})
    assert refused.status_code == 403 and "set-cookie" not in refused.headers
    assert through_her_page.status_code == 403 and "set-cookie" not in through_her_page.headers
    assert answered.status_code == 200


def test_config_knob_default_is_no_extra_names():
    from core.config import config

    assert getattr(config, "BACKEND_ALLOWED_HOSTS", None) == ""
