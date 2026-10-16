from __future__ import annotations

from core.identity.history_diff import build_markdown_diff


def test_build_markdown_diff_reports_no_changes_for_identical_documents() -> None:
    diff = build_markdown_diff(
        history_markdown="---\ndoc_type: memory_summary\n---\nCurrent line\n",
        current_markdown="---\ndoc_type: memory_summary\n---\nCurrent line\n",
    )

    assert diff["summary"] == {
        "history_line_count": 4,
        "current_line_count": 4,
        "added_lines": 0,
        "removed_lines": 0,
        "unchanged_lines": 4,
        "changed_blocks": 0,
        "has_changes": False,
    }
    assert diff["hunks"] == []


def test_build_markdown_diff_reports_added_and_removed_lines() -> None:
    diff = build_markdown_diff(
        history_markdown="---\ndoc_type: memory_summary\nmood: calm\n---\nOld line\n",
        current_markdown="---\ndoc_type: memory_summary\nmood: focused\n---\nOld line\nNew line\n",
    )

    assert diff["summary"]["history_line_count"] == 5
    assert diff["summary"]["current_line_count"] == 6
    assert diff["summary"]["added_lines"] == 2
    assert diff["summary"]["removed_lines"] == 1
    assert diff["summary"]["unchanged_lines"] == 4
    assert diff["summary"]["changed_blocks"] == 1
    assert diff["summary"]["has_changes"] is True
    assert diff["hunks"][0]["lines"] == [
        {
            "kind": "context",
            "history_line_number": 1,
            "current_line_number": 1,
            "text": "---",
        },
        {
            "kind": "context",
            "history_line_number": 2,
            "current_line_number": 2,
            "text": "doc_type: memory_summary",
        },
        {
            "kind": "removed",
            "history_line_number": 3,
            "current_line_number": None,
            "text": "mood: calm",
        },
        {
            "kind": "added",
            "history_line_number": None,
            "current_line_number": 3,
            "text": "mood: focused",
        },
        {
            "kind": "context",
            "history_line_number": 4,
            "current_line_number": 4,
            "text": "---",
        },
        {
            "kind": "context",
            "history_line_number": 5,
            "current_line_number": 5,
            "text": "Old line",
        },
        {
            "kind": "added",
            "history_line_number": None,
            "current_line_number": 6,
            "text": "New line",
        },
    ]
