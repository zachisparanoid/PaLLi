# tests/ai/test_daydream_store.py
"""The daydream store (re-spec §4.1): the self-notes shape — dated JSONL,
newest-first reads, today-count for the daily cap — plus the one sticky
workbench-intent line."""
from __future__ import annotations

import ai.daydream_store as dd


def test_write_read_roundtrip_newest_first(tmp_path):
    dd.write_entry(text="first daydream", root=tmp_path)
    dd.write_entry(text="second daydream", meta={"quest_ops": 1}, root=tmp_path)
    entries = dd.read_recent(root=tmp_path)
    assert [e.text for e in entries] == ["second daydream", "first daydream"]
    assert entries[0].meta == {"quest_ops": 1}


def test_count_today_counts_only_today(tmp_path):
    assert dd.count_today(root=tmp_path) == 0
    dd.write_entry(text="one", root=tmp_path)
    dd.write_entry(text="two", root=tmp_path)
    assert dd.count_today(root=tmp_path) == 2


def test_empty_text_raises(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        dd.write_entry(text="   ", root=tmp_path)


def test_intent_is_sticky_and_overwritten(tmp_path):
    assert dd.read_intent(root=tmp_path) == ""
    dd.write_intent("pick the poem back up", root=tmp_path)
    assert dd.read_intent(root=tmp_path) == "pick the poem back up"
    dd.write_intent("actually: the printer project", root=tmp_path)
    assert dd.read_intent(root=tmp_path) == "actually: the printer project"


def test_bad_lines_skipped(tmp_path):
    dd.write_entry(text="survives", root=tmp_path)
    day_file = next(tmp_path.glob("????-??-??.jsonl"))
    with day_file.open("a", encoding="utf-8") as fh:
        fh.write("{broken\n")
    assert [e.text for e in dd.read_recent(root=tmp_path)] == ["survives"]


# ── the intent, dated + one-shot (item 5b, 2026-08-18) ───────────────────
# The one sticky line lagged: written before her last session, shown to
# the NEXT session as if new ("the daydream still says the letter's
# unwritten, and I've verified it twice"). It now carries when it was
# written and whether a session already took it up.

def test_intent_record_carries_when_and_consumption(tmp_path):
    dd.write_intent("pick the poem back up", root=tmp_path, at=1000.0)
    rec = dd.read_intent_record(root=tmp_path)
    assert rec["text"] == "pick the poem back up"
    assert rec["written_at"] == 1000.0 and rec["consumed_at"] is None
    assert dd.read_intent(root=tmp_path) == "pick the poem back up"
    dd.consume_intent(root=tmp_path, at=2000.0)
    assert dd.read_intent(root=tmp_path) == ""            # taken up: no longer "next"
    rec = dd.read_intent_record(root=tmp_path)
    assert rec["text"] == "pick the poem back up" and rec["consumed_at"] == 2000.0
    dd.write_intent("now the printer", root=tmp_path, at=3000.0)   # a new one resets
    assert dd.read_intent(root=tmp_path) == "now the printer"
    assert dd.read_intent_record(root=tmp_path)["consumed_at"] is None


def test_legacy_plain_text_intent_still_reads(tmp_path):
    (tmp_path / dd.INTENT_FILENAME).write_text("old plain line", encoding="utf-8")
    assert dd.read_intent(root=tmp_path) == "old plain line"
    rec = dd.read_intent_record(root=tmp_path)
    assert rec["text"] == "old plain line" and rec["written_at"] is None


def test_empty_intent_clears(tmp_path):
    dd.write_intent("x", root=tmp_path)
    dd.write_intent("", root=tmp_path)
    assert dd.read_intent(root=tmp_path) == "" and dd.read_intent_record(root=tmp_path)["text"] == ""
