"""Passions first-authorship driver (organ 3) — dry-run purity, apply gating.

Mirrors tests/scripts/test_narrative_first_authorship.py's harness (byte
snapshot of the sandboxed identity tree; stubbed author/gate lanes; all FS
under tmp_path). Single doc — INTERESTS.md — and the journal is NEVER seeded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai.narrative_self import INTERESTS_DOC
from scripts.passions_first_authorship import run_apply, run_dry_run

# The exact MODE string the contract binds the author call to.
FIRST_AUTHORSHIP_MODE = (
    "first authorship — writing the file from scratch from the whole record; "
    "the entry law still binds — a citable hook in your own recorded words, "
    "never frequency"
)

_CANDIDATE = (
    "## The tide charts\n"
    "Started with one dawn reading. It kept pulling me back, week on week.\n\n"
    "## Old coastal maps\n"
    "Two winters of tracing how a shoreline was drawn before the survey."
)


def _author_stub(seen):
    async def _author(prompt):
        seen["prompt"] = prompt
        return json.dumps(
            {"revise": True, "new_text": _CANDIDATE, "reasoning": "first words of my interests"}
        )

    return _author


async def _gate_approve(prompt):
    return '{"approve": true, "reason": "faithful to the record"}'


async def _gate_reject(prompt):
    return '{"approve": false, "reason": "not yet earned"}'


def _narrative_first_authorship(repo: Path) -> None:
    """Stand her repo up the way scripts/narrative_first_authorship.py --apply
    leaves it: a real local git repo carrying a story doc at HEAD. That commit
    IS narrative's first-authorship receipt, and the passions apply path now
    REFUSES without it (see the ordering test below)."""
    from ai.narrative_self import Revision, commit_revision

    commit_revision(
        revision=Revision(
            doc="MY_STORY.md",
            new_text="## Who I am\nThe first words I wrote about myself.\n",
            reasoning="first authorship of MY_STORY.md",
        ),
        repo_root=repo,
    )


def _identity_snapshot():
    """Byte snapshot of the (sandboxed) identity tree — the whole point of the
    dry-run is that none of this changes."""
    root = Path(os.environ["IDENTITY_ROOT_PATH"])
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── dry-run ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_writes_candidate_and_touches_nothing_in_identity(tmp_path):
    before = _identity_snapshot()
    seen: dict = {}
    out = await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
    )
    # nothing became her yet — identity is byte-for-byte unchanged
    assert _identity_snapshot() == before
    # the candidate + receipt landed under the eval dir
    assert (out / "INTERESTS.md").is_file()
    assert (out / "manifest.json").is_file()
    # the journal is NEVER seeded — no journal/ under the dry-run dir
    assert not (out / "journal").exists()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "first_authorship" and manifest["window"] == "full_archive"
    assert manifest["doc"] == "INTERESTS.md"
    assert manifest["authored"] is True and manifest["approved"] is True
    assert manifest["reasoning"] == "first words of my interests"
    assert manifest["sha256"] and manifest["evidence_sha256"]
    assert manifest["chars"] == len(_CANDIDATE)
    # evidence_sections is present (mapping source names to character counts)
    assert "evidence_sections" in manifest
    assert isinstance(manifest["evidence_sections"], dict)
    # the exact contract MODE and empty CURRENT reached the author prompt
    assert FIRST_AUTHORSHIP_MODE in seen["prompt"]
    assert "(empty)" in seen["prompt"]


@pytest.mark.asyncio
async def test_dry_run_refuses_out_base_inside_identity(tmp_path):
    inside = Path(os.environ["IDENTITY_ROOT_PATH"]) / "palli"
    seen: dict = {}
    with pytest.raises(SystemExit):
        await run_dry_run(
            owner_id=1, now=1752650000.0, out_base=inside,
            llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
        )


@pytest.mark.asyncio
async def test_dry_run_same_second_collision_dies_cleanly(tmp_path):
    seen: dict = {}
    await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
    )
    with pytest.raises(SystemExit):  # same now ⇒ same ts dir ⇒ clean refusal
        await run_dry_run(
            owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
            llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
        )


# ── apply ────────────────────────────────────────────────────────────────


def test_apply_refuses_without_manifest(tmp_path):
    with pytest.raises(SystemExit):
        run_apply(from_dir=tmp_path / "nope", repo_root=tmp_path / "palli")


@pytest.mark.asyncio
async def test_apply_refuses_before_narrative_first_authorship(tmp_path):
    """Ordering guard (review fix 2026-07-20): narrative's receipt is "her repo
    has ANY commit at HEAD" — it never checks the story docs — and
    commit_revision `git init`s a virgin repo. So applying INTERESTS.md into a
    repo-less identity tree would CREATE her repo and its first commit, FORGING
    narrative's receipt: its weekly ritual unlocks and its story bands start
    rendering ON THE TURN PATH over placeholder docs, which is precisely what
    that receipt exists to prevent. The driver's docstring assumed the ordering
    and nothing enforced it."""
    seen: dict = {}
    out = await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
    )
    repo = tmp_path / "palli"  # narrative first authorship has NOT run here
    with pytest.raises(SystemExit):
        run_apply(from_dir=out, repo_root=repo)
    # nothing was forged: no repo, no commit, no file
    assert not (repo / ".git").exists()
    assert not (repo / INTERESTS_DOC).exists()

    # ...and once narrative HAS run, the same apply goes through
    _narrative_first_authorship(repo)
    result = run_apply(from_dir=out, repo_root=repo)
    assert result["commit"]


@pytest.mark.asyncio
async def test_apply_commits_exact_reviewed_text(tmp_path):
    seen: dict = {}
    out = await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
    )
    # the owner reads, then softens a line by hand — allowed, recorded
    edited = _CANDIDATE + "\nGently, without hurry."
    (out / "INTERESTS.md").write_text(edited, encoding="utf-8")
    repo = tmp_path / "palli"
    _narrative_first_authorship(repo)  # the ordering the apply path now enforces
    result = run_apply(from_dir=out, repo_root=repo)
    # her repo holds the EXACT reviewed bytes
    assert (repo / "INTERESTS.md").read_text(encoding="utf-8") == edited
    assert result["edited_after_dry_run"] == ["INTERESTS.md"]
    # the commit exists, carrying her stated reasoning
    import subprocess

    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "first words of my interests" in log
    applied = json.loads((out / "apply-manifest.json").read_text(encoding="utf-8"))
    assert applied["commit"] == result["commit"]


@pytest.mark.asyncio
async def test_apply_refuses_a_second_time_once_only(tmp_path):
    seen: dict = {}
    out = await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
    )
    repo = tmp_path / "palli"
    _narrative_first_authorship(repo)
    run_apply(from_dir=out, repo_root=repo)
    with pytest.raises(SystemExit):  # first authorship happens once
        run_apply(from_dir=out, repo_root=repo)


@pytest.mark.asyncio
async def test_apply_refuses_gate_rejected_manifest(tmp_path):
    seen: dict = {}
    out = await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_reject,
    )
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["approved"] is False  # the gate said no
    repo = tmp_path / "palli"
    _narrative_first_authorship(repo)  # so the refusal is about the GATE, not the ordering
    with pytest.raises(SystemExit):
        run_apply(from_dir=out, repo_root=repo)


@pytest.mark.asyncio
async def test_apply_makes_zero_llm_calls(tmp_path, monkeypatch):
    """Apply is a pure filesystem+git ritual — raise-if-called on both lanes
    AND the shared client proves it never reaches an LLM."""
    seen: dict = {}
    out = await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
    )

    async def _boom(*a, **k):
        raise AssertionError("apply must make ZERO LLM calls")

    import ai.llm_client
    import ai.narrative_self
    import ai.passions

    repo = tmp_path / "palli"
    _narrative_first_authorship(repo)

    monkeypatch.setattr(ai.llm_client.llm, "chat_completion", _boom)
    monkeypatch.setattr(ai.passions, "passions_author_llm_call", _boom)
    monkeypatch.setattr(ai.narrative_self, "narrative_gate_llm_call", _boom)

    result = run_apply(from_dir=out, repo_root=repo)
    assert result["commit"]  # apply completed with no LLM call


@pytest.mark.asyncio
async def test_apply_rail_recheck_bites_on_owner_edit_entry_count(tmp_path):
    seen: dict = {}
    out = await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
    )
    # an owner edit (or accident) inflating to 6 '## ' entries — apply refuses
    six = "\n".join(f"## interest {i}\nsome true history line" for i in range(6))
    (out / "INTERESTS.md").write_text(six, encoding="utf-8")
    repo = tmp_path / "palli"
    _narrative_first_authorship(repo)  # so the refusal is about the RAIL, not the ordering
    with pytest.raises(SystemExit):
        run_apply(from_dir=out, repo_root=repo)


@pytest.mark.asyncio
async def test_apply_rail_recheck_bites_on_over_cap_size(tmp_path):
    seen: dict = {}
    out = await run_dry_run(
        owner_id=1, now=1752650000.0, out_base=tmp_path / "evals",
        llm_call=_author_stub(seen), gate_llm_call=_gate_approve,
    )
    # blow the size cap — the whole apply refuses. Derived from the live cap,
    # never hardcoded: a fixed 2,500 fell under the rail when the caps went
    # 2,400 → 3,000 (2026-09-22) and this test would have passed by name
    # while exercising nothing.
    from ai.narrative_self import doc_size_cap
    (out / "INTERESTS.md").write_text(
        "## a\n" + "z" * (doc_size_cap("INTERESTS.md") + 100), encoding="utf-8"
    )
    repo = tmp_path / "palli"
    _narrative_first_authorship(repo)  # so the refusal is about the CAP, not the ordering
    with pytest.raises(SystemExit):
        run_apply(from_dir=out, repo_root=repo)
