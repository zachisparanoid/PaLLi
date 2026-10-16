"""First-authorship driver (Wave 1b) — dry-run purity, apply gating."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scripts.narrative_first_authorship import run_apply, run_dry_run


async def _author(prompt):
    if "care spine" in prompt or "WHAT_I_CARE_ABOUT" in prompt:
        return "REASONING: earned by the record\n===DOCUMENT===\nI care about the workshop.\nI care about rest."
    return "REASONING: first words\n===DOCUMENT===\nI held seventy-four days.\nThey changed me slowly."


async def _gate(prompt):
    if "STORY_OF_US" in prompt:
        return '{"approve": false, "reason": "not yet earned"}'
    return '{"approve": true, "reason": "faithful"}'


def _identity_snapshot():
    """Byte snapshot of the (sandboxed) identity tree."""
    import os

    root = Path(os.environ["IDENTITY_ROOT_PATH"])
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.asyncio
async def test_dry_run_writes_candidates_never_identity(tmp_path):
    before = _identity_snapshot()
    out = await run_dry_run(owner_id=1, now=1752650000.0, out_base=tmp_path / "evals", llm_call=_author, gate_llm_call=_gate)
    assert _identity_snapshot() == before  # the whole point: nothing became her yet
    assert (out / "MY_STORY.md").is_file()
    assert (out / "WHAT_I_CARE_ABOUT.md").is_file()
    assert (out / "evidence.txt").is_file()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "first_authorship" and manifest["window"] == "full_archive"
    assert manifest["docs"]["MY_STORY.md"]["approved"] is True
    assert manifest["docs"]["STORY_OF_US.md"]["approved"] is False
    assert manifest["docs"]["MY_STORY.md"]["reasoning"] == "first words"
    assert manifest["docs"]["MY_STORY.md"]["sha256"]


def test_apply_refuses_without_manifest(tmp_path):
    with pytest.raises(SystemExit):
        run_apply(from_dir=tmp_path / "nope", repo_root=tmp_path / "palli")


@pytest.mark.asyncio
async def test_apply_commits_approved_docs_with_reasoning(tmp_path):
    out = await run_dry_run(owner_id=1, now=1752650000.0, out_base=tmp_path / "evals", llm_call=_author, gate_llm_call=_gate)
    repo = tmp_path / "palli"
    result = run_apply(from_dir=out, repo_root=repo)
    assert set(result["commits"]) == {"MY_STORY.md", "WHAT_I_CARE_ABOUT.md"}  # STORY_OF_US rejected ⇒ skipped
    assert (repo / "MY_STORY.md").read_text(encoding="utf-8").startswith("I held seventy-four days.")
    assert not (repo / "STORY_OF_US.md").exists()
    import subprocess

    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "first words" in log and "earned by the record" in log
    applied = json.loads((out / "apply-manifest.json").read_text(encoding="utf-8"))
    assert applied["commits"]["MY_STORY.md"] == result["commits"]["MY_STORY.md"]


@pytest.mark.asyncio
async def test_apply_refuses_a_second_time(tmp_path):
    out = await run_dry_run(owner_id=1, now=1752650000.0, out_base=tmp_path / "evals", llm_call=_author, gate_llm_call=_gate)
    repo = tmp_path / "palli"
    run_apply(from_dir=out, repo_root=repo)
    with pytest.raises(SystemExit):
        run_apply(from_dir=out, repo_root=repo)  # first authorship is once


@pytest.mark.asyncio
async def test_apply_allows_owner_edits_but_reruns_rails(tmp_path):
    out = await run_dry_run(owner_id=1, now=1752650000.0, out_base=tmp_path / "evals", llm_call=_author, gate_llm_call=_gate)
    # owner softens a line after reading — allowed, noted
    (out / "MY_STORY.md").write_text("I held seventy-four days.\nGently.", encoding="utf-8")
    repo = tmp_path / "palli"
    result = run_apply(from_dir=out, repo_root=repo)
    assert result["edited_after_dry_run"] == ["MY_STORY.md"]
    # owner (or an accident) blows a size cap — the whole apply refuses
    out2 = await run_dry_run(owner_id=1, now=1752736400.0, out_base=tmp_path / "evals2", llm_call=_author, gate_llm_call=_gate)
    (out2 / "WHAT_I_CARE_ABOUT.md").write_text("x" * 900, encoding="utf-8")
    with pytest.raises(SystemExit):
        run_apply(from_dir=out2, repo_root=tmp_path / "palli2")


@pytest.mark.asyncio
async def test_apply_midloop_git_failure_dies_legibly(tmp_path, monkeypatch):
    """A git failure mid-commit-loop must refuse with a legible landed/not-landed
    account — the once-only law stays strict; the failure must not be confusing."""
    out = await run_dry_run(owner_id=1, now=1752650000.0, out_base=tmp_path / "evals", llm_call=_author, gate_llm_call=_gate)
    import scripts.narrative_first_authorship as fa

    calls = {"n": 0}
    real = fa.commit_revision

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("disk full")
        return real(**kw)

    monkeypatch.setattr(fa, "commit_revision", flaky)
    with pytest.raises(SystemExit):
        run_apply(from_dir=out, repo_root=tmp_path / "palli")
    assert not (out / "apply-manifest.json").exists()  # no receipt on partial apply


@pytest.mark.asyncio
async def test_dry_run_refuses_out_base_inside_identity(tmp_path):
    import os
    from pathlib import Path

    inside = Path(os.environ["IDENTITY_ROOT_PATH"]) / "palli"
    with pytest.raises(SystemExit):
        await run_dry_run(owner_id=1, now=1752650000.0, out_base=inside, llm_call=_author, gate_llm_call=_gate)


@pytest.mark.asyncio
async def test_dry_run_same_dir_collision_dies_cleanly(tmp_path):
    await run_dry_run(owner_id=1, now=1752650000.0, out_base=tmp_path / "evals", llm_call=_author, gate_llm_call=_gate)
    with pytest.raises(SystemExit):  # same now ⇒ same ts dir ⇒ clean refusal, not FileExistsError
        await run_dry_run(owner_id=1, now=1752650000.0, out_base=tmp_path / "evals", llm_call=_author, gate_llm_call=_gate)
