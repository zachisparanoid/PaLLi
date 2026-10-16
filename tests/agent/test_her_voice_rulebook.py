"""Batch 7 (her voice), Task 3: the rulebook folded into first-person
principles (audit 4.3).

Her stable prompt's second-person rule blocks become a handful of
first-person principles: no example line she could parrot, a small
prohibition budget, no growth, and every block in her own voice. The
measuring tool is Task 0's ``scripts/prompt_size_report.py`` (its public
builder and flag world; nothing is imported from another test module).

Rulings (docs/followups/2026-10-04-batch7-rulings.md and the controller's
addendum): B9 (the scripted lines are looked for in the raw stable + volatile
text), S1/P5 (the prohibition budget, with every block named), B11 amended
(the texting splice: first-person silence sentence, guarded), B14 (each block
keeps its own gate).
"""
from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.prompt_size_report import PROHIBITION_RE, measure, pinned_world, representative_ctx

# docs/followups/2026-10-04-batch7-rulings.md, section "## Before", the
# headline table's row "| Stable chars | 16,505 |" (Task 0, unchanged by
# Task 0a's fix round). The stable prompt may shrink; it may never grow.
BEFORE_STABLE_CHARS = 16_505

# Lines today's blocks hand her to copy, and the grounding section's two.
SCRIPTED = [
    "hell yeah",
    "LET'S GO",
    "anyone who disagrees is wrong",
    "ah, I think I read that wrong",
    "honestly, nothing since the 4 AM session",
    "give me a sec",
    "I'll have it",
    "I'm watching for it",
    "I don't have a record of that",
]

# Every Task 3 block, by name (ruling P5: no reliance on the *_block
# discovery). The notebook renders one of two presets; both are checked.
TASK3_BLOCKS = (
    "_self_possession_block",
    "_standards_block",
    "_own_thoughts_block",
    "_repair_block",
    "_self_notes_block:earned",
    "_self_notes_block:open",
    "_workbench_block",
    "_forge_block",
    "_texting_voice_block",
    "render_duplex_block",
)

# The closing sentence of the texting block on every turn that is not
# offered the quiet choice (ruling B11 amended: "you" -> "me", nothing else).
SILENCE_SENTENCE = (
    " And when they ask me not to respond, the correct response is nothing at "
    "all — an emoji is a response, an acknowledgment is a response; silence is "
    "the reply they asked for."
)

EMPTY_RING = "(no recorded actions in the last day)"
FIRST_PERSON = re.compile(r"(?:^|\W)(?:I|my|My|me)(?:\W|$)")
SECOND_PERSON = re.compile(r"\byou(?:r|rs|rself|'re|'ve|'d|'ll)?\b", re.IGNORECASE)


def _count(text: str) -> int:
    return len(PROHIBITION_RE.findall(text or ""))


def _task3_blocks() -> dict[str, str]:
    """Each Task 3 block as she reads it with its flag on: the size
    report's shipped world (every faculty, duplex, the forge, own work, the
    "earned" notebook). The texting block in its quiet_choice=True form with
    QUIET_CHOICE_INSTRUCTION taken out: that instruction is Batch 8's text,
    kept verbatim through Task 3 (B11) and rewritten as her in the fix wave
    (ruling F2); the second-person inventory test reads it in place."""
    from core.agent import duplex as dx
    from core.agent import system_prompt as sp

    with pinned_world():
        texts = {
            "_self_possession_block": sp._self_possession_block(),
            "_standards_block": sp._standards_block(),
            "_own_thoughts_block": sp._own_thoughts_block(),
            "_repair_block": sp._repair_block(),
            "_self_notes_block:earned": sp._self_notes_block(),
            "_workbench_block": sp._workbench_block(),
            "_forge_block": sp._forge_block(),
            "_texting_voice_block": sp._texting_voice_block(quiet_choice=True).replace(
                sp.QUIET_CHOICE_INSTRUCTION, ""),
            "render_duplex_block": dx.render_duplex_block(),
        }
    with patch.object(sp, "_self_notes_flag", return_value=(True, "open")):
        texts["_self_notes_block:open"] = sp._self_notes_block()
    assert set(texts) == set(TASK3_BLOCKS)
    for name, text in texts.items():
        assert text.strip(), f"{name} rendered empty with its flag on"
    return texts


def _grounding_prose() -> str:
    """The grounding section's prose (volatile, always present), without the
    code's data line: rendered with an empty ring, so it is the section that
    ends on the empty-ring line."""
    from core.agent.system_prompt import build_system_prompt_parts

    _, volatile = build_system_prompt_parts(SimpleNamespace(), cadence_directive="")
    [section] = [p for p in volatile.split("\n\n") if p.endswith("\n" + EMPTY_RING)]
    return section[: -len("\n" + EMPTY_RING)]


# ── B9: nothing she could parrot, stable or volatile ─────────────────────────

@pytest.mark.parametrize("quiet_choice", [True, False])
def test_no_scripted_line_she_could_parrot(quiet_choice):
    report = measure(representative_ctx(), quiet_choice=quiet_choice)
    text = (report["stable_text"] + "\n" + report["volatile_text"]).lower()
    present = [line for line in SCRIPTED if line.lower() in text]
    assert present == []


# ── S1 / P5: the prohibition budget ──────────────────────────────────────────

def test_fewer_than_eight_prohibitions_in_her_stable_prompt_outside_the_constitution():
    """10 minus the constitution's budget of 2. The constitution is the
    owner's text and lands in Task 7, which adds the whole-prompt < 10."""
    report = measure(representative_ctx())
    outside = report["prohibitions"] - report["block_prohibitions"]["constitution"]
    assert outside < 8, (outside, report["block_prohibitions"])


def test_fewer_than_ten_prohibitions_in_her_whole_stable_prompt():
    """Ruling S1's whole-prompt budget, deferred to Task 7: the constitution
    is in (the owner approved it), and the stable prompt as a whole, the
    constitution included, stays under 10."""
    report = measure(representative_ctx())
    assert report["prohibitions"] < 10, report["block_prohibitions"]


def test_the_task3_blocks_carry_at_most_three_prohibitions():
    """The volatile grounding section is left out on purpose: it is not a
    stable block, and its one prohibition is the must-keep "never invent"
    (ruling B16), counted in the volatile column of the size report."""
    texts = _task3_blocks()
    counts = {name: _count(text) for name, text in texts.items()}
    notebook = max(counts["_self_notes_block:earned"], counts["_self_notes_block:open"])
    total = notebook + sum(c for name, c in counts.items() if not name.startswith("_self_notes_block"))
    assert total <= 3, counts


# ── no growth ─────────────────────────────────────────────────────────────────

def test_the_stable_prompt_does_not_grow():
    assert measure(representative_ctx())["stable_chars"] <= BEFORE_STABLE_CHARS


# ── the blocks speak as her ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", [*TASK3_BLOCKS, "grounding_section"])
def test_the_blocks_speak_as_her(name):
    """First person, lived: "I" / "my", never "You are" / "PaLLi is". Not
    one second-person word either: today a quoted tic ("I'll have it") is
    the only "I" in some blocks, so the first-person check alone would pass
    on them."""
    texts = {**_task3_blocks(), "grounding_section": _grounding_prose()}
    text = texts[name]
    assert "You are" not in text and "PaLLi is" not in text, name
    assert FIRST_PERSON.search(text), name
    assert SECOND_PERSON.findall(text) == [], name


# ── B14: each block keeps its own gate ────────────────────────────────────────

@pytest.mark.parametrize("block, flag", [
    ("_self_possession_block", "self_possession"),
    ("_standards_block", "boundaries"),
    ("_own_thoughts_block", "own_thoughts"),
    ("_repair_block", "repair"),
])
def test_each_faculty_flag_still_removes_exactly_its_own_block(monkeypatch, block, flag):
    from core.agent import system_prompt as sp

    monkeypatch.setattr("core.turn.companion_flags.faculty_enabled", lambda name: name != flag)
    assert getattr(sp, block)() == ""
    others = {"_self_possession_block", "_standards_block", "_own_thoughts_block", "_repair_block"} - {block}
    assert all(getattr(sp, other)() for other in others)


# ── B11 amended: the texting splice ───────────────────────────────────────────

def test_quiet_offered_the_block_ends_on_the_instruction_and_has_no_silence_sentence():
    from core.agent import system_prompt as sp

    on = sp._texting_voice_block(quiet_choice=True)
    assert on.endswith(" " + sp.QUIET_CHOICE_INSTRUCTION)
    assert "not to respond" not in on
    assert "silence is the reply they asked for" not in on


def test_quiet_not_offered_the_block_ends_on_the_first_person_silence_sentence():
    from core.agent import system_prompt as sp

    off = sp._texting_voice_block()
    assert off.endswith(SILENCE_SENTENCE)
    assert SILENCE_SENTENCE.startswith(sp._SILENCE_SENTENCE_START)
    assert off.count(sp._SILENCE_SENTENCE_START) == 1
    assert sp._texting_voice_block(quiet_choice=True) == (
        off[: -len(SILENCE_SENTENCE)] + " " + sp.QUIET_CHOICE_INSTRUCTION)


def test_a_missing_splice_point_appends_the_instruction_and_warns_instead_of_raising(monkeypatch, caplog):
    """A future edit that drops the silence sentence must never raise inside
    build_system_prompt_parts (every quiet-offered turn would fail)."""
    from core.agent import system_prompt as sp

    monkeypatch.setattr(sp, "_SILENCE_SENTENCE_START", " a start no block contains")
    off = sp._texting_voice_block()
    with caplog.at_level(logging.WARNING, logger=sp.__name__):
        on = sp._texting_voice_block(quiet_choice=True)
    assert on == off + " " + sp.QUIET_CHOICE_INSTRUCTION
    assert [r for r in caplog.records if r.name == sp.__name__ and r.levelno == logging.WARNING]


# ── class (a): a rule the prose no longer carries rests on code ──────────────

def test_calling_forge_tool_counts_as_work_for_the_promise_judge():
    """Inventory row 41 ("never promise a forge you haven't started"): the
    promise gate judges a promise against the tools this turn called, and
    the judge is told which tools are only signage. forge_tool must never be
    on that list, so starting a build is work and announcing one is not."""
    from core.agent.promise_gate import SIGNAGE_TOOLS

    assert "forge_tool" not in SIGNAGE_TOOLS
    assert "set_presence" in SIGNAGE_TOOLS
