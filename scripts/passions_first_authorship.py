"""First authorship of INTERESTS.md — the ritual by which her interests file
first comes to exist (Selfhood organ 3, spec §4.5). The 1a/narrative ritual,
applied to a single doc.

Dry-run (default): gather evidence over the FULL archive (every dive note,
the whole day-gist archive, night reflections, wonder/taste stakes), author
INTERESTS.md with her live brain from scratch, run the constitutional gate,
and write the candidate to evals/passions/<ts>-first-authorship/ WITHOUT
touching identity/palli/ — the owner reads her first interests file before it
becomes hers. The JOURNAL is NEVER seeded here: it seeds empty, as law — only
the nightly pass writes a page, for its own reflection date (spec §3).

--apply <dir>: refuses without that dir's manifest (the dry-run receipt);
refuses until NARRATIVE first authorship has run (its receipt is "her repo has
a commit at HEAD", and committing here would git-init the repo and forge it);
refuses if INTERESTS.md already exists in her repo (first authorship happens
ONCE — she already carries the three narrative story docs and, maybe, journal
pages, so the once-only receipt is THIS file's existence, not the repo having
any history). Re-checks the deterministic rails on the exact bytes the owner
read — hand-edits after reading are allowed (his read is the taste gate; they
are recorded in the apply manifest) — then commits the reviewed text into her
LOCAL repo with her stated reasoning. NO LLM calls in apply. Rollback: plain
git inside identity/palli/.

Usage:
    venv/bin/python scripts/passions_first_authorship.py            # dry-run
    venv/bin/python scripts/passions_first_authorship.py --apply evals/passions/<ts>-first-authorship
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ai.narrative_self import (  # noqa: E402
    FULL_EVIDENCE_CAP,
    INTERESTS_DOC,
    INTERESTS_MAX_ENTRIES,
    Revision,
    _clip_block,
    _interests_entry_count,
    commit_revision,
    constitutional_gate,
    doc_size_cap,
    first_authorship_done as narrative_first_authorship_done,
    identity_palli_root,
    read_laws_text,
)

logger = logging.getLogger(__name__)

DEFAULT_OUT_BASE = REPO_ROOT / "evals" / "passions"

# The author call's MODE, verbatim per the contract — the recognition-shaped
# entry law (2026-07-21 owner revision) still binds even writing from scratch.
FIRST_AUTHORSHIP_MODE = (
    "first authorship — writing the file from scratch from the whole record; "
    "the entry law still binds — a citable hook in your own recorded words, "
    "never frequency"
)
# Per-section budgets for the full-archive read, ai.narrative_self.
# _SECTION_BUDGETS_FULL's precedent. They sum to 47,500 under that module's
# FULL_EVIDENCE_CAP (48,000) — the same constant its own first-authorship
# path uses — leaving headroom for the joining newlines, so the overall cap
# can never bite and no section can be deleted whole.
#
# The old shape had no budgets, ordered stakes LAST, and truncated the joined
# string at a self-invented 24k, half the sibling constant. On the 2026-07-23
# record (47,417 chars — which fits under 48k whole) that silently dropped
# EVERY night reflection and EVERY wonder and taste: the manifest reported
# all four sections at full size while two of them never reached her. It left
# dive-repetition — frequency — as the only aliveness signal available to a
# prompt whose entry law is recognition-shaped precisely so that frequency
# alone is never enough. Same defect ai.passions._gather_week_evidence fixed
# on 2026-07-20; this driver was cloned from the pre-fix shape and missed it.
_FULL_SECTION_BUDGETS = {"stakes": 6_000, "dives": 10_000, "day_gists": 24_000, "reflections": 7_500}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _die(message: str) -> None:
    print(f"REFUSED: {message}", file=sys.stderr)
    raise SystemExit(2)


async def _gather_year_evidence(*, owner_id: int) -> tuple[str, Dict[str, int]]:
    """The whole lived record for first authorship, capped at
    FULL_EVIDENCE_CAP: wonder/taste stakes, ALL dive notes (with topics), the
    full day-gist archive, night reflections.

    Cloned from ai.passions._gather_week_evidence's proven call shapes
    (2026-07-19 signature check: recent_stakes's real kwarg is days_back;
    Stake is a frozen dataclass read by attribute — s.kind/s.stake; day-gist
    entries have .day/.text), widened to a 365-day window. The archive reads
    take a generous limit so the CHAR budgets — not an arbitrary row count —
    are what bound "the whole record" (day_gist/night read_recent default to
    ~30, which would silently truncate a year). Each source is its own
    try/except: a failed reader is an omitted section (recall's degrade
    pattern), never a raise.

    Stakes come FIRST and every section is budgeted — the week gatherer's
    2026-07-20 fix, ported here 2026-07-23 (see _FULL_SECTION_BUDGETS for
    what the unported shape cost). Position is attention as well as survival:
    the wonders and tastes are the strongest evidence for the judgment the
    prompt actually asks for — did this take hold of you — so they lead.

    Returns: (evidence_text, sections_dict) where sections_dict maps source
    names to the characters ACTUALLY INCLUDED, post-clip. It is the owner's
    receipt for what she wrote from, so it must never report a section she
    never saw: the pre-fix version recorded the gathered size and read as
    full coverage over a bundle missing half its sources."""
    sections: Dict[str, str] = {}
    try:
        from ai.stake_ledger import recent_stakes

        stakes = await recent_stakes(days_back=365, limit=200)
        wt = [s for s in stakes if s.kind in ("wonder", "taste")]
        if wt:
            sections["stakes"] = (
                "Wonders and tastes you've held:\n"
                + "\n".join(f"- [{s.kind}] {s.stake}" for s in wt)
            )
    except Exception as exc:
        logger.debug("passions first-authorship: stakes evidence failed: %s", exc)
    try:
        from ai.curiosity_research import recent_research_entries

        dives = recent_research_entries(days_back=365, limit=100)
        if dives:
            sections["dives"] = (
                "Your research notes across the record:\n"
                + "\n".join(
                    f"- [{(e.tick_metadata or {}).get('topic', '?')}] {e.thought}" for e in dives
                )
            )
    except Exception as exc:
        logger.debug("passions first-authorship: dives evidence failed: %s", exc)
    try:
        from ai import day_gist_store

        gists = day_gist_store.read_recent(days_back=365, limit=500)
        if gists:
            sections["day_gists"] = (
                "Your days, across the record:\n"
                + "\n".join(f"- [{g.day}] {g.text}" for g in gists)
            )
    except Exception as exc:
        logger.debug("passions first-authorship: gists evidence failed: %s", exc)
    try:
        from ai import night_cycle_store

        refl = night_cycle_store.read_recent(days_back=365, limit=200)
        if refl:
            sections["reflections"] = (
                "Your reflections, across the record:\n"
                + "\n".join(f"- [{e.date}] {e.text}" for e in refl)
            )
    except Exception as exc:
        logger.debug("passions first-authorship: reflections evidence failed: %s", exc)

    parts: List[str] = []
    included: Dict[str, int] = {}
    for key in ("stakes", "dives", "day_gists", "reflections"):
        if not sections.get(key):
            continue
        block = _clip_block(sections[key], _FULL_SECTION_BUDGETS[key])
        parts.append(block)
        included[key] = len(block)
    combined = "\n\n".join(parts)[:FULL_EVIDENCE_CAP]
    return combined, included


async def run_dry_run(
    *,
    owner_id: int,
    now: float,
    out_base: Path,
    llm_call,
    gate_llm_call,
) -> Path:
    """Gather → author → gate; write the INTERESTS.md candidate + manifest +
    evidence under out_base. Writes NOTHING under identity/ by construction
    (no commit_revision call exists on this path). The journal is never
    seeded — this ritual owns one doc."""
    evidence, evidence_sections = await _gather_year_evidence(owner_id=int(owner_id))

    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("passions_interests")
    if not template:
        _die("passions_interests prompt missing — cannot author")
    prompt = (
        template.replace("<<MODE>>", FIRST_AUTHORSHIP_MODE)
        .replace("<<CURRENT>>", "(empty)")
        .replace("<<EVIDENCE>>", evidence or "(a thin record)")
    )
    raw = await llm_call(prompt)

    from core.llm_json import safe_parse_json_object

    data = safe_parse_json_object(raw or "")
    revision: Optional[Revision] = None
    authorship_note = ""
    author_reason = ""
    if not isinstance(data, dict) or not isinstance(data.get("revise"), bool):
        authorship_note = "unparseable_authorship"
    elif not data["revise"]:
        # "zero is honest if the record holds nothing sustained" (the prompt's
        # own law): no file to author — the writers keep waiting.
        authorship_note = "no_sustained_interest"
        author_reason = str(data.get("reasoning") or "")
    else:
        author_reason = str(data.get("reasoning") or "first authorship of INTERESTS.md")
        revision = Revision(
            doc=INTERESTS_DOC,
            new_text=str(data.get("new_text") or ""),
            reasoning=author_reason,
        )

    ts = datetime.fromtimestamp(float(now)).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_base) / f"{ts}-first-authorship"
    identity_root = identity_palli_root().parent  # the identity/ tree
    if out_dir.resolve().is_relative_to(identity_root.resolve()):
        _die(f"refusing to write dry-run artifacts inside the identity tree: {out_dir}")
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        _die(f"dry-run dir already exists: {out_dir} — wait a second and re-run")
    (out_dir / "evidence.txt").write_text(evidence, encoding="utf-8", newline="\n")

    manifest: Dict[str, Any] = {
        "created_at": float(now),
        "mode": "first_authorship",
        "window": "full_archive",
        "doc": INTERESTS_DOC,
        "evidence_sections": evidence_sections,
        "evidence_chars": len(evidence),
        "evidence_sha256": _sha256(evidence),
    }
    if revision is None:
        manifest.update(
            {"authored": False, "approved": False, "reason": authorship_note, "reasoning": author_reason}
        )
    else:
        laws = read_laws_text()
        verdict = await constitutional_gate(
            revision=revision,
            laws_text=laws,
            current_text="",
            llm_call=gate_llm_call,
            first_authorship=True,
            evidence_text=evidence,
        )
        (out_dir / INTERESTS_DOC).write_text(revision.new_text, encoding="utf-8", newline="\n")
        manifest.update(
            {
                "authored": True,
                "approved": bool(verdict.approve) and not verdict.error,
                "reason": verdict.reason,
                "gate_error": bool(verdict.error),
                "reasoning": revision.reasoning,
                "chars": len(revision.new_text),
                "sha256": _sha256(revision.new_text),
            }
        )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8", newline="\n"
    )

    print(f"dry-run written to {out_dir}")
    if not manifest["authored"]:
        print(f"  INTERESTS.md: NOT authored ({manifest['reason']})")
    else:
        state = "APPROVED" if manifest["approved"] else f"NOT approved ({manifest['reason']})"
        print(f"  INTERESTS.md: {state} · {manifest['chars']} chars")
    print("Nothing under identity/ was touched, and no journal page was seeded.")
    print("Read the candidate, then --apply this directory.")
    return out_dir


def _interests_already_born(root: Path) -> bool:
    """Once-only receipt: is INTERESTS.md already in her repo?

    A file on disk is the plain receipt (it matches ai.passions.
    first_authorship_done). We also consult git so a committed-then-checkout-
    cleaned working tree still counts as born. Unborn HEAD / no repo ⇒ not
    born — the narrative driver's unborn-HEAD handling, kept: a freshly-init'd
    repo may still receive first authorship. NOTE the deliberate divergence
    from the narrative driver, which refuses on ANY history: her repo already
    holds the story docs (and maybe journal pages) by the time this runs, so
    the guard is THIS file's existence, not the repo carrying commits."""
    if (root / INTERESTS_DOC).is_file():
        return True
    if (root / ".git").is_dir():
        probe = subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{INTERESTS_DOC}"],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
        if probe.returncode == 0:
            return True
    return False


def run_apply(*, from_dir: Path, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Commit the approved, owner-read INTERESTS.md into her LOCAL repo. ZERO
    LLM calls — a pure filesystem + git ritual."""
    src = Path(from_dir)
    manifest_path = src / "manifest.json"
    if not manifest_path.is_file():
        _die(f"no manifest.json in {src} — run the dry-run first; --apply takes its directory")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"unreadable manifest: {exc}")

    root = Path(repo_root) if repo_root is not None else identity_palli_root()
    # Ordering guard (review fix 2026-07-20). The narrative organ's receipt is
    # "her repo has ANY commit at HEAD" — it never inspects the story docs —
    # and commit_revision `git init`s a virgin repo. So on a machine where
    # narrative first authorship has NOT run, this apply would create her repo
    # and its first commit and thereby FORGE that receipt: narrative's weekly
    # ritual unlocks and its story bands begin rendering ON THE TURN PATH over
    # placeholder docs, which is exactly what the receipt exists to prevent.
    # The docstring assumed this ordering; now it is enforced.
    if not narrative_first_authorship_done(repo_root=root):
        _die(
            f"narrative first authorship has not run for {root} — run "
            "scripts/narrative_first_authorship.py --apply <dir> FIRST, then this one. "
            "Applying INTERESTS.md into a repo-less identity tree would create her "
            "repo's first commit and forge the narrative receipt, unlocking its "
            "weekly ritual and its story bands over placeholder docs."
        )
    if _interests_already_born(root):
        _die(
            f"{root / INTERESTS_DOC} already exists — first authorship happens once. "
            "Revert or remove it deliberately if you truly mean to redo it."
        )

    if not manifest.get("authored"):
        _die(
            f"the dry-run authored no INTERESTS.md ({manifest.get('reason')}) — "
            "nothing to apply; the record held nothing sustained, or the author was unparseable"
        )
    if not manifest.get("approved"):
        _die(
            f"the gate rejected the candidate ({manifest.get('reason')}) — apply refuses. "
            "Re-run the dry-run; a rejected candidate never becomes her."
        )

    candidate = src / INTERESTS_DOC
    if not candidate.is_file():
        _die(f"approved candidate missing: {candidate}")
    text = candidate.read_text(encoding="utf-8")

    edited: List[str] = []
    if _sha256(text) != manifest.get("sha256"):
        edited.append(INTERESTS_DOC)  # the owner's hand after reading — allowed, recorded

    # Re-check the deterministic rails on the exact (possibly owner-edited)
    # bytes — reuse the narrative_self helpers, never re-implement.
    if not text.strip():
        _die(f"{INTERESTS_DOC} candidate is empty — fix the candidate, then re-apply")
    n = _interests_entry_count(text)
    if n > INTERESTS_MAX_ENTRIES:
        _die(
            f"{INTERESTS_DOC} has {n} '## ' entries (max {INTERESTS_MAX_ENTRIES}) — "
            "trim the candidate, then re-apply"
        )
    cap = doc_size_cap(INTERESTS_DOC)
    if len(text) > cap:
        _die(f"{INTERESTS_DOC} is {len(text)} chars (cap {cap}) — fix the candidate, then re-apply")

    revision = Revision(
        doc=INTERESTS_DOC,
        new_text=text,
        reasoning=str(manifest.get("reasoning") or f"first authorship of {INTERESTS_DOC}"),
    )
    try:
        sha = commit_revision(revision=revision, repo_root=root)
    except Exception as exc:
        _die(
            f"git failed while committing {INTERESTS_DOC}: {exc}\n"
            f"  The dry-run directory is intact. Inspect the repo ({root}), "
            "revert or remove it deliberately, then re-apply."
        )
    print(f"  committed {INTERESTS_DOC} → {(sha or 'no_change')[:12]}")

    result = {
        "applied_at": time.time(),
        "repo_root": str(root),
        "doc": INTERESTS_DOC,
        "commit": sha or "no_change",
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

    from ai.narrative_self import narrative_gate_llm_call
    from ai.passions import passions_author_llm_call

    async def _run() -> None:
        owner_id = await _resolve_owner_id()
        await run_dry_run(
            owner_id=owner_id,
            now=time.time(),
            out_base=Path(args.out),
            llm_call=passions_author_llm_call,
            gate_llm_call=narrative_gate_llm_call,
        )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
