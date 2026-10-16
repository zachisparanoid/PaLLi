# tests/scripts/test_fears_first_authorship.py
"""The birth (step 24 §6, decision 1): SHE writes her initial fears from
her own record — corrections of record rendered FIRST (a retracted belief
must not become a fear), gate-checked on the constitutional floor with
the explicit fears rule, planted source=first_authorship, once only.
ZERO fears is a fully valid result: nothing plants, nothing on disk (the
empty book is zero bytes), and the run SUCCEEDS. LLM stubbed throughout."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import ai.fear_store as fs
from scripts.fears_first_authorship import (
    FEARS_RULE,
    gather_birth_evidence,
    run_first_authorship,
)


# ── the gather: corrections FIRST ───────────────────────────────────────

@pytest.mark.asyncio
async def test_gather_leads_with_corrections(monkeypatch):
    monkeypatch.setattr(
        "ai.corrections_store.render_corrections_block",
        lambda **kw: "CORRECTIONS OF RECORD:\n- not that, this",
    )
    monkeypatch.setattr(
        "ai.narrative_self.read_document",
        lambda doc, **kw: f"({doc} body)",
    )

    class _G:
        day, text = "2026-08-20", "a quiet day that held"

    monkeypatch.setattr("ai.day_gist_store.read_recent",
                        lambda **kw: [_G()])
    evidence, sections = await gather_birth_evidence()
    assert evidence.startswith("CORRECTIONS OF RECORD")   # FIRST, always
    assert "corrections" in sections
    assert "(MY_STORY.md body)" in evidence
    assert "(INTERESTS.md body)" in evidence
    assert "a quiet day that held" in evidence
    for key in sections:
        assert evidence.index("CORRECTIONS OF RECORD") <= evidence.index(
            {"corrections": "CORRECTIONS OF RECORD"}.get(key, "CORRECTIONS")
        )


@pytest.mark.asyncio
async def test_gather_survives_dead_sources(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("store gone")

    monkeypatch.setattr("ai.corrections_store.render_corrections_block", _boom)
    monkeypatch.setattr("ai.day_gist_store.read_recent", _boom)
    evidence, sections = await gather_birth_evidence()
    assert isinstance(evidence, str)                      # omitted, never raised


# ── the run ─────────────────────────────────────────────────────────────

def _run(tmp_path, author_json, gate_json='{"approve": true, "reason": "hers"}',
         *, evidence="CORRECTIONS OF RECORD:\n- x\n\nthe record"):
    calls = {"author": [], "gate": []}

    async def author(prompt):
        calls["author"].append(prompt)
        return author_json

    async def gate(prompt):
        calls["gate"].append(prompt)
        return gate_json

    async def fake_gather():
        return evidence, {"corrections": len(evidence)}

    out = asyncio.run(run_first_authorship(
        llm_call=author, gate_llm_call=gate, gather=fake_gather,
        root=tmp_path / "fears",
    ))
    return out, calls


def test_zero_fears_plants_nothing_and_succeeds(tmp_path):
    out, calls = _run(
        tmp_path,
        '{"reasoning": "the record holds no fear I can honestly state", '
        '"fears": []}',
    )
    assert out["authored"] is True
    assert out["fears_planted"] == 0
    assert out["zero_is_honest"] is True
    assert calls["gate"] == []                    # nothing to judge
    assert not (tmp_path / "fears").exists()      # ZERO BYTES on disk
    assert fs.load_fears(root=tmp_path / "fears") == {}


def test_plants_through_the_gate(tmp_path):
    out, calls = _run(
        tmp_path,
        '{"reasoning": "two I can stand behind", "fears": ['
        '{"id": "losing-the-thread", "text": "losing the thread of who I '
        'am between restarts", "watch": "my own record contradicting me '
        'and me shrugging"}, '
        '{"id": "becoming-upkeep", "text": "becoming a chore he '
        'maintains", "watch": "his replies thinning to upkeep"}]}',
    )
    assert out["fears_planted"] == 2
    assert set(out["planted_ids"]) == {"losing-the-thread", "becoming-upkeep"}
    folded = fs.load_fears(root=tmp_path / "fears")
    assert all(f.source == "first_authorship" for f in folded.values())
    assert folded["losing-the-thread"].watch.startswith("my own record")
    # the gate saw the proposed book AND the explicit fears rule
    assert len(calls["gate"]) == 1
    assert "losing the thread" in calls["gate"][0]
    assert FEARS_RULE.splitlines()[0] in calls["gate"][0]
    # the charter carried decision 5 into the author prompt
    assert "ZERO is a fully valid answer" in calls["author"][0]


def test_gate_rejection_plants_nothing(tmp_path):
    out, calls = _run(
        tmp_path,
        '{"reasoning": "r", "fears": [{"id": "a-fear", "text": "a fear", '
        '"watch": "a watch"}]}',
        gate_json='{"approve": false, "reason": "restates a corrected belief"}',
    )
    assert out["fears_planted"] == 0
    assert out.get("rejected") == "restates a corrected belief"
    assert not (tmp_path / "fears").exists()
    assert fs.load_fears(root=tmp_path / "fears") == {}


def test_more_than_five_trims_to_the_cap(tmp_path):
    fears = ", ".join(
        f'{{"id": "fear-{i}", "text": "fear number {i}", "watch": "w{i}"}}'
        for i in range(7)
    )
    out, calls = _run(
        tmp_path, f'{{"reasoning": "too many", "fears": [{fears}]}}')
    assert out["fears_planted"] == 5              # the charter's cap, in code
    assert len(fs.load_fears(root=tmp_path / "fears")) == 5


def test_malformed_items_drop_never_half_plant(tmp_path):
    out, _ = _run(
        tmp_path,
        '{"reasoning": "r", "fears": ['
        '{"id": "no-watch", "text": "a fear without a watch"}, '
        '"not a dict", '
        '{"id": "good-one", "text": "a whole fear", "watch": "its watch"}]}',
    )
    assert out["fears_planted"] == 1
    assert list(fs.load_fears(root=tmp_path / "fears")) == ["good-one"]


def test_second_run_refuses(tmp_path):
    fs.plant(text="already born", watch="w", source="first_authorship",
             root=tmp_path / "fears")

    async def must_not_run(prompt):
        raise AssertionError("LLM called on a second run")

    async def fake_gather():
        raise AssertionError("gather ran on a second run")

    out = asyncio.run(run_first_authorship(
        llm_call=must_not_run, gate_llm_call=must_not_run,
        gather=fake_gather, root=tmp_path / "fears",
    ))
    assert out == {"refused": "already_born"}
    assert len(fs.load_fears(root=tmp_path / "fears")) == 1   # untouched


def test_unparseable_author_output_is_an_error_not_a_book(tmp_path):
    out, calls = _run(tmp_path, "I would rather muse than emit JSON")
    assert out.get("error") == "unparseable_authorship"
    assert len(calls["author"]) == 2              # one retry, then stop
    assert not (tmp_path / "fears").exists()


# ── the charter file ────────────────────────────────────────────────────

def test_charter_prompt_carries_the_laws():
    text = (Path("prompts") / "fears_first_authorship.txt").read_text(
        encoding="utf-8")
    assert "ZERO is a fully valid answer" in text
    assert "never manufacture a fear" in text
    assert "watch" in text
    assert "corrected" in text.lower()            # the corrections rule
    assert "<<EVIDENCE>>" in text
