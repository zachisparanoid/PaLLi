"""The workbench session prompt — the hour she is handed (ai/workbench.py).

Step 28 task 2 fix 1: the NAS is one knob (PALLI_NAS_ROOT) and an install
may have none. This template is a static string with no place to branch, so
its one NAS mention has to be conditional in WORDING — she is told what is
true on any install, never handed a folder that does not exist.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROMPT = REPO / "prompts" / "workbench_session.txt"


def _load() -> str:
    assert PROMPT.exists(), f"prompt file missing: {PROMPT}"
    return PROMPT.read_text(encoding="utf-8")


def test_the_prompt_loads():
    text = _load()
    assert text and len(text) > 500
    assert "{workbench_md}" in text and "{budget_line}" in text


def test_the_shell_line_never_promises_a_nas_folder_unconditionally():
    text = _load()
    assert "your workspace and your NAS folder are fully yours" not in text
    assert "when one is configured, your NAS folder" in text
