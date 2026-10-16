"""Batch 5, Task 10 (memory that picks, 2026-09-28): the pure parts of
"her memory of them, in her own voice" — ``core/memory_first_person.py``.

``is_third_person`` is a SHAPE check, not a meaning one: the bullet body
(after the ``- `` and any leading stamps) starts with "User " / "User's" /
"The user " or names PaLLi as a word outside double quotes. The batch
protocol asks the model for one line per input line, same count and order,
same ``- `` and stamps; ``parse_rewrite_reply`` refuses anything else.
``plan_candidates`` picks the lines a rewrite may touch — bullets that are
third-person, not template placeholders and not already on disk — and
``replace_lines`` puts the accepted rewrites back byte-exactly around them.
"""
from __future__ import annotations

import pytest

from core import memory_first_person as fp


# --- the shape --------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "User is working on a Rust project called Atlas.",
    "User's cat is Mochi.",
    "User’s cat is Mochi.",
    "The user asked for a summary.",
    "the user asked for a summary.",
    "USER prefers short replies.",
    "They asked PaLLi to call at noon.",
    "Explicitly invited PaLLi's help.",
    "- [2026-09-12] User likes tea.",                   # a whole line works too
    "- [2026-09-12] [2026-09-12] The user likes tea.",  # double-stamped
])
def test_third_person_shapes(body):
    assert fp.is_third_person(body) is True


@pytest.mark.parametrize("body", [
    "They're working on a Rust project called Atlas.",
    "They told me to call anytime.",
    "- [2026-09-12] They like tea.",
    "Username for the NAS is admin.",       # "User" as a prefix of a word
    "palli.yaml is their config file.",     # lowercase, a file name
    'They said "PaLLi owes: a link" — verbatim.',  # PaLLi only inside quotes
    "No active curated threads yet.",
    "",
])
def test_first_person_and_other_shapes(body):
    assert fp.is_third_person(body) is False


# --- the batch protocol -------------------------------------------------------

def test_the_messages_carry_the_instruction_and_the_lines_in_order():
    lines = ["- [2026-09-12] User likes tea.", "- The user runs 5 km."]
    messages = fp.build_rewrite_messages(lines)
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == fp.INSTRUCTION
    assert messages[1]["content"] == "\n".join(lines)
    assert "they/them" in fp.INSTRUCTION and "I/me/my" in fp.INSTRUCTION
    assert "same order" in fp.INSTRUCTION and "- " in fp.INSTRUCTION


def test_parse_keeps_count_and_order():
    reply = "- [2026-09-12] They like tea.\n- They run 5 km.\n"
    assert fp.parse_rewrite_reply(reply, 2) == ["- [2026-09-12] They like tea.", "- They run 5 km."]


def test_parse_ignores_preamble_blank_lines_and_code_fences():
    reply = "Here you go:\n```\n- [2026-09-12] They like tea.\n\n- They run 5 km.\n```\n"
    assert fp.parse_rewrite_reply(reply, 2) == ["- [2026-09-12] They like tea.", "- They run 5 km."]


def test_parse_refuses_a_count_mismatch():
    assert fp.parse_rewrite_reply("- They like tea.\n", 2) is None
    assert fp.parse_rewrite_reply("- a\n- b\n- c\n", 2) is None
    assert fp.parse_rewrite_reply("", 1) is None
    assert fp.parse_rewrite_reply(None, 1) is None
    assert fp.parse_rewrite_reply("1. They like tea.\n", 1) is None  # numbered, not a bullet


# --- the candidates and the write-back -------------------------------------

_DOC = (
    "## Durable Context\n"
    "- User's cat is Mochi.\n"
    "- They like tea.\n"
    "- Interests: No explicit interest list yet.\n"
    "\n"
    "## Active Threads\n"
    "- [2026-09-01] User is fixing the NAS backup.\n"
    "- No active curated threads yet.\n"
    "\n"
    "## Important Decisions\n"
    "- User decided on Postgres; PaLLi agreed.\n"
    "\n"
    "## Useful References\n"
    "- No durable references recorded yet.\n"
)


def test_plan_candidates_picks_third_person_bullets_with_their_sections():
    cands = fp.plan_candidates(_DOC)
    assert [(c.index, c.section, c.line) for c in cands] == [
        (1, "Durable Context", "- User's cat is Mochi."),
        (6, "Active Threads", "- [2026-09-01] User is fixing the NAS backup."),
        (10, "Important Decisions", "- User decided on Postgres; PaLLi agreed."),
    ]


def test_plan_candidates_never_touches_a_line_already_on_disk():
    previous = "## Durable Context\n- User's cat is Mochi.\n"
    cands = fp.plan_candidates(_DOC, previous=previous)
    assert [c.index for c in cands] == [6, 10]


def test_plan_candidates_reads_a_crlf_document():
    doc = "## Durable Context\r\n- User likes tea.\r\n- They like coffee.\r\n"
    cands = fp.plan_candidates(doc)
    assert [(c.index, c.line) for c in cands] == [(1, "- User likes tea.")]


def test_replace_lines_is_byte_exact_around_the_replacement():
    out = fp.replace_lines(_DOC, {1: "- Their cat is Mochi."})
    assert out == _DOC.replace("- User's cat is Mochi.", "- Their cat is Mochi.")
    assert fp.replace_lines(_DOC, {}) == _DOC


def test_replace_lines_keeps_a_carriage_return_and_indentation():
    doc = "## A\r\n  - User likes tea.\r\n"
    assert fp.replace_lines(doc, {1: "- They like tea."}) == "## A\r\n  - They like tea.\r\n"
