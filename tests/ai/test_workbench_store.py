"""Workbench session store — dated JSONL, the present-moment store pattern
(call-time root so the hermetic probe redirect is real)."""
from __future__ import annotations

import json
import time


def test_write_and_count_today(tmp_path):
    from ai import workbench_store as ws

    p = ws.write_session(record={"project": "first", "rounds": 3,
                                 "outcome": "wrote", "started": time.time()},
                         root=tmp_path)
    assert p.exists() and p.parent == tmp_path
    assert ws.count_today(root=tmp_path) == 1
    ws.write_session(record={"project": "first", "rounds": 1,
                             "outcome": "quiet", "started": time.time()},
                     root=tmp_path)
    assert ws.count_today(root=tmp_path) == 2


def test_read_recent_newest_first(tmp_path):
    from ai import workbench_store as ws

    ws.write_session(record={"project": "a", "started": 1.0}, root=tmp_path)
    ws.write_session(record={"project": "b", "started": 2.0}, root=tmp_path)
    out = ws.read_recent(root=tmp_path)
    assert [r["project"] for r in out] == ["b", "a"]


def test_call_time_root_resolution(monkeypatch, tmp_path):
    """The 2026-07-26 store lesson: module-attr redirect must actually
    redirect (probe engine patches WORKBENCH_DIR)."""
    from ai import workbench_store as ws

    monkeypatch.setattr(ws, "WORKBENCH_DIR", tmp_path)
    p = ws.write_session(record={"project": "x", "started": 3.0})
    assert p.parent == tmp_path


def test_pending_share_keeps_paragraphs(tmp_path):
    """A SHARE is usually one sentence, but the letter of 2026-08-19 had
    paragraphs — collapsing all whitespace sent it as one block. Lines are
    tidied; blank-line breaks survive."""
    from ai import workbench_store as ws
    text = "First  para,\n  two lines.\n\n\nSecond   para."
    ws.write_pending_share(text=text, owner_id=1, root=tmp_path)
    rows = ws.read_pending_shares(root=tmp_path)
    assert rows[0]["text"] == "First para, two lines.\n\nSecond para."
    ws.write_pending_share(text=text, owner_id=1, root=tmp_path)   # idempotent on the tidied text
    assert len(ws.read_pending_shares(root=tmp_path)) == 1
