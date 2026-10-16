# tests/ai/test_monologue_wanting_variant.py
"""The two-file law (Slice-3 pattern): the base monologue prompt is never
edited; the wanting variant differs in exactly one line — the plans-ban
sentence now names where plans LIVE instead of gesturing at 'other
parts'."""
from __future__ import annotations

from pathlib import Path

_BASE = Path("prompts/inner_monologue.txt")
_VARIANT = Path("prompts/inner_monologue_wanting.txt")

_OLD_LINE = (
    "Keep it short. One to three sentences is usually enough. A single "
    "observation is better than a list. Do not generate action items, "
    "reminders, or plans — those belong to other parts of you. This is "
    "just thinking."
)
_NEW_LINE = (
    "Keep it short. One to three sentences is usually enough. A single "
    "observation is better than a list. Do not generate action items, "
    "reminders, or plans — planning lives in your daydream lane, where "
    "your own wants get their time. This is just thinking."
)


def test_base_still_carries_the_old_line_untouched():
    text = _BASE.read_text(encoding="utf-8")
    assert _OLD_LINE in text
    assert "daydream" not in text


def test_variant_differs_in_exactly_one_line():
    base_lines = _BASE.read_text(encoding="utf-8").splitlines()
    var_lines = _VARIANT.read_text(encoding="utf-8").splitlines()
    assert len(base_lines) == len(var_lines)
    diffs = [
        (b, v) for b, v in zip(base_lines, var_lines) if b != v
    ]
    assert len(diffs) == 1
    assert diffs[0] == (_OLD_LINE, _NEW_LINE)


def test_selection_follows_the_flag(monkeypatch):
    import ai.inner_monologue as im

    seen = []

    class FakeLoader:
        def load_prompt(self, name):
            seen.append(name)
            return f"TEMPLATE:{name}"

    monkeypatch.setattr(im, "prompt_loader", FakeLoader())
    monkeypatch.setattr(im, "_wanting_prompt_on", lambda: True)
    assert im._select_monologue_template() == "TEMPLATE:inner_monologue_wanting"
    monkeypatch.setattr(im, "_wanting_prompt_on", lambda: False)
    assert im._select_monologue_template() == "TEMPLATE:inner_monologue"
