"""Batch 8, task 8 (6.6, ruling T8-1): the dashboards are private.

``dashboards/server.py`` listened on every address with no gate at all, and
its pages quote her private reads. Now it answers only this machine
(127.0.0.0/8, ::1), the tailnet (100.64.0.0/10) and any extra ranges in
``DASHBOARDS_TRUSTED_NETWORKS`` (the environment first, else the repo's
``.env.local``) — the same check her backend uses, ``core.trusted_networks``.
Any other peer gets 403 with a fixed body before a handler looks at the
request, so nothing of hers is read for it.

A real server on ``127.0.0.1:0`` in a thread. From loopback the 403 can only
be shown with a policy that leaves loopback out. The 200 case fetches a
static page, never ``/api/stats`` (that one reads her database). Every
``.env.local`` here is a tmp file; the repo's own is never read.
"""
from __future__ import annotations

import http.client
import importlib.util
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.trusted_networks import DEFAULT_ALLOWED_NETWORKS, parse_networks

REPO = Path(__file__).resolve().parents[2]
DASH = REPO / "dashboards"
SETTING = "DASHBOARDS_TRUSTED_NETWORKS"


@pytest.fixture(scope="module")
def server():
    """dashboards/server.py, loaded from its file the way the command runs it
    (its ``import collect`` needs dashboards/ on the path)."""
    added = str(DASH) not in sys.path
    if added:
        sys.path.insert(0, str(DASH))
    try:
        spec = importlib.util.spec_from_file_location("palli_dashboards_server", DASH / "server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if added:
            sys.path.remove(str(DASH))
    return mod


@contextmanager
def _serving(mod):
    srv = mod.ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)


def _fetch(port: int, path: str, method: str = "GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


@pytest.fixture
def her_data_untouched(server, monkeypatch):
    """Any read of her stats or her pages fails the test loudly."""
    calls: list[str] = []

    def _collect():
        calls.append("collect")
        raise AssertionError("the stats collector ran for a refused peer")

    monkeypatch.setattr(server.collect, "collect", _collect)
    monkeypatch.setattr(server, "_stats_json", lambda: calls.append("stats") or b"{}")
    return calls


# ── over a real socket ─────────────────────────────────────────────────────

def test_this_machine_opens_a_page(server, monkeypatch):
    monkeypatch.setattr(server, "POLICY", parse_networks(DEFAULT_ALLOWED_NETWORKS))
    with _serving(server) as port:
        status, body = _fetch(port, "/vitals")
    assert status == 200
    assert body == (DASH / "static" / "vitals.html").read_bytes()


def test_a_peer_outside_the_list_gets_403_and_nothing_of_hers(server, monkeypatch, her_data_untouched):
    # only the tailnet: the loopback test client is now an outsider
    monkeypatch.setattr(server, "POLICY", parse_networks("100.64.0.0/10"))
    with _serving(server) as port:
        answers = [_fetch(port, path) for path in ("/api/stats", "/vitals", "/", "/static/common.js", "/nope")]
        posted = _fetch(port, "/api/stats", method="POST")
        head = _fetch(port, "/vitals", method="HEAD")
    for status, body in answers + [posted]:
        assert status == 403
        assert body == server.FORBIDDEN          # one fixed line, the same for every path and verb
    assert head == (403, b"")
    assert her_data_untouched == []
    assert b"<html" not in server.FORBIDDEN.lower() and b"{" not in server.FORBIDDEN


# ── who counts as trusted ──────────────────────────────────────────────────

def test_an_ipv4_mapped_loopback_peer_is_this_machine(server):
    defaults = parse_networks(DEFAULT_ALLOWED_NETWORKS)
    assert server.peer_allowed("::ffff:127.0.0.1", defaults)
    assert server.peer_allowed("::1", defaults)
    assert server.peer_allowed("100.101.102.103", defaults)
    assert not server.peer_allowed("::ffff:10.0.0.5", defaults)
    assert not server.peer_allowed("10.0.0.5", defaults)


def test_no_setting_means_this_machine_and_the_tailnet_only(server, tmp_path):
    policy = server.load_policy(env_file=tmp_path / "missing.env", environ={})
    assert set(policy.networks) == set(parse_networks(DEFAULT_ALLOWED_NETWORKS).networks)
    assert not policy.open
    assert server.peer_allowed("127.0.0.1", policy)
    assert not server.peer_allowed("10.0.0.5", policy)
    assert not server.peer_allowed("172.16.0.5", policy)


def test_a_blank_setting_adds_nothing(server, tmp_path):
    env = tmp_path / ".env.local"
    env.write_text(f"{SETTING}=\n")
    policy = server.load_policy(env_file=env, environ={})
    assert set(policy.networks) == set(parse_networks(DEFAULT_ALLOWED_NETWORKS).networks)
    assert not policy.open
    assert not server.peer_allowed("10.0.0.5", policy)


def test_the_lan_range_in_env_local_is_let_in(server, tmp_path):
    env = tmp_path / ".env.local"
    env.write_text(f"OTHER=1\n{SETTING}=10.0.0.0/24\n")
    policy = server.load_policy(env_file=env, environ={})
    assert server.peer_allowed("10.0.0.5", policy)
    assert server.peer_allowed("127.0.0.1", policy)          # the defaults stay in
    assert server.peer_allowed("100.101.102.103", policy)
    assert not server.peer_allowed("172.16.0.5", policy)


def test_a_bad_token_never_widens_the_list(server, tmp_path):
    env = tmp_path / ".env.local"
    env.write_text(f"{SETTING}=not-a-network, 10.0.0.0/33 ,*\n")
    policy = server.load_policy(env_file=env, environ={})
    assert set(policy.networks) == set(parse_networks(DEFAULT_ALLOWED_NETWORKS).networks)
    assert set(policy.rejected) == {"not-a-network", "10.0.0.0/33", "*"}
    assert not policy.open                                   # rejected tokens never open the door
    assert server.peer_allowed("127.0.0.1", policy)
    assert not server.peer_allowed("10.0.0.5", policy)
    assert not server.peer_allowed("172.16.0.5", policy)


def test_the_environment_overrides_env_local(server, tmp_path):
    env = tmp_path / ".env.local"
    env.write_text(f"{SETTING}=10.0.0.0/24\n")
    policy = server.load_policy(env_file=env, environ={SETTING: "172.16.0.0/24"})
    assert server.peer_allowed("172.16.0.5", policy)
    assert not server.peer_allowed("10.0.0.5", policy)
    # set but empty in the environment still wins: defaults only
    policy = server.load_policy(env_file=env, environ={SETTING: ""})
    assert not server.peer_allowed("10.0.0.5", policy)
    assert server.peer_allowed("127.0.0.1", policy)


def test_importing_the_server_reads_no_settings_and_trusts_only_the_defaults(server):
    assert set(server.POLICY.networks) == set(parse_networks(DEFAULT_ALLOWED_NETWORKS).networks)


def test_the_only_piece_of_her_runtime_imported_is_the_network_check(tmp_path):
    probe = (
        "import sys, runpy\n"
        f"sys.path.insert(0, {str(DASH)!r})\n"
        f"runpy.run_path({str(DASH / 'server.py')!r}, run_name='not_main')\n"
        "theirs = {'core', 'ai', 'api', 'bots', 'voice', 'palli_config', 'identity', 'turn', 'reminders'}\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in theirs))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "['core', 'core.trusted_networks']"


def test_startup_reads_the_setting_names_a_bad_token_and_says_what_it_answers(tmp_path):
    # __main__ for real, with the server class swapped for one that never
    # binds: no port opens and nothing of hers is read.
    probe = (
        "import sys, runpy, http.server\n"
        "class NoBind:\n"
        "    def __init__(self, addr, handler): self.addr = addr\n"
        "    def serve_forever(self): print('bound', self.addr)\n"
        "http.server.ThreadingHTTPServer = NoBind\n"
        f"sys.path.insert(0, {str(DASH)!r})\n"
        f"g = runpy.run_path({str(DASH / 'server.py')!r}, run_name='__main__')\n"
        "print('policy', [str(n) for n in g['POLICY'].networks])\n"
    )
    env = {"PATH": "/usr/bin:/bin", SETTING: "10.0.0.0/24, bogus"}
    out = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "DASHBOARDS_TRUSTED_NETWORKS: 'bogus' is not a network; left out" in out.stderr
    assert "answering 127.0.0.0/8, ::1/128, 100.64.0.0/10, 10.0.0.0/24; everyone else gets 403" in out.stdout
    assert "policy ['127.0.0.0/8', '::1/128', '100.64.0.0/10', '10.0.0.0/24']" in out.stdout
    assert "bound ('0.0.0.0', 3310)" in out.stdout


def test_the_env_template_shows_a_stranger_how_to_let_their_network_in(server):
    """Final fix wave (ruling FW-12): without this line a stranger can't
    find the setting, and the dashboards answer only this machine and the
    tailnet. Commented (nothing changes until they set it), and the example
    is the documentation range, never a real home network."""
    template = (REPO / ".env.template").read_text(encoding="utf-8")
    assert f"# {SETTING}=192.0.2.0/24" in template.splitlines()
    assert f"\n{SETTING}=" not in template                    # never set by the template
    policy = server.load_policy(env_file=REPO / ".env.template", environ={})
    assert set(policy.networks) == set(parse_networks(DEFAULT_ALLOWED_NETWORKS).networks)
