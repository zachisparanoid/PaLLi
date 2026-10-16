"""The backend off the LAN (item 7b, 2026-08-19, owner's call: loopback +
tailnet only). The API bound 0.0.0.0:8000 with the dashboard in no-auth
mode, open to the whole LAN. A trusted-networks gate at the ASGI layer
now answers 403 to any client outside BACKEND_ALLOWED_NETWORKS (default:
loopback + Tailscale's CGNAT range); websockets are refused at the
handshake. Since 2026-09-08 the bind itself defaults to loopback, so this
gate is the second line: BACKEND_HOST=0.0.0.0 is the opt-in that makes the
tailnet address work, and these CIDRs are what it is still gated by.

The addresses below are examples: RFC 5737 TEST-NET-1 (192.0.2.0/24) for the
house LAN and TEST-NET-2 (198.51.100.0/24) for "some other subnet". The
tailnet one is NOT a TEST-NET address on purpose — it has to sit inside
Tailscale's CGNAT block 100.64.0.0/10 or the assertion it makes is vacuous."""
from __future__ import annotations

import logging

import httpx
import pytest

from core import trusted_networks as tn


def test_is_trusted_defaults():
    nets = tn.parse_networks(tn.DEFAULT_ALLOWED_NETWORKS)
    assert tn.is_trusted("127.0.0.1", nets)
    assert tn.is_trusted("::1", nets)
    assert tn.is_trusted("100.101.102.103", nets)      # a tailnet peer (CGNAT)
    assert tn.is_trusted("100.64.0.1", nets)
    assert not tn.is_trusted("192.0.2.240", nets)      # the house LAN
    assert not tn.is_trusted("10.0.0.5", nets)
    assert not tn.is_trusted("8.8.8.8", nets)
    assert tn.is_trusted("testclient", nets)           # not an IP (test transports) — not a network peer


def test_open_and_custom_lists():
    blank = tn.parse_networks("")
    # Empty = open (documented), and the ONLY thing that is open.
    assert blank.open and blank.networks == () and blank.rejected == ()
    assert tn.is_trusted("192.0.2.9", blank)
    # A spec with separators but no tokens is blank too: nobody wrote a
    # network, so nobody chose a gate (week 2, task 4, review fix).
    assert tn.parse_networks(" , ,").open is True
    nets = tn.parse_networks("192.0.2.0/24, 127.0.0.1/32")
    assert tn.is_trusted("192.0.2.42", nets) and not tn.is_trusted("198.51.100.1", nets)
    # A bad token no longer vanishes: the good ones are the list, the bad one
    # is reported (week 2, task 4 — this assertion used to be an equality
    # between two lists, which could not see the difference).
    partial = tn.parse_networks("garbage,, 10.0.0.0/8")
    assert partial.networks == tn.parse_networks("10.0.0.0/8").networks
    assert partial.rejected == ("garbage",)
    assert not partial.open


@pytest.mark.asyncio
async def test_lan_client_gets_403_and_loopback_and_tailnet_pass(monkeypatch):
    from core.main import app
    monkeypatch.setattr("core.main.backend_bootstrap.health_status", lambda: "ok")
    monkeypatch.setattr("core.main.backend_bootstrap.snapshot",
                        lambda: {"status": "ok", "counts": {"degraded": 0, "failed": 0}})
    for host, expected in (("192.0.2.240", 403), ("127.0.0.1", 200), ("100.101.102.103", 200)):
        transport = httpx.ASGITransport(app=app, client=(host, 51000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            r = await c.get("/api/health")
        assert r.status_code == expected, (host, r.status_code)
    assert r.status_code == 200


def test_config_knob_default_is_loopback_plus_tailnet():
    from core.config import Config
    assert getattr(Config, "BACKEND_ALLOWED_NETWORKS", None) == tn.DEFAULT_ALLOWED_NETWORKS or \
        getattr(__import__("core.config", fromlist=["config"]).config, "BACKEND_ALLOWED_NETWORKS") == tn.DEFAULT_ALLOWED_NETWORKS


async def _ok_app(scope, receive, send):
    """The smallest thing the gate can stand in front of: always 200."""
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"2")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def _status(app, host: str) -> int:
    transport = httpx.ASGITransport(app=app, client=(host, 51000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/health")
    return response.status_code


def test_a_list_that_parses_to_nothing_locks_the_door():
    """Week 2 task 4: a typo in BACKEND_ALLOWED_NETWORKS used to OPEN the
    backend. Every token was dropped with a log line nobody reads, the list
    came out empty, and an empty list means open — the one setting whose whole
    job is to keep strangers out failed in their favour. A non-blank list that
    parses to nothing is now a LOCKED door: no IP peer passes, loopback
    included, and the bad token leaves the parser instead of vanishing into a
    log, so boot can name it."""
    policy = tn.parse_networks("nonsense")

    assert not policy.open
    assert policy.networks == ()
    assert policy.rejected == ("nonsense",)
    assert not tn.is_trusted("192.0.2.9", policy)       # the house LAN
    assert not tn.is_trusted("127.0.0.1", policy)       # locked means everyone
    assert not tn.is_trusted("100.101.102.103", policy)  # the tailnet too
    # Still not a network peer: test transports and unix sockets are not
    # addresses an attacker can forge (test_is_trusted_defaults pins the rule).
    assert tn.is_trusted("testclient", policy)


@pytest.mark.asyncio
async def test_the_middleware_refuses_even_loopback_when_the_list_is_broken(caplog):
    """The gate itself, not just the parser: a 403 off a real ASGI round-trip.
    And the rejected token is named ONCE, when the middleware is built — not
    on every request, which would bury the log it is trying to be."""
    with caplog.at_level(logging.WARNING, logger="core.trusted_networks"):
        gated = tn.TrustedNetworksMiddleware(_ok_app, networks_spec="nonsense")
        assert await _status(gated, "127.0.0.1") == 403
        assert await _status(gated, "192.0.2.240") == 403

    named = [r for r in caplog.records if "nonsense" in r.getMessage()]
    assert len(named) == 1, [r.getMessage() for r in caplog.records]

    # The 403s above are the list, not the app: the shipped default still
    # lets loopback through the same stack.
    allowed = tn.TrustedNetworksMiddleware(_ok_app, networks_spec=tn.DEFAULT_ALLOWED_NETWORKS)
    assert await _status(allowed, "127.0.0.1") == 200
    assert await _status(allowed, "192.0.2.240") == 403


@pytest.mark.asyncio
async def test_a_blank_list_is_still_an_open_door_on_purpose():
    """The documented open case survives the fix. "" is a choice someone can
    make; a typo is not, and only the typo locks."""
    assert tn.parse_networks("").open
    assert tn.is_trusted("192.0.2.9", tn.parse_networks(""))

    wide_open = tn.TrustedNetworksMiddleware(_ok_app, networks_spec="")
    assert await _status(wide_open, "192.0.2.240") == 200
    assert await _status(wide_open, "127.0.0.1") == 200
