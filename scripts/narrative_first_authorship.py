"""First authorship — the 1a ritual applied to identity (Wave 1b, spec §4.5).

Dry-run (default): gather evidence over the FULL archive (every day-gist,
every stake with its resolution, every reflection, the momentum arc), author
all three documents with her live brain, run the constitutional gate, and
write the candidates to evals/narrative/<ts>-first-authorship/ WITHOUT
touching identity/palli/ — the owner reads her first self-story before it
becomes her.

--apply <dir>: refuses without that dir's manifest (the dry-run receipt) and
refuses if her repo already has commits (first authorship happens once).
Re-checks the deterministic rails on the exact bytes the owner read —
hand-edits after reading are allowed (his read is the taste gate; they are
recorded in the apply manifest) — then initializes the LOCAL repo and
commits each approved document with her stated reasoning. NO LLM calls in
apply. Rollback: plain git inside identity/palli/.

Usage:
    venv/bin/python scripts/narrative_first_authorship.py            # dry-run
    venv/bin/python scripts/narrative_first_authorship.py --apply evals/narrative/<ts>-first-authorship
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ai.narrative_self import (  # noqa: E402
    DOC_SIZE_CAPS,
    Revision,
    author_revisions,
    commit_revision,
    constitutional_gate,
    gather_evidence,
    identity_palli_root,
    read_document,
    read_laws_text,
)

# The 1b ritual is HISTORICAL: it authored her original three documents and
# ran once (2026-07-18). Its doc set is frozen — later additions to
# RITUAL_DOCS (VOICE_IDENTITY, 2026-08-13, which has its own gated
# script) must never retroactively join this ritual.
RITUAL_DOCS = ("WHAT_I_CARE_ABOUT.md", "MY_STORY.md", "STORY_OF_US.md")

DEFAULT_OUT_BASE = REPO_ROOT / "evals" / "narrative"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _die(message: str) -> None:
    print(f"REFUSED: {message}", file=sys.stderr)
    raise SystemExit(2)


async def run_dry_run(
    *,
    owner_id: int,
    now: float,
    out_base: Path,
    llm_call,
    gate_llm_call,
) -> Path:
    """Gather → author → gate; write candidates + manifest + evidence under
    out_base. Writes NOTHING under identity/ by construction (no
    commit_revision call exists on this path)."""
    evidence = await gather_evidence(owner_id=int(owner_id), days=0)
    current_docs = {doc: read_document(doc) for doc in RITUAL_DOCS}
    revisions = {
        r.doc: r
        for r in await author_revisions(
            evidence=evidence, current_docs=current_docs, llm_call=llm_call,
            first_authorship=True, docs=RITUAL_DOCS,
        )
    }
    laws = read_laws_text()
    ts = datetime.fromtimestamp(float(now)).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_base) / f"{ts}-first-authorship"
    identity_root = identity_palli_root().parent  # the identity/ tree
    if out_dir.resolve().is_relative_to(identity_root.resolve()):
        _die(f"refusing to write dry-run artifacts inside the identity tree: {out_dir}")
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        _die(f"dry-run dir already exists: {out_dir} — wait a second and re-run")
    (out_dir / "evidence.txt").write_text(evidence.text, encoding="utf-8", newline="\n")

    manifest: Dict[str, Any] = {
        "created_at": float(now),
        "mode": "first_authorship",
        "window": "full_archive",
        "evidence_sections": dict(evidence.sections),
        "evidence_chars": len(evidence.text),
        "docs": {},
    }
    for doc in RITUAL_DOCS:
        rev = revisions.get(doc)
        if rev is None:
            manifest["docs"][doc] = {"authored": False, "approved": False, "reason": "not_authored"}
            continue
        verdict = await constitutional_gate(
            revision=rev,
            laws_text=laws,
            current_text=current_docs.get(doc, ""),
            llm_call=gate_llm_call,
            first_authorship=True,
            evidence_text=evidence.text,
        )
        (out_dir / doc).write_text(rev.new_text, encoding="utf-8", newline="\n")
        manifest["docs"][doc] = {
            "authored": True,
            "approved": bool(verdict.approve) and not verdict.error,
            "reason": verdict.reason,
            "gate_error": bool(verdict.error),
            "reasoning": rev.reasoning,
            "chars": len(rev.new_text),
            "sha256": _sha256(rev.new_text),
        }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8", newline="\n"
    )
    print(f"dry-run written to {out_dir}")
    for doc, meta in manifest["docs"].items():
        state = "APPROVED" if meta.get("approved") else f"NOT approved ({meta.get('reason')})"
        print(f"  {doc}: {state} · {meta.get('chars', 0)} chars")
    print("Nothing under identity/ was touched. Read the candidates, then --apply this directory.")
    return out_dir


def run_apply(*, from_dir: Path, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Commit the approved, owner-read candidates into her LOCAL repo."""
    src = Path(from_dir)
    manifest_path = src / "manifest.json"
    if not manifest_path.is_file():
        _die(f"no manifest.json in {src} — run the dry-run first; --apply takes its directory")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"unreadable manifest: {exc}")
    root = Path(repo_root) if repo_root is not None else identity_palli_root()
    if (root / ".git").is_dir():
        import subprocess

        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
        if probe.returncode == 0:
            _die(
                f"{root} already has a history — first authorship happens once. "
                "Revert or remove that repo deliberately if you truly mean to redo it."
            )

    to_commit: Dict[str, Revision] = {}
    edited: list[str] = []
    for doc in RITUAL_DOCS:
        meta = (manifest.get("docs") or {}).get(doc) or {}
        if not meta.get("approved"):
            continue
        candidate = src / doc
        if not candidate.is_file():
            _die(f"approved candidate missing: {candidate}")
        text = candidate.read_text(encoding="utf-8")
        if _sha256(text) != meta.get("sha256"):
            edited.append(doc)  # the owner's hand after reading — allowed, recorded
        cap = DOC_SIZE_CAPS[doc]
        if len(text) > cap:
            _die(f"{doc} is {len(text)} chars (cap {cap}) — fix the candidate, then re-apply")
        if not text.strip():
            _die(f"{doc} candidate is empty — fix the candidate, then re-apply")
        to_commit[doc] = Revision(doc=doc, new_text=text, reasoning=str(meta.get("reasoning") or f"first authorship of {doc}"))
    if not to_commit:
        _die("no approved candidates to apply")

    commits: Dict[str, str] = {}
    for doc, rev in to_commit.items():
        try:
            sha = commit_revision(revision=rev, repo_root=root)
        except Exception as exc:
            landed = ", ".join(f"{d} → {s[:12]}" for d, s in commits.items()) or "none"
            remaining = ", ".join(d for d in to_commit if d not in commits and d != doc)
            _die(
                f"git failed while committing {doc}: {exc}\n"
                f"  landed: {landed}\n"
                f"  not landed: {doc}" + (f", {remaining}" if remaining else "") + "\n"
                "  The dry-run directory is intact. Inspect the repo "
                f"({root}), revert or remove it deliberately, then re-apply."
            )
        commits[doc] = sha or "no_change"
        print(f"  committed {doc} → {commits[doc][:12]}")
    result = {
        "applied_at": time.time(),
        "repo_root": str(root),
        "commits": commits,
        "edited_after_dry_run": edited,
    }
    (src / "apply-manifest.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8", newline="\n"
    )
    print(f"apply manifest written to {src / 'apply-manifest.json'}")
    return result


async def _resolve_owner_id() -> int:
    from core.local_mode import get_local_owner_refs

    refs = await get_local_owner_refs()
    return int(refs.get("app_user_id") or 0)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", metavar="DIR", help="apply a previously dry-run directory")
    parser.add_argument("--out", default=str(DEFAULT_OUT_BASE), help="dry-run output base directory")
    args = parser.parse_args(argv)
    if args.apply:
        run_apply(from_dir=Path(args.apply))
        return

    from ai.narrative_self import narrative_author_llm_call, narrative_gate_llm_call

    async def _run() -> None:
        owner_id = await _resolve_owner_id()
        await run_dry_run(
            owner_id=owner_id,
            now=time.time(),
            out_base=Path(args.out),
            llm_call=narrative_author_llm_call,
            gate_llm_call=narrative_gate_llm_call,
        )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
