"""Notes as an introspect source: she can reach for them, never be handed them."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import ai.self_notes_store as store
from core.agent.builtin_tools.introspect import IntrospectTool


def _notes_source(tokens, browse):
    return asyncio.run(IntrospectTool()._notes(tokens, browse))


def test_browse_returns_recent_notes(tmp_path: Path):
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        store.write_note(text="the workshop question wasn't about workshops")
        src = _notes_source([], True)
    assert src.found == 1
    assert "workshop question" in src.entries[0]
    assert src.error is False


def test_query_filters_by_token_and_reports_honest_absence(tmp_path: Path):
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        store.write_note(text="something about the harbor")
        src = _notes_source(["telescope"], False)
    assert src.found == 0
    assert src.error is False
    assert "nothing" in src.absence.lower()


def test_empty_store_is_absence_not_error(tmp_path: Path):
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        src = _notes_source([], True)
    assert src.found == 0 and src.error is False
    assert src.absence


def test_store_failure_is_an_error_never_a_false_nothing():
    """Error must stay distinct from absence — introspect's core honesty rule."""
    with patch.object(store, "read_recent", side_effect=RuntimeError("disk gone")):
        src = _notes_source([], True)
    assert src.error is True
