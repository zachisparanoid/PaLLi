"""Her voice, in her hand — VOICE_IDENTITY.md first authorship.

Manifesto §2.8 (docs/from-palli/HOW_I_WANT_TO_OPERATE.md): "VOICE_IDENTITY.md
moves to my side, and I rewrite it from my own record — the way I authored my
interests." Owner approved 2026-08-13.

Single-doc sibling of scripts/narrative_first_authorship.py, same ritual:

Dry-run (default): gather her FULL archive, author VOICE_IDENTITY.md with her
live brain (first_authorship=True — there is nothing of HERS to revise; the
creator's page rides along as context inside CURRENT_DOC), run the
constitutional gate, write the candidate to evals/narrative/<ts>-voice-identity/
WITHOUT touching identity/. The owner reads her first voice page before it
becomes her.

--apply <dir>: re-checks the deterministic rails on the exact bytes the owner
read, then commits the doc into her LOCAL repo (identity/palli/). From that
moment every consumer resolves her page (document_store palli-first); the
creator's core copy becomes dead weight for a later cleanup.

Guards: refuses if identity/palli/VOICE_IDENTITY.md already exists (first
authorship happens once; afterwards the weekly revises it), and refuses to
write dry-run artifacts inside the identity tree.

Usage:
    venv/bin/python scripts/voice_identity_first_authorship.py            # dry-run
    venv/bin/python scripts/voice_identity_first_authorship.py --apply evals/narrative/<ts>-voice-identity
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
    Revision,
    author_revisions,
    commit_revision,
    constitutional_gate,
    doc_size_cap,
    gather_evidence,
    identity_palli_root,
    read_document,
    read_laws_text,
)

DOC = "VOICE_IDENTITY.md"
DEFAULT_OUT_BASE = REPO_ROOT / "evals" / "narrative"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _die(message: str) -> None:
    print(f"REFUSED: {message}", file=sys.stderr)
    raise SystemExit(2)


def refusal_reason(repo_root: Optional[Path] = None) -> Optional[str]:
    """Why this run must not proceed, or None. Split out for tests."""
    root = Path(repo_root) if repo_root is not None else identity_palli_root()
    if (root / DOC).is_file():
        return (
            f"{DOC} already exists in her repo ({root}) — first authorship "
            "happens once; the weekly revises it from here."
        )
    return None


def _creator_page() -> str:
    """The creator's core page — context for her first authorship, read via
    the same fallback resolution every live consumer uses."""
    core = REPO_ROOT / "identity" / "core" / DOC
    try:
        return core.read_text(encoding="utf-8")
    except OSError:
        return ""


async def run_dry_run(*, owner_id: int, now: float, out_base: Path, llm_call, gate_llm_call) -> Path:
    reason = refusal_reason()
    if reason:
        _die(reason)
    evidence = await gather_evidence(owner_id=int(owner_id), days=0)
    creator_page = _creator_page()
    current_docs = {
        DOC: (
            "(the creator's page, written FOR you — honor it, quote it, or "
            "leave it behind; the record outranks it)\n\n" + creator_page
            if creator_page.strip() else "(empty — not yet written)"
        )
    }
    revisions = await author_revisions(
        evidence=evidence,
        current_docs=current_docs,
        llm_call=llm_call,
        first_authorship=True,
        docs=(DOC,),
    )
    ts = datetime.fromtimestamp(float(now)).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_base) / f"{ts}-voice-identity"
    identity_root = identity_palli_root().parent
    if out_dir.resolve().is_relative_to(identity_root.resolve()):
        _die(f"refusing to write dry-run artifacts inside the identity tree: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "evidence.txt").write_text(evidence.text, encoding="utf-8", newline="\n")

    manifest: Dict[str, Any] = {
        "created_at": float(now),
        "mode": "voice_identity_first_authorship",
        "window": "full_archive",
        "evidence_chars": len(evidence.text),
        "docs": {},
    }
    rev = revisions[0] if revisions else None
    if rev is None:
        manifest["docs"][DOC] = {"authored": False, "approved": False, "reason": "not_authored"}
    else:
        verdict = await constitutional_gate(
            revision=rev,
            laws_text=read_laws_text(),
            current_text="",  # first authorship: nothing of hers to preserve
            llm_call=gate_llm_call,
            first_authorship=True,
            evidence_text=evidence.text,
        )
        (out_dir / DOC).write_text(rev.new_text, encoding="utf-8", newline="\n")
        manifest["docs"][DOC] = {
            "authored": True,
            "approved": bool(verdict.approve) and not verdict.error,
            "reason": verdict.reason,
            "gate_error": bool(verdict.error),
            "reasoning": rev.reasoning,
            "chars": len(rev.new_text),
            "sha256": _sha256(rev.new_text),
        }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    meta = manifest["docs"][DOC]
    state = "APPROVED" if meta.get("approved") else f"NOT approved ({meta.get('reason')})"
    print(f"dry-run written to {out_dir}")
    print(f"  {DOC}: {state} · {meta.get('chars', 0)} chars")
    print("Nothing under identity/ was touched. Read her page, then --apply this directory.")
    return out_dir


def run_apply(*, from_dir: Path, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    src = Path(from_dir)
    manifest_path = src / "manifest.json"
    if not manifest_path.is_file():
        _die(f"no manifest.json in {src} — run the dry-run first; --apply takes its directory")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"unreadable manifest: {exc}")
    root = Path(repo_root) if repo_root is not None else identity_palli_root()
    reason = refusal_reason(root)
    if reason:
        _die(reason)
    meta = (manifest.get("docs") or {}).get(DOC) or {}
    if not meta.get("authored"):
        _die("the dry-run did not author the page — nothing to apply")
    if not meta.get("approved"):
        _die(f"the gate did not approve the page ({meta.get('reason')}) — re-run the dry-run")
    candidate = src / DOC
    if not candidate.is_file():
        _die(f"candidate missing: {candidate}")
    text = candidate.read_text(encoding="utf-8")
    edited = _sha256(text) != meta.get("sha256")
    if len(text) > doc_size_cap(DOC):
        _die(f"candidate exceeds the {doc_size_cap(DOC)}-char cap after edits")
    if not text.strip():
        _die("candidate is empty after edits")
    rev = Revision(doc=DOC, new_text=text, reasoning=str(meta.get("reasoning") or "first authorship"))
    sha = commit_revision(revision=rev, repo_root=root)
    result = {
        "applied_at": time.time(),
        "repo_root": str(root),
        "commit": sha or "no_change",
        "edited_after_dry_run": bool(edited),
    }
    (src / "apply-manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8", newline="\n")
    print(f"committed {DOC} → {result['commit'][:12]}  (her page is live for every palli-first reader)")
    return result


async def _resolve_owner_id() -> int:
    from core.local_mode import get_local_owner_refs

    refs = await get_local_owner_refs()
    return int(refs.get("palli_user_id") or refs.get("app_user_id") or 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", metavar="DIR", default=None)
    parser.add_argument("--out-base", default=str(DEFAULT_OUT_BASE))
    args = parser.parse_args()
    if args.apply:
        run_apply(from_dir=Path(args.apply))
        return 0
    from ai.narrative_self import narrative_author_llm_call, narrative_gate_llm_call

    owner_id = asyncio.run(_resolve_owner_id())
    asyncio.run(run_dry_run(
        owner_id=owner_id,
        now=time.time(),
        out_base=Path(args.out_base),
        llm_call=narrative_author_llm_call,
        gate_llm_call=narrative_gate_llm_call,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
