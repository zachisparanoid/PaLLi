"""The owner is never he/him in her prompt corpus — permanently
(stranger-proofing week 1, task 4).

The 2026-09-07 neutralization rewrote every gendered owner referent in her
prompts to they/them; the real pronouns now reach her through one line built
from the owner's profile (``core.agent.context_adapter.owner_person_line``).
This tripwire keeps the corpus that way: every file under ``prompts/`` and the
prompt assembler itself are scanned line by line for a masculine pronoun
standing alone as a word. A legitimate use goes in ``ALLOWED`` as one
``(path, line-fragment)`` pair — and the set is EMPTY today.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCANNED_DIRS: tuple[str, ...] = ("prompts",)
SCANNED_FILES: tuple[str, ...] = ("core/agent/system_prompt.py",)
# Word boundaries do the work: "she/her", "they/them", "the", "this", "these",
# "history" never match; "he", "His", "HIM", "himself" always do.
PATTERN = re.compile(r"\b(he|him|his|himself)\b", re.IGNORECASE)
# (path relative to the repo, a fragment of the line) — one entry per
# legitimate use, never a whole file. Empty: nothing in the corpus needs one.
ALLOWED: frozenset[tuple[str, str]] = frozenset()


def _corpus() -> list[Path]:
    files: list[Path] = []
    for rel_dir in SCANNED_DIRS:
        files.extend(p for p in sorted((REPO / rel_dir).rglob("*")) if p.is_file())
    files.extend(REPO / rel for rel in SCANNED_FILES)
    return files


def _allowed(rel: str, line: str) -> bool:
    return any(rel == path and fragment in line for path, fragment in ALLOWED)


def _hits() -> list[str]:
    hits: list[str] = []
    for path in _corpus():
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line) and not _allowed(rel, line):
                hits.append(f"{rel}:{number}: {line.strip()}")
    return hits


def test_the_corpus_is_where_this_test_thinks_it_is():
    for rel_dir in SCANNED_DIRS:
        assert (REPO / rel_dir).is_dir(), rel_dir
    for rel in SCANNED_FILES:
        assert (REPO / rel).is_file(), rel
    assert len(_corpus()) > 50


def test_no_masculine_owner_referent_in_the_prompt_corpus():
    hits = _hits()
    assert not hits, (
        "he/him/his owner referents in the prompt corpus — say 'they'/'them'/"
        "'the owner', or add ONE (path, line-fragment) pair to ALLOWED with a "
        "reason:\n" + "\n".join(hits)
    )


def test_every_allowed_entry_still_hits():
    """A stale allowance is a hole in the gate: each (path, fragment) must
    name a real file and a line the pattern still matches. Vacuous today."""
    for rel, fragment in ALLOWED:
        path = REPO / rel
        assert path.is_file(), rel
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        assert any(fragment in line and PATTERN.search(line) for line in lines), (rel, fragment)


@pytest.mark.parametrize(
    "text",
    ["she/her", "they/them", "the", "this", "these", "them", "her", "hers", "history", "chemistry", "hi"],
)
def test_the_pattern_leaves_neutral_words_alone(text):
    assert PATTERN.search(text) is None


@pytest.mark.parametrize(
    "text",
    ["he", "His", "HIM", "himself", "answers HIM could", "beside his context", "(he)"],
)
def test_the_pattern_catches_the_masculine_forms(text):
    assert PATTERN.search(text) is not None
