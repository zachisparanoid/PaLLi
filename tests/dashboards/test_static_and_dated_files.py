"""Two small dashboard fixes (Batch 8, final fix wave — rulings FW-10, FW-11).

- The static check is a path check, not a string prefix: a request for
  ``/static/../static_x/f`` resolves to a sibling folder whose name starts
  with "static", and must never be served.
- The collector's dated stores are read by name in any year: a ``2026-*``
  glob would have gone blank on 2027-01-01.

The server and its collector are loaded from their files the way the command
runs them; every path they read here is under ``tmp_path``.
"""
from __future__ import annotations

import datetime as real_dt
import http.client
import importlib.util
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.trusted_networks import DEFAULT_ALLOWED_NETWORKS, parse_networks

REPO = Path(__file__).resolve().parents[2]
DASH = REPO / "dashboards"


@pytest.fixture(scope="module")
def server():
    added = str(DASH) not in sys.path
    if added:
        sys.path.insert(0, str(DASH))
    try:
        spec = importlib.util.spec_from_file_location("palli_dashboards_server_fw", DASH / "server.py")
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


def _fetch(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_a_sibling_folder_that_shares_the_static_prefix_is_never_served(server, monkeypatch, tmp_path):
    root = tmp_path.resolve()
    static = root / "static"
    static.mkdir()
    (static / "ok.html").write_text("ok", encoding="utf-8")
    sibling = root / "static_x"
    sibling.mkdir()
    (sibling / "secret.html").write_text("SECRET", encoding="utf-8")
    monkeypatch.setattr(server, "STATIC", static)
    monkeypatch.setattr(server, "POLICY", parse_networks(DEFAULT_ALLOWED_NETWORKS))

    with _serving(server) as port:
        served = _fetch(port, "/static/ok.html")
        sideways = _fetch(port, "/static/../static_x/secret.html")
        upward = _fetch(port, "/static/../../etc/hostname")

    assert served == (200, b"ok")
    assert sideways[0] == 404 and b"SECRET" not in sideways[1]
    assert upward[0] == 404


class _Clock2027(real_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2027, 1, 5, 12, 0, 0)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_the_collector_reads_its_dated_stores_in_any_year(server, monkeypatch, tmp_path):
    collect = server.collect
    monkeypatch.setattr(collect, "REPO", tmp_path)
    monkeypatch.setattr(collect, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(collect, "dt", SimpleNamespace(
        datetime=_Clock2027, timedelta=real_dt.timedelta, date=real_dt.date))
    day = "2027-01-04"
    _write_jsonl(tmp_path / f"data/inner_monologue/{day}.jsonl", [{"timestamp": f"{day}T10:00:00"}])
    _write_jsonl(tmp_path / f"data/daydream/{day}.jsonl", [{"x": 1}, {"x": 2}])
    _write_jsonl(tmp_path / f"data/workbench/{day}.jsonl",
                 [{"signoff": "done", "duration_s": 60, "shared": "False"}])
    _write_jsonl(tmp_path / f"data/arbiter/{day}.jsonl", [{"disposition": "sent_text"}])

    out = collect.collect()

    i = out["days"].index(day[5:])
    series = out["series"]
    assert (series["mono"][i], series["dream"][i], series["bench"][i], series["arb_sent"][i]) == (1, 2, 1, 1)
