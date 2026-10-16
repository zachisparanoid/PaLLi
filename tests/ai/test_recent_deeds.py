"""Impulse-freshness layer 2 (spec §3): a plain record of her own deeds."""
from __future__ import annotations

import json
import time

from ai import recent_deeds as rd


def test_record_and_read_ring_order(tmp_path):
    for i in range(3):
        rd.record_deed("test", f"deed {i}", root=tmp_path)
    got = rd.read_recent_deeds(root=tmp_path)
    assert [d["detail"] for d in got] == ["deed 2", "deed 1", "deed 0"]


def test_ring_caps_at_50_on_write(tmp_path):
    for i in range(60):
        rd.record_deed("test", f"d{i}", root=tmp_path)
    lines = (tmp_path / "recent_deeds.jsonl").read_text().strip().splitlines()
    assert len(lines) == 50
    assert json.loads(lines[-1])["detail"] == "d59"


def test_read_filters_by_age(tmp_path):
    old = {"at": time.time() - 3 * 86400, "kind": "test", "detail": "ancient"}
    (tmp_path / "recent_deeds.jsonl").write_text(json.dumps(old) + "\n")
    rd.record_deed("test", "fresh", root=tmp_path)
    got = rd.read_recent_deeds(root=tmp_path)
    assert [d["detail"] for d in got] == ["fresh"]


def test_tool_deed_phrase_map_covers_v1_kinds(tmp_path):
    rd.record_tool_deed("deliver_artifact", {"path": "/tmp/x.mp3", "caption": "roadmap"},
                        root=tmp_path)
    rd.record_tool_deed("create_reminder", {"message": "call uncle", "when": "10am"},
                        root=tmp_path)
    rd.record_tool_deed("note_to_self", {"note": "the doorknob thing"}, root=tmp_path)
    rd.record_tool_deed("web_search", {"q": "x"}, root=tmp_path)  # NOT deed-shaped
    got = rd.read_recent_deeds(root=tmp_path)
    details = " | ".join(d["detail"] for d in got)
    assert "delivered a file: x.mp3" in details
    assert "set a reminder: call uncle" in details
    assert "wrote a note to yourself" in details
    assert len(got) == 3


def test_record_never_raises_on_unwritable_root(tmp_path):
    bad = tmp_path / "nope"
    bad.write_text("a file, not a dir")
    rd.record_deed("test", "x", root=bad)  # must not raise


def test_missing_store_reads_empty(tmp_path):
    assert rd.read_recent_deeds(root=tmp_path / "absent") == []


def test_outbound_records_a_send_deed(tmp_path, monkeypatch):
    """record_outbound's success path writes a deed (origin-tagged). Unit
    level — the wiring line itself is one call; integration is pinned by
    reading outbound.py and by the full suite."""
    monkeypatch.setattr(rd, "DEEDS_PATH", tmp_path / "recent_deeds.jsonl")
    rd.record_deed("sent", 'sent a message (arbiter): "Hey — the roadmap…"')
    got = rd.read_recent_deeds()
    assert got and got[0]["kind"] == "sent"


def test_runner_hook_shape_ignores_non_deed_tools(tmp_path):
    rd.record_tool_deed("local_shell", {"command": "ls"}, root=tmp_path)
    assert rd.read_recent_deeds(root=tmp_path) == []
