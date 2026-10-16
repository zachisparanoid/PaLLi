"""Her notes store — dated JSONL, same shape as the moment store.

Hermetic: every test passes an explicit tmp_path root, and one test proves
the module-attribute redirect works (the 2026-07-26 step-02 lesson: a
definition-time default arg silently defeats patch.object redirection).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import ai.self_notes_store as store


def test_write_then_read_roundtrip(tmp_path: Path):
    store.write_note(text="the workshop question wasn't about workshops", root=tmp_path)
    entries = store.read_recent(root=tmp_path)
    assert len(entries) == 1
    assert entries[0].text == "the workshop question wasn't about workshops"
    assert entries[0].written_at > 0


def test_note_is_clipped_not_rejected(tmp_path: Path):
    """A clipped note beats a lost one (spec §2b)."""
    store.write_note(text="x" * 900, root=tmp_path)
    entry = store.read_recent(root=tmp_path)[0]
    assert len(entry.text) == store.NOTE_CHAR_CAP


def test_empty_note_raises(tmp_path: Path):
    """Phantom records break every reader's count; refuse at the door."""
    for bad in ("", "   ", "\n\t "):
        try:
            store.write_note(text=bad, root=tmp_path)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_read_recent_is_newest_first_and_limited(tmp_path: Path):
    for i in range(5):
        store.write_note(text=f"note {i}", root=tmp_path)
    entries = store.read_recent(limit=3, root=tmp_path)
    assert [e.text for e in entries] == ["note 4", "note 3", "note 2"]


def test_count_today_and_texts_today(tmp_path: Path):
    assert store.count_today(root=tmp_path) == 0
    store.write_note(text="first", root=tmp_path)
    store.write_note(text="second", root=tmp_path)
    assert store.count_today(root=tmp_path) == 2
    assert set(store.texts_today(root=tmp_path)) == {"first", "second"}


def test_corrupt_line_is_skipped_not_fatal(tmp_path: Path):
    store.write_note(text="good one", root=tmp_path)
    day_file = next(tmp_path.glob("*.jsonl"))
    with day_file.open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
    assert [e.text for e in store.read_recent(root=tmp_path)] == ["good one"]


def test_module_attribute_redirect_actually_redirects(tmp_path: Path):
    """Step-02 lesson: root must resolve at CALL time, not definition time."""
    with patch.object(store, "SELF_NOTES_DIR", tmp_path / "redirected"):
        path = store.write_note(text="lands in the redirect")
        assert "redirected" in str(path)
        assert store.count_today() == 1
