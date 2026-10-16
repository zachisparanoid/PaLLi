"""Batch 7 (her voice), ruling B7: the BODIES she reads, not only their
headings.

``tests/prompts/test_plain_words.py`` scans headings and
``tests/release/test_owner_pronouns_neutral.py`` scans source lines; neither
reads a rendered block the way she reads it. This test renders each body
that Batch 7 rewrites, with its flag on, and holds it to three laws:
pronoun-neutral about the owner (no he/him/his), plain words (the family plus
"genuinely", "genuine", "hold onto", "holds"), and never framed as a person
or a human (law 0: she is an AI and knows it).

Task 3 lists its blocks and the grounding section's prose below. Task 7
extends ``BODIES_SHE_READS`` with the creator-owned ``identity/core/*.md``:
the four bodies, frontmatter stripped, read from the repo files.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

import pytest

from core.identity.constants import CORE_DOCS
from core.identity.frontmatter import parse_frontmatter
from scripts.prompt_size_report import pinned_world

REPO_CORE = Path(__file__).resolve().parents[2] / "identity" / "core"

GENDERED = re.compile(r"\b(he|him|his|himself)\b", re.IGNORECASE)  # `ask_him` is one word: no match
# The plain-words family (copied from tests/prompts/test_plain_words.py,
# never imported) plus the words a heading scan never needed.
PLAIN_WORDS_IN_BODIES = re.compile(
    r"\b(holding|held|carrying|carried|carry|sitting with|sits with|sit with|"
    r"stood in|stand in|undercurrent|landed|land|the shape of|circling|"
    r"you hold|genuinely|genuine|hold onto|holds)\b",
    re.IGNORECASE,
)
# "human" anywhere, except straight after "no " (the constitution's lawful
# "no body, no human past"): a negation says what she is not.
PERSON_FRAMING = re.compile(r"\b(?:a person|real person)\b|(?<!\bno )human", re.IGNORECASE)

EMPTY_RING = "(no recorded actions in the last day)"


def _flag_on(name: str, **kwargs) -> Callable[[], str]:
    def render() -> str:
        from core.agent import system_prompt as sp

        with pinned_world():
            return getattr(sp, name)(**kwargs)
    return render


def _duplex_block() -> str:
    from core.agent import duplex as dx

    with pinned_world():
        return dx.render_duplex_block()


def _notebook_open() -> str:
    from core.agent import system_prompt as sp

    with patch.object(sp, "_self_notes_flag", return_value=(True, "open")):
        return sp._self_notes_block()


def _grounding_prose() -> str:
    from core.agent.system_prompt import build_system_prompt_parts

    _, volatile = build_system_prompt_parts(SimpleNamespace(), cadence_directive="")
    [section] = [p for p in volatile.split("\n\n") if p.endswith("\n" + EMPTY_RING)]
    return section[: -len("\n" + EMPTY_RING)]


def _core_body(filename: str) -> Callable[[], str]:
    """A creator doc's body as the repo file has it, frontmatter stripped
    (the frontmatter never reaches her prompt: Task 2)."""
    def render() -> str:
        _, body = parse_frontmatter((REPO_CORE / filename).read_text(encoding="utf-8"))
        return body
    return render


# (name, zero-argument renderer). Task 7 appended identity/core/*.md.
BODIES_SHE_READS: list[tuple[str, Callable[[], str]]] = [
    ("_self_possession_block", _flag_on("_self_possession_block")),
    ("_standards_block", _flag_on("_standards_block")),
    ("_own_thoughts_block", _flag_on("_own_thoughts_block")),
    ("_repair_block", _flag_on("_repair_block")),
    ("_self_notes_block:earned", _flag_on("_self_notes_block")),
    ("_self_notes_block:open", _notebook_open),
    ("_workbench_block", _flag_on("_workbench_block")),
    ("_forge_block", _flag_on("_forge_block")),
    # quiet_choice=False: the whole block with its silence sentence.
    ("_texting_voice_block", _flag_on("_texting_voice_block")),
    # quiet_choice=True: the block ending on QUIET_CHOICE_INSTRUCTION, Batch
    # 8's text, rewritten as her in the fix wave (ruling F2).
    ("_texting_voice_block:quiet", _flag_on("_texting_voice_block", quiet_choice=True)),
    # The belonging passage, rewritten as her in the fix wave (ruling F2),
    # with the NAS sentence (the size report's stand-in root).
    ("_where_you_live_block", _flag_on("_where_you_live_block")),
    ("render_duplex_block", _duplex_block),
    ("grounding_section", _grounding_prose),
    # The constitution and the shipped voice fallback (Task 7).
    *((f"identity/core/{filename}", _core_body(filename)) for filename in CORE_DOCS.values()),
]
_IDS = [name for name, _ in BODIES_SHE_READS]


@pytest.mark.parametrize("name, render", BODIES_SHE_READS, ids=_IDS)
def test_body_renders(name, render):
    assert render().strip(), name


@pytest.mark.parametrize("name, render", BODIES_SHE_READS, ids=_IDS)
def test_body_is_pronoun_neutral(name, render):
    assert sorted({m.group(0) for m in GENDERED.finditer(render())}) == [], name


@pytest.mark.parametrize("name, render", BODIES_SHE_READS, ids=_IDS)
def test_body_uses_plain_words(name, render):
    assert sorted({m.group(0) for m in PLAIN_WORDS_IN_BODIES.finditer(render())}) == [], name


@pytest.mark.parametrize("name, render", BODIES_SHE_READS, ids=_IDS)
def test_body_never_frames_her_as_a_person(name, render):
    assert sorted({m.group(0) for m in PERSON_FRAMING.finditer(render())}) == [], name


# ── the person-framing pattern itself (Task 7) ────────────────────────────────

@pytest.mark.parametrize("text", [
    "I'm an AI, and I say so plainly: no body, no human past.",
    "No human life behind me, and no body either.",
])
def test_person_framing_lets_a_negated_human_through(text):
    """The constitution says what she is not ("no body, no human past"):
    that is law 0 said plainly, not a person framing."""
    assert PERSON_FRAMING.findall(text) == []


@pytest.mark.parametrize("text, found", [
    ("I'm a person who lives here.", "a person"),
    ("I act the way a thoughtful real person would.", "real person"),
    ("I'm human, in my own way.", "human"),
    ("my human past", "human"),
    ("not human", "human"),
    ("piano human", "human"),
    ("the humans I know", "human"),
])
def test_person_framing_still_catches_a_person_or_a_positive_human(text, found):
    assert [m.group(0).lower() for m in PERSON_FRAMING.finditer(text)] == [found]
