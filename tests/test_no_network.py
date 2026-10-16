"""The suite can't reach the network (fix plan 6.7, Batch 7 task 1).

The guard lives in tests/conftest.py, installed at import: connect/connect_ex
on every AF_INET/AF_INET6 socket refuse an address that is not this machine,
socket.getaddrinfo refuses a name that is not numeric or localhost, and every
refusal is recorded against its test and fails the session at the end.
PALLI_TESTS_ALLOW_NETWORK=1 turns it off (the probe battery's own run).

Every test here that trips the guard on purpose asks for the ``network_guard``
fixture, which removes that test's own records at teardown, so the guard's
tests never fail the session they run in.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# With the opt-out set for the whole run, these would really connect.
_armed = pytest.mark.skipif(
    os.environ.get("PALLI_TESTS_ALLOW_NETWORK", "").strip() == "1",
    reason="PALLI_TESTS_ALLOW_NETWORK=1: the guard is off for this run",
)


@_armed
def test_a_test_cannot_open_an_internet_socket(network_guard):
    with pytest.raises(RuntimeError, match="network"):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


def test_localhost_still_works_for_local_test_servers():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    c = socket.create_connection(("127.0.0.1", port), timeout=1)
    c.close(); srv.close()


def test_localhost_by_name_and_ipv6_loopback_still_work():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    c = socket.create_connection(("localhost", port), timeout=1)
    c.close(); srv.close()
    if not socket.has_ipv6:
        return
    try:
        srv6 = socket.socket(socket.AF_INET6); srv6.bind(("::1", 0)); srv6.listen(1)
    except OSError:
        return  # this box has no IPv6 loopback; the address check below still runs
    port6 = srv6.getsockname()[1]
    c6 = socket.socket(socket.AF_INET6); c6.settimeout(1); c6.connect(("::1", port6))
    c6.close(); srv6.close()


def test_unix_sockets_pass_the_guard(tmp_path):
    path = str(tmp_path / "s.sock")
    srv = socket.socket(socket.AF_UNIX); srv.bind(path); srv.listen(1)
    c = socket.socket(socket.AF_UNIX); c.settimeout(1)
    assert c.connect_ex(path) == 0
    c.close(); srv.close()


@_armed
def test_a_dns_lookup_of_an_outside_name_is_refused(network_guard):
    with pytest.raises(socket.gaierror, match="network"):
        socket.getaddrinfo("api.deepseek.com", 443)
    # localhost and numeric addresses never leave the machine, so they still resolve.
    assert socket.getaddrinfo("localhost", 80)
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("93.184.216.34", 80)   # numeric: no lookup; connect is what refuses it
    assert [a.address for a in network_guard.attempts] == ["api.deepseek.com:443"]


@_armed
def test_asyncio_open_connection_surfaces_the_guard(network_guard):
    async def reach_out():
        await asyncio.open_connection("93.184.216.34", 80)

    with pytest.raises(RuntimeError, match="network"):
        asyncio.run(reach_out())
    with pytest.raises(network_guard.Blocked):
        asyncio.run(reach_out())


@_armed
def test_the_refusal_is_a_connection_error_so_clients_take_their_offline_path(network_guard):
    with pytest.raises(ConnectionRefusedError, match="network access in tests: 93.184.216.34:443"):
        socket.create_connection(("93.184.216.34", 443), timeout=1)
    s = socket.socket()
    try:
        with pytest.raises(network_guard.Blocked):
            s.connect_ex(("93.184.216.34", 443))
    finally:
        s.close()


@_armed
def test_a_blocked_attempt_is_recorded_against_its_test(network_guard, request):
    with pytest.raises(network_guard.Blocked):
        socket.create_connection(("93.184.216.34", 443), timeout=1)
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("huggingface.co", 443)
    recorded = [(a.kind, a.address) for a in network_guard.attempts]
    assert recorded == [("connect", "93.184.216.34:443"), ("dns", "huggingface.co:443")]
    assert all(a.test == f"{request.node.nodeid} (call)" for a in network_guard.attempts)
    network_guard.release()
    assert network_guard.attempts == []


@pytest.mark.parametrize(
    "family, address",
    [
        (socket.AF_INET, ("127.0.0.1", 80)),
        (socket.AF_INET, ("127.9.9.9", 80)),
        (socket.AF_INET, ("127.1", 80)),            # legacy spellings, resolved like inet_aton
        (socket.AF_INET, ("2130706433", 80)),
        (socket.AF_INET, ("localhost", 80)),
        (socket.AF_INET, ("LOCALHOST", 80)),
        (socket.AF_INET, ("api.localhost", 80)),
        (socket.AF_INET, ("0.0.0.0", 80)),
        (socket.AF_INET6, ("::1", 80, 0, 0)),
        (socket.AF_INET6, ("::1%lo", 80, 0, 1)),
        (socket.AF_INET6, ("::ffff:127.0.0.1", 80, 0, 0)),
        (socket.AF_UNIX, "/tmp/palli.sock"),
        (socket.AF_UNIX, b"\0abstract"),
    ],
    ids=lambda v: repr(v) if not isinstance(v, int) else socket.AddressFamily(v).name,
)
def test_loopback_and_unix_addresses_are_never_blocked(network_guard, monkeypatch, family, address):
    monkeypatch.delenv("PALLI_TESTS_ALLOW_NETWORK", raising=False)
    assert network_guard.blocked_address(family, address) is None


@pytest.mark.parametrize(
    "family, address, shown",
    [
        (socket.AF_INET, ("93.184.216.34", 80), "93.184.216.34:80"),
        (socket.AF_INET, ("172.16.0.20", 11434), "172.16.0.20:11434"),   # the LAN is the network
        (socket.AF_INET, ("10.0.0.5", 22), "10.0.0.5:22"),
        (socket.AF_INET, ("api.deepseek.com", 443), "api.deepseek.com:443"),
        (socket.AF_INET, ("localhost.example.com", 80), "localhost.example.com:80"),
        (socket.AF_INET6, ("2606:4700::1", 443, 0, 0), "[2606:4700::1]:443"),
        (socket.AF_INET6, ("::ffff:93.184.216.34", 443, 0, 0), "[::ffff:93.184.216.34]:443"),
    ],
    ids=lambda v: repr(v) if not isinstance(v, int) else socket.AddressFamily(v).name,
)
def test_off_box_addresses_are_blocked(network_guard, monkeypatch, family, address, shown):
    monkeypatch.delenv("PALLI_TESTS_ALLOW_NETWORK", raising=False)
    assert network_guard.blocked_address(family, address) == shown


def test_the_opt_out_restores_real_behaviour(network_guard, monkeypatch):
    # The check functions only: nothing here opens a socket or looks a name up.
    monkeypatch.delenv("PALLI_TESTS_ALLOW_NETWORK", raising=False)
    assert network_guard.blocked_address(socket.AF_INET, ("93.184.216.34", 80)) == "93.184.216.34:80"
    assert network_guard.blocked_lookup("api.deepseek.com") == "api.deepseek.com"
    monkeypatch.setenv("PALLI_TESTS_ALLOW_NETWORK", "1")
    assert network_guard.blocked_address(socket.AF_INET, ("93.184.216.34", 80)) is None
    assert network_guard.blocked_lookup("api.deepseek.com") is None
    assert network_guard.attempts == []


def test_the_probe_battery_opts_its_own_run_out():
    source = (REPO / "scripts" / "probe_battery.py").read_text(encoding="utf-8")
    assert 'env["PALLI_TESTS_ALLOW_NETWORK"] = "1"' in source


@_armed
def test_a_recorded_attempt_fails_the_session_even_when_its_test_passes(tmp_path):
    # PaLLi degrades through `except Exception` almost everywhere, so a blocked
    # call rarely fails its own test: the session's exit status is what does.
    # A child pytest loads this conftest as a plugin and runs one such test;
    # 192.0.2.1 is TEST-NET-1, which no machine answers even if the guard broke.
    probe = tmp_path / "test_swallows_a_blocked_call.py"
    probe.write_text(textwrap.dedent("""
        import socket

        def test_swallows():
            try:
                socket.create_connection(("192.0.2.1", 80), timeout=1)
            except Exception:
                pass
    """), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PALLI_TESTS_ALLOW_NETWORK", "PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS")}
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "tests"), str(REPO)])
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest",
         "--rootdir", str(tmp_path), str(probe)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=300,
    )
    out = proc.stdout + proc.stderr
    assert "1 passed" in out, out
    assert "network guard" in out and "192.0.2.1:80" in out and "test_swallows" in out, out
    assert proc.returncode == 1, out
