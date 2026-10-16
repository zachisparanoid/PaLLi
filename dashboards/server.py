"""Local dashboard suite server — private viewing of PaLLi's live stats.

    venv/bin/python dashboards/server.py            # 0.0.0.0:3310

Who may open it (Batch 8, task 8): this machine (127.0.0.0/8, ::1), the
tailnet (100.64.0.0/10) and any extra ranges named in
DASHBOARDS_TRUSTED_NETWORKS — comma-separated CIDRs, read from the
environment, else from the repo's .env.local, e.g.

    DASHBOARDS_TRUSTED_NETWORKS=192.0.2.0/24

The first two are always in: a blank setting adds nothing, and a token that
is not a network is left out (named on stderr at startup) and never widens
the list. Every other peer gets 403 with one fixed line, checked as soon as
the request is parsed — before any handler runs, for every verb — so
nothing of hers is read for it. The socket peer address is the truth.

Read-only by construction: GET only, stats come from collect.collect()
(cached 60 s), what each reply used (/replies, R15) from collect.replies()
(read fresh), static files served from dashboards/static only.
The one piece of her code imported is core/trusted_networks.py (standard
library only; the same check her backend's gate uses). Nothing else of her
runtime is imported, and nothing of hers is touched.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping, Optional

from dotenv import dotenv_values

import collect

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from core.trusted_networks import (  # noqa: E402  (stdlib-only; needs REPO on the path)
    DEFAULT_ALLOWED_NETWORKS,
    NetworkPolicy,
    is_trusted,
    parse_networks,
)

STATIC = HERE / "static"
PORT = 3310
TTL_SECONDS = 60
SETTING = "DASHBOARDS_TRUSTED_NETWORKS"
ENV_FILE = REPO / ".env.local"
FORBIDDEN = b"forbidden: these dashboards answer this machine, the tailnet and the trusted networks only\n"

_cache = {"at": 0.0, "body": b"{}"}
_lock = threading.Lock()

PAGES = {"/": "index.html", "/vitals": "vitals.html", "/backstage": "backstage.html",
         "/observatory": "observatory.html", "/replies": "replies.html"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


def load_policy(env_file: Path = ENV_FILE, environ: Optional[Mapping[str, str]] = None) -> NetworkPolicy:
    """The defaults plus DASHBOARDS_TRUSTED_NETWORKS. The environment wins
    when the name is set there at all (even blank); else .env.local; else
    nothing extra. The defaults are always in the spec, so the policy is
    never open, and a bad extra token lands in ``rejected``, never in
    ``networks``."""
    environ = os.environ if environ is None else environ
    if SETTING in environ:
        extra = environ.get(SETTING)
    elif Path(env_file).is_file():
        extra = dotenv_values(env_file).get(SETTING)
    else:
        extra = None
    return parse_networks(DEFAULT_ALLOWED_NETWORKS + "," + (extra or ""))


# Safe until __main__ reads the setting: this machine and the tailnet only.
# Importing this module reads no file.
POLICY: NetworkPolicy = parse_networks(DEFAULT_ALLOWED_NETWORKS)


def peer_allowed(host: str, policy: Optional[NetworkPolicy] = None) -> bool:
    return is_trusted(host, POLICY if policy is None else policy)


def _stats_json() -> bytes:
    now = time.time()
    with _lock:
        if now - _cache["at"] > TTL_SECONDS:
            try:
                _cache["body"] = json.dumps(collect.collect(), ensure_ascii=False).encode()
                _cache["at"] = now
            except Exception as e:  # keep serving the last good snapshot
                stale = json.loads(_cache["body"] or b"{}")
                stale["errors"] = [f"collect crashed: {e}"]
                _cache["body"] = json.dumps(stale).encode()
                _cache["at"] = now
        return _cache["body"]


def _replies_json() -> bytes:
    """What each reply used (R15, T13), read fresh on every request: the
    record is small, and a reply just made should show. A read that breaks
    is an error line on the page, never a crash."""
    try:
        return json.dumps(collect.replies(), ensure_ascii=False).encode()
    except Exception as e:
        return json.dumps({"rows": [], "errors": [f"replies: {type(e).__name__}"]}).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "PaLLiDash/1"

    def parse_request(self):
        # The peer check, for every verb, before any do_* handler runs: a
        # refused peer gets the fixed line and the connection closes.
        if not super().parse_request():
            return False
        if peer_allowed(self.client_address[0]):
            return True
        self.close_connection = True
        self._send(403, "text/plain; charset=utf-8", FORBIDDEN)
        return False

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/stats":
            return self._send(200, "application/json", _stats_json())
        if path == "/api/replies":
            return self._send(200, "application/json", _replies_json())
        name = PAGES.get(path)
        if name is None and path.startswith("/static/"):
            name = path[len("/static/"):]
        if name is None:
            return self._send(404, "text/plain; charset=utf-8", b"not found")
        target = (STATIC / name).resolve()
        # A path check, not a string prefix (final fix wave, ruling FW-10):
        # "/static/../static_x/f" resolves to a sibling whose name starts
        # with "static".
        if not target.is_relative_to(STATIC) or not target.is_file():
            return self._send(404, "text/plain; charset=utf-8", b"not found")
        ctype = TYPES.get(target.suffix, "application/octet-stream")
        return self._send(200, ctype, target.read_bytes())

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet: only errors to stderr
        if args and str(args[-2] if len(args) > 1 else "").startswith(("4", "5")):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    POLICY = load_policy()
    for token in POLICY.rejected:
        print(f"{SETTING}: {token!r} is not a network; left out", file=sys.stderr)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"PaLLi dashboards on http://0.0.0.0:{PORT} (vitals · backstage · observatory)")
    print("answering " + ", ".join(str(n) for n in POLICY.networks) + "; everyone else gets 403")
    srv.serve_forever()
