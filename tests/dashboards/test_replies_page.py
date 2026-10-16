"""What a reply used, on the dashboards (R15, memory that reaches, T13).

``/replies`` is one static page; ``/api/replies`` is the record read fresh
(``collect.replies``: the same day files ``core.reply_sources`` writes, read
with the standard library only). Both sit behind the dashboards' one access
policy: a peer outside it gets the same 403 as every other page, before
anything of hers is read.

A real server on ``127.0.0.1:0`` in a thread (the ``tests/dashboards``
pattern). Every record here is a tmp folder (``PALLI_REPLY_SOURCES_DIR``);
the live one is never read. Stand-in names only.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core import reply_sources as rs
from core.trusted_networks import DEFAULT_ALLOWED_NETWORKS, parse_networks

REPO = Path(__file__).resolve().parents[2]
DASH = REPO / "dashboards"


@pytest.fixture(scope="module")
def server():
    added = str(DASH) not in sys.path
    if added:
        sys.path.insert(0, str(DASH))
    try:
        spec = importlib.util.spec_from_file_location("palli_dashboards_server_replies", DASH / "server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if added:
            sys.path.remove(str(DASH))
    return mod


@pytest.fixture
def folder(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "reply_sources"
    monkeypatch.setenv("PALLI_REPLY_SOURCES_DIR", str(path))
    return path


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
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _seed(now: datetime) -> None:
    for back, reply in ((3, "three days ago"), (1, "yesterday"), (0, "today")):
        assert rs.record(
            door="discord_text", conversation="discord:111", who_name="Robin", owner=True,
            reply_excerpt=reply, record_lines=[f"[Oct 5 19:02] them: {reply}"], notes=[], facts=["hobby: kites"],
            today=[], room_lines=[], queries=["kites"], look=None, now=now - timedelta(days=back))


def test_collect_replies_reads_the_same_files_newest_first(server, folder):
    now = datetime.now()
    _seed(now)
    got = server.collect.replies()
    assert got["days"] == 3
    assert [r["reply"] for r in got["rows"]] == ["today", "yesterday"]        # three days, today included
    assert got["rows"] == list(reversed(rs.read(3)))
    assert got["errors"] == []
    assert [r["reply"] for r in server.collect.replies(days=4)["rows"]] == ["today", "yesterday", "three days ago"]


def test_collect_replies_with_no_record_is_empty(server, folder):
    assert server.collect.replies()["rows"] == []


def test_collect_replies_skips_a_bad_line(server, folder):
    _seed(datetime.now())
    with (folder / f"{datetime.now():%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{broken\n")
    assert [r["reply"] for r in server.collect.replies()["rows"]] == ["today", "yesterday"]


def test_api_replies_is_the_record_as_json_read_fresh(server, folder, monkeypatch):
    monkeypatch.setattr(server, "POLICY", parse_networks(DEFAULT_ALLOWED_NETWORKS))
    with _serving(server) as port:
        empty = _fetch(port, "/api/replies")
        _seed(datetime.now())
        status, headers, body = _fetch(port, "/api/replies")
    assert empty[0] == 200 and json.loads(empty[2])["rows"] == []
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-cache"
    assert [r["reply"] for r in json.loads(body)["rows"]] == ["today", "yesterday"]   # no 60 s cache


def test_the_replies_page_is_served(server, monkeypatch):
    monkeypatch.setattr(server, "POLICY", parse_networks(DEFAULT_ALLOWED_NETWORKS))
    with _serving(server) as port:
        status, headers, body = _fetch(port, "/replies")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert body == (DASH / "static" / "replies.html").read_bytes()


def test_the_page_is_one_table_fed_by_one_fetch_every_minute():
    page = (DASH / "static" / "replies.html").read_text(encoding="utf-8")
    assert page.count("<table") == 1
    assert 'fetch("/api/replies"' in page and "60000" in page
    assert "/api/stats" not in page
    assert "Suite.esc" in page                       # every value escaped before it is shown
    raw = (DASH / "static" / "replies.html").read_bytes()
    assert b"\r" not in raw


def test_a_peer_outside_the_list_gets_403_and_nothing_of_hers(server, folder, monkeypatch):
    _seed(datetime.now())
    reads: list[str] = []

    def _replies(*_a, **_kw):
        reads.append("replies")
        raise AssertionError("the record was read for a refused peer")

    monkeypatch.setattr(server.collect, "replies", _replies)
    monkeypatch.setattr(server, "POLICY", parse_networks("100.64.0.0/10"))
    with _serving(server) as port:
        answers = [_fetch(port, p) for p in ("/api/replies", "/replies", "/static/replies.html")]
        posted = _fetch(port, "/api/replies", method="POST")
    for status, _headers, body in answers + [posted]:
        assert status == 403
        assert body == server.FORBIDDEN
    assert reads == []


def test_a_broken_record_read_is_an_error_line_never_a_crash(server, monkeypatch):
    def _boom(*_a, **_kw):
        raise OSError("disk went away")

    monkeypatch.setattr(server.collect, "replies", _boom)
    monkeypatch.setattr(server, "POLICY", parse_networks(DEFAULT_ALLOWED_NETWORKS))
    with _serving(server) as port:
        status, _headers, body = _fetch(port, "/api/replies")
    assert status == 200
    got = json.loads(body)
    assert got["rows"] == [] and got["errors"] == ["replies: OSError"]
