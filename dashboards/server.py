"""Read-only statistics suite, network-wide with the launcher's access key.

Run normal prepare/start first, then `venv/bin/python dashboards/server.py`.
Open port 3310 and sign in as palli with the server access key. DASHBOARDS_HOST
or FRONTEND_HOST selects the bind, DASHBOARDS_PORT changes the port, and
DASHBOARDS_TRUSTED_NETWORKS optionally restricts authenticated peer addresses.
The collector reads data without importing or starting the companion runtime.
"""
from __future__ import annotations

import json
import base64
import hmac
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
from core.server_access import is_loopback
from core.trusted_networks import (  # noqa: E402  (stdlib-only; needs REPO on the path)
    DEFAULT_ALLOWED_NETWORKS,
    NetworkPolicy,
    is_trusted,
    parse_networks,
)

STATIC = HERE / "static"
PORT = 3310
BIND_HOST = "0.0.0.0"
ACCESS_KEY = ""
TTL_SECONDS = 60
SETTING = "DASHBOARDS_TRUSTED_NETWORKS"
ENV_FILE = REPO / ".env.local"
FORBIDDEN = b"forbidden: this address is outside DASHBOARDS_TRUSTED_NETWORKS\n"

_cache = {"at": 0.0, "body": b"{}"}
_lock = threading.Lock()

PAGES = {"/": "index.html", "/vitals": "vitals.html", "/backstage": "backstage.html",
         "/observatory": "observatory.html", "/replies": "replies.html"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


def load_policy(env_file: Path = ENV_FILE, environ: Optional[Mapping[str, str]] = None) -> NetworkPolicy:
    """Optional explicit CIDR restriction; blank means key-authenticated network access."""
    environ = os.environ if environ is None else environ
    if SETTING in environ:
        extra = environ.get(SETTING)
    elif Path(env_file).is_file():
        extra = dotenv_values(env_file).get(SETTING)
    else:
        extra = None
    return parse_networks(extra or "")


# Access still requires the server key; importing this module reads no file.
POLICY: NetworkPolicy = parse_networks("")


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
        if not peer_allowed(self.client_address[0]):
            self.close_connection = True
            self._send(403, "text/plain; charset=utf-8", FORBIDDEN)
            return False
        if ACCESS_KEY:
            try:
                scheme, _, encoded = self.headers.get("Authorization", "").partition(" ")
                credentials = base64.b64decode(encoded, validate=True).decode() if scheme.lower() == "basic" else ""
                user, _, key = credentials.partition(":")
                if user == "palli" and hmac.compare_digest(key.encode(), ACCESS_KEY.encode()):
                    return True
            except (ValueError, UnicodeError):
                pass
            self.close_connection = True
            self._send(401, "text/plain; charset=utf-8", b"Sign in as palli with your server access key.\n",
                       {"WWW-Authenticate": 'Basic realm="PaLLi dashboards", charset="UTF-8"'})
            return False
        if is_loopback(BIND_HOST):
            return True
        self.close_connection = True
        self._send(503, "text/plain; charset=utf-8", b"Run palli_ctl prepare/start to initialize the server access key.\n")
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

    def _send(self, code: int, ctype: str, body: bytes, headers: Optional[Mapping[str, str]] = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet: only errors to stderr
        if args and str(args[-2] if len(args) > 1 else "").startswith(("4", "5")):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    settings = dict(dotenv_values(REPO / ".env"))
    settings.update(dotenv_values(ENV_FILE))
    settings.update(os.environ)
    ACCESS_KEY = str(settings.get("DASHBOARD_ACCESS_TOKEN") or "").strip()
    BIND_HOST = str(settings.get("DASHBOARDS_HOST") or settings.get("FRONTEND_HOST") or "0.0.0.0")
    PORT = int(settings.get("DASHBOARDS_PORT") or 3310)
    if not ACCESS_KEY and not is_loopback(BIND_HOST):
        raise SystemExit("Run palli_ctl prepare/start first to generate the server access key.")
    POLICY = load_policy()
    for token in POLICY.rejected:
        print(f"{SETTING}: {token!r} is not a network; left out", file=sys.stderr)
    srv = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    print(f"PaLLi dashboards on http://{BIND_HOST}:{PORT} (vitals · backstage · observatory)")
    print("Sign in as palli with the main launcher's server access key." if ACCESS_KEY else "Local-only dashboard.")
    if POLICY.networks or POLICY.rejected:
        print("Allowed peers: " + ", ".join(str(n) for n in POLICY.networks))
    srv.serve_forever()
