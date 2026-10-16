from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional


def _build_diff_line(
    *,
    kind: str,
    history_line_number: Optional[int],
    current_line_number: Optional[int],
    text: str,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "history_line_number": history_line_number,
        "current_line_number": current_line_number,
        "text": text,
    }


def build_markdown_diff(
    *,
    history_markdown: str,
    current_markdown: str,
    context_lines: int = 3,
) -> Dict[str, Any]:
    history_lines = str(history_markdown or "").splitlines()
    current_lines = str(current_markdown or "").splitlines()
    matcher = SequenceMatcher(a=history_lines, b=current_lines, autojunk=False)

    added_lines = 0
    removed_lines = 0
    unchanged_lines = 0
    for tag, history_start, history_end, current_start, current_end in matcher.get_opcodes():
        if tag == "equal":
            unchanged_lines += history_end - history_start
            continue
        if tag in {"replace", "delete"}:
            removed_lines += history_end - history_start
        if tag in {"replace", "insert"}:
            added_lines += current_end - current_start

    hunks: List[Dict[str, Any]] = []
    for group in matcher.get_grouped_opcodes(n=context_lines):
        lines: List[Dict[str, Any]] = []
        for tag, history_start, history_end, current_start, current_end in group:
            if tag == "equal":
                for index in range(history_end - history_start):
                    lines.append(
                        _build_diff_line(
                            kind="context",
                            history_line_number=history_start + index + 1,
                            current_line_number=current_start + index + 1,
                            text=history_lines[history_start + index],
                        )
                    )
                continue

            if tag in {"replace", "delete"}:
                for line_index in range(history_start, history_end):
                    lines.append(
                        _build_diff_line(
                            kind="removed",
                            history_line_number=line_index + 1,
                            current_line_number=None,
                            text=history_lines[line_index],
                        )
                    )

            if tag in {"replace", "insert"}:
                for line_index in range(current_start, current_end):
                    lines.append(
                        _build_diff_line(
                            kind="added",
                            history_line_number=None,
                            current_line_number=line_index + 1,
                            text=current_lines[line_index],
                        )
                    )

        hunks.append(
            {
                "history_start_line": group[0][1] + 1 if group else 1,
                "current_start_line": group[0][3] + 1 if group else 1,
                "lines": lines,
            }
        )

    return {
        "summary": {
            "history_line_count": len(history_lines),
            "current_line_count": len(current_lines),
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "unchanged_lines": unchanged_lines,
            "changed_blocks": len(hunks),
            "has_changes": bool(added_lines or removed_lines),
        },
        "hunks": hunks,
    }
