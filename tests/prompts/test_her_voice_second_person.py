"""Batch 7 (her voice), the fix wave: every "you" left in her stable prompt is
deliberate (the final review's recommendation 2).

Her stable prompt speaks as her, in the first person. This test renders it
the way the size report does (``scripts/prompt_size_report.measure`` over
``representative_ctx()``, the shipped-representative context, with and
without the quiet choice) and finds every second-person word. Each one must
sit inside a span named in ``ALLOWED_SPANS``, with a one-line reason.

The list can only shrink on purpose: a named span must still be in the prompt
and must still carry a second-person word, so the day one is rewritten as her,
this test fails until its entry is taken off. A new "you" anywhere else fails
it too, and the message names the line. What is left on the list is the next
voice batch's worklist.

Checked and NOT on the list, because they carry no second-person word today
(so a "you" added to them fails this test): the memory preamble's code-owned
text (``core.identity.constants.IDENTITY_DOC_PREAMBLES["memory"]``) and the
creator-owned constitution docs in ``identity/core/`` (the owner's to approve,
Task 7), the shipped voice page included.
"""
from __future__ import annotations

import re
from typing import Callable

import pytest

from scripts.prompt_size_report import measure, representative_ctx

SECOND_PERSON = re.compile(r"\b(you|your|yours|yourself)\b", re.IGNORECASE)

# A skill card's trigger line inside <capabilities>: "    when: User asks …".
_SKILL_WHEN_LINE = re.compile(r"(?m)^[ \t]+when: .*$")

Span = tuple[int, int]


def _person_line(stable: str, ctx) -> list[Span]:
    line = str(getattr(ctx, "person_line", "") or "")
    start = stable.find(line) if line else -1
    return [] if start < 0 else [(start, start + len(line))]


def _skill_when_lines(stable: str, ctx) -> list[Span]:
    return [m.span() for m in _SKILL_WHEN_LINE.finditer(stable)]


# name -> (reason, where it sits in the rendered stable prompt)
ALLOWED_SPANS: dict[str, tuple[str, Callable[[str, object], list[Span]]]] = {
    "person_line": (
        '"Your person: {name} ({pronouns})." is code-owned and pinned text '
        "(rulings B12/B13), out of Batch 7's scope.",
        _person_line,
    ),
    "skill_card_when_lines": (
        "The skill cards' `when:` lines in prompts/skills/*.md (the final "
        "review's M5): ruled to the next voice batch.",
        _skill_when_lines,
    ),
}


def _render(quiet_choice: bool) -> tuple[str, object]:
    ctx = representative_ctx()
    return measure(ctx, quiet_choice=quiet_choice)["stable_text"], ctx


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:] if end < 0 else text[start:end]


@pytest.mark.parametrize("quiet_choice", [True, False])
def test_every_second_person_word_sits_in_a_named_span(quiet_choice):
    stable, ctx = _render(quiet_choice)
    spans = [span for _reason, find in ALLOWED_SPANS.values() for span in find(stable, ctx)]
    stray = [
        f"{m.group(0)!r} in: {_line_at(stable, m.start())[:160]}"
        for m in SECOND_PERSON.finditer(stable)
        if not any(start <= m.start() and m.end() <= end for start, end in spans)
    ]
    assert stray == [], "second person outside every named span:\n" + "\n".join(stray)


@pytest.mark.parametrize("quiet_choice", [True, False])
@pytest.mark.parametrize("name", sorted(ALLOWED_SPANS))
def test_each_named_span_is_still_there_and_still_second_person(name, quiet_choice):
    """The list only shrinks on purpose: once a span is rewritten as her (or
    leaves the prompt), take its entry off ALLOWED_SPANS."""
    reason, find = ALLOWED_SPANS[name]
    assert reason.strip(), name
    stable, ctx = _render(quiet_choice)
    spans = find(stable, ctx)
    assert spans, f"{name}: not in the stable prompt any more; take it off the list"
    assert any(SECOND_PERSON.search(stable[start:end]) for start, end in spans), (
        f"{name}: no second-person word left in it; take it off the list")


def test_the_rewritten_passages_are_in_the_prompt_she_reads():
    """The fix wave's two passages (ruling F2) are rendered in this prompt,
    so the inventory above really reads them."""
    from core.agent import system_prompt as sp

    stable, _ = _render(True)
    assert sp.QUIET_CHOICE_INSTRUCTION in stable
    assert "## Where I live\n" in stable and "/srv/nas" in stable
