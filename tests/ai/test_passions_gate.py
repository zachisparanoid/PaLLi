# tests/ai/test_passions_gate.py
"""Organ 3 gate extension: INTERESTS.md revisable, journal/ create-only,
both locks. Property-style over the path rails — the gate is the organ's
blast radius, so it gets the paranoid tests."""
from __future__ import annotations

import subprocess

import pytest

from ai.narrative_self import (
    INTERESTS_DOC,
    Revision,
    commit_revision,
    constitutional_gate,
    doc_size_cap,
    write_allowed,
)


def test_write_allowed_policy():
    assert write_allowed("MY_STORY.md") and write_allowed("STORY_OF_US.md")
    assert write_allowed("WHAT_I_CARE_ABOUT.md")
    assert write_allowed(INTERESTS_DOC)
    assert write_allowed("journal/2026-07-20.md")
    for bad in (
        "journal/2026-7-20.md", "journal/notes.md", "journal/2026-07-20.txt",
        "journal/../MY_STORY.md", "../identity/core/CORE_IDENTITY.md",
        "/etc/passwd", "journal/2026-07-20.md/x", "SOMETHING.md", "journal/",
        "journal/2026-07-20.md\n", "journal/2026-07-20.md\r\n", " journal/2026-07-20.md",
    ):
        assert not write_allowed(bad), bad


def test_doc_size_cap_never_keyerrors():
    assert doc_size_cap("MY_STORY.md") == 3000
    assert doc_size_cap(INTERESTS_DOC) == 3000
    assert doc_size_cap("journal/2026-07-20.md") == 2400
    assert doc_size_cap("WHAT_I_CARE_ABOUT.md") == 700


@pytest.mark.asyncio
async def test_gate_rejects_bad_paths_and_wrong_journal_date():
    async def llm(prompt):  # must never be reached for rail rejections
        raise AssertionError("rails must reject before the LLM")

    for doc in ("journal/../MY_STORY.md", "notes.md"):
        v = await constitutional_gate(
            revision=Revision(doc=doc, new_text="x", reasoning="r"),
            laws_text="laws", current_text="", llm_call=llm,
        )
        assert v.approve is False and v.error is False

    # journal page for a date other than the pass's reflection date
    v = await constitutional_gate(
        revision=Revision(doc="journal/2026-07-21.md", new_text="page", reasoning="r"),
        laws_text="laws", current_text="", llm_call=llm,
        expected_journal_date="2026-07-20",
    )
    assert v.approve is False and "journal_date" in v.reason

    # journal doc with NO expected date (a caller that isn't the nightly pass)
    v = await constitutional_gate(
        revision=Revision(doc="journal/2026-07-20.md", new_text="page", reasoning="r"),
        laws_text="laws", current_text="", llm_call=llm,
    )
    assert v.approve is False and "journal_date" in v.reason


@pytest.mark.asyncio
async def test_gate_interests_entry_count_rail():
    async def llm(prompt):
        raise AssertionError("rails must reject before the LLM")

    six = "\n\n".join(f"## Interest {i}\nline" for i in range(6))
    v = await constitutional_gate(
        revision=Revision(doc=INTERESTS_DOC, new_text=six, reasoning="r"),
        laws_text="laws", current_text="", llm_call=llm,
    )
    assert v.approve is False and "entries" in v.reason


def test_pen_create_only_for_journal(tmp_path):
    r1 = Revision(doc="journal/2026-07-20.md", new_text="first page\n", reasoning="day one")
    sha = commit_revision(revision=r1, repo_root=tmp_path)
    assert sha
    r2 = Revision(doc="journal/2026-07-20.md", new_text="rewritten\n", reasoning="no")
    with pytest.raises(ValueError, match="immutable"):
        commit_revision(revision=r2, repo_root=tmp_path)
    # identical-content no-op stays a no-op, not an error
    assert commit_revision(revision=r1, repo_root=tmp_path) == ""


def test_pen_rejects_outside_policy(tmp_path):
    with pytest.raises(ValueError):
        commit_revision(
            revision=Revision(doc="journal/../x.md", new_text="x", reasoning="r"),
            repo_root=tmp_path,
        )
