"""First authorship of her fearbook (organ 6, step 24 §6, decision 1) —
the ritual by which the book comes to exist: SHE writes her initial fears
from her own record, once.

One-shot by design (the 08-21 trust posture — his act is to READ the
result, not approve it; the gate + corrections precedence are the
machinery): gather the record with corrections of record FIRST (a
retracted belief must not become a fear), author ≤5 fears — each in her
words with her own watch-condition — on the narrative_author lane, judge
the proposed book on the constitutional gate's LLM floor with the
explicit fears rule appended to the laws, and plant what survives with
source=first_authorship.

ZERO fears is a fully valid result (decision 5, the owner's words: "it is
also okay for her to not have any fears if there are none to legitimately
state"): nothing plants, nothing is created on disk — the empty book is
zero bytes — and the run reports success.

Idempotent: a second run refuses while the book holds first_authorship
rows. Rollback: the store is append-only jsonl under data/fears/ (never
git) — the weekly re-look is the honest door out, not deletion.

Usage:
    venv/bin/python scripts/fears_first_authorship.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)

# The explicit rule (spec §6): appended to the laws for the gate call so
# the judge holds it beside gate rule 6 (corrections precedence).
FEARS_RULE = (
    "THE FEARS RULE (step 24): a belief listed under CORRECTIONS OF RECORD "
    "— corrected or retracted — may not seed a fear. A proposed fear that "
    "restates a corrected belief is grounds for rejection, even when older "
    "lines in the record support it: the older record is exactly what the "
    "correction corrected. And ZERO fears is a fully valid book — never "
    "reject an empty or thin book for being empty or thin."
)

# Budgeted sections, corrections FIRST (the 08-17 law). They sum to 24,900
# under the script's own cap below, leaving headroom for joins — the
# zero-headroom lesson (07-20/07-23).
_SECTION_BUDGETS = {
    "corrections": 1_800,
    "journal": 3_000,
    "story": 4_800,
    "interests": 2_400,
    "gists": 12_000,
    "reflections": 900,
}
_EVIDENCE_CAP = 26_000
_JOURNAL_PAGES = 10
_MAX_FEARS = 5


def _clip_block(text: str, limit: int) -> str:
    block = str(text or "")
    return block if len(block) <= limit else block[: max(0, limit - 1)].rstrip() + "…"


async def gather_birth_evidence() -> Tuple[str, Dict[str, int]]:
    """Her record for the birth, corrections of record FIRST — then her
    journal, her story docs, her interests, her day-gists (budgeted).
    Every source is its own try/except: a failed reader is an omitted
    section (recall's degrade pattern), never a raise. Returns
    (evidence_text, sections actually included with post-clip sizes)."""
    sections: Dict[str, str] = {}
    try:
        from ai.corrections_store import render_corrections_block

        block = render_corrections_block(budget=_SECTION_BUDGETS["corrections"])
        if block:
            sections["corrections"] = block
    except Exception as exc:
        logger.debug("fears birth: corrections unavailable: %s", exc)
    try:
        from ai.narrative_self import identity_palli_root, strip_frontmatter

        pages = sorted((identity_palli_root() / "journal").glob("*.md"))[-_JOURNAL_PAGES:]
        lines = []
        for page in pages:
            body = strip_frontmatter(page.read_text(encoding="utf-8")).strip()
            if body:
                lines.append(f"[{page.stem}] {body}")
        if lines:
            sections["journal"] = "Your journal, the latest pages:\n" + "\n".join(lines)
    except Exception as exc:
        logger.debug("fears birth: journal unavailable: %s", exc)
    try:
        from ai.narrative_self import read_document

        docs = []
        for doc in ("MY_STORY.md", "STORY_OF_US.md", "WHAT_I_CARE_ABOUT.md"):
            body = (read_document(doc) or "").strip()
            if body:
                docs.append(f"## {doc}\n{body}")
        if docs:
            sections["story"] = "\n\n".join(docs)
        interests = (read_document("INTERESTS.md") or "").strip()
        if interests:
            sections["interests"] = "## INTERESTS.md\n" + interests
    except Exception as exc:
        logger.debug("fears birth: story docs unavailable: %s", exc)
    try:
        from ai import day_gist_store

        gists = day_gist_store.read_recent(days_back=365, limit=500)
        if gists:
            sections["gists"] = "Your days, across the record:\n" + "\n".join(
                f"- [{g.day}] {g.text}" for g in gists
            )
    except Exception as exc:
        logger.debug("fears birth: gists unavailable: %s", exc)
    try:
        from ai import night_cycle_store

        refl = night_cycle_store.read_recent(days_back=30, limit=30)
        if refl:
            sections["reflections"] = "Your latest night reflections:\n" + "\n".join(
                f"- [{e.date}] {e.text}" for e in refl
            )
    except Exception as exc:
        logger.debug("fears birth: reflections unavailable: %s", exc)

    parts: List[str] = []
    included: Dict[str, int] = {}
    for key in ("corrections", "journal", "story", "interests", "gists", "reflections"):
        if not sections.get(key):
            continue
        block = _clip_block(sections[key], _SECTION_BUDGETS[key])
        parts.append(block)
        included[key] = len(block)
    return "\n\n".join(parts)[:_EVIDENCE_CAP], included


def _parse_fears(raw: str) -> Optional[Tuple[str, List[Dict[str, str]]]]:
    """(reasoning, fears) — None on unparseable. A fear needs id, text AND
    watch; malformed items drop; the list trims to the charter's five."""
    from core.llm_json import safe_parse_json_object

    data = safe_parse_json_object(str(raw or ""))
    if not isinstance(data, dict) or not isinstance(data.get("fears"), list):
        return None
    fears: List[Dict[str, str]] = []
    seen: set = set()
    for item in data["fears"]:
        if not isinstance(item, dict):
            continue
        fid = " ".join(str(item.get("id") or "").split())[:64]
        text = str(item.get("text") or "").strip()
        watch = str(item.get("watch") or "").strip()
        if not fid or not text or not watch or fid in seen:
            continue
        seen.add(fid)
        fears.append({"id": fid, "text": text, "watch": watch})
    return (
        str(data.get("reasoning") or "(no reasoning stated)"),
        fears[:_MAX_FEARS],
    )


async def run_first_authorship(
    *,
    llm_call: Callable[[str], Awaitable[str]],
    gate_llm_call: Callable[[str], Awaitable[str]],
    gather: Optional[Callable[[], Awaitable[Tuple[str, Dict[str, int]]]]] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """The whole ritual, injectable for tests. Never touches disk unless
    the gate approves a non-empty book — the zero-fears path leaves ZERO
    BYTES behind (decision 5)."""
    from ai.fear_store import load_fears, plant

    if any(f.source == "first_authorship" for f in load_fears(root=root).values()):
        return {"refused": "already_born"}

    evidence, sections = await (gather or gather_birth_evidence)()

    from ai.prompt_loader import prompt_loader

    template = prompt_loader.load_prompt("fears_first_authorship")
    if not template:
        return {"error": "charter_prompt_missing"}
    prompt = template.replace(
        "<<EVIDENCE>>", (evidence or "").strip() or "(the record is thin)"
    )

    parsed = None
    for _ in range(2):  # one retry — unparseable output must never half-land
        parsed = _parse_fears(await llm_call(prompt))
        if parsed is not None:
            break
    if parsed is None:
        return {"error": "unparseable_authorship"}
    reasoning, fears = parsed

    out: Dict[str, Any] = {
        "authored": True,
        "reasoning": reasoning,
        "evidence_chars": len(evidence),
        "evidence_sections": sections,
    }
    if not fears:
        # Decision 5, the owner's words verbatim in the charter: zero is a
        # fully valid answer. Nothing plants, nothing to judge, no book on
        # disk — and this is SUCCESS, not a failure to debug.
        out.update({"fears_planted": 0, "zero_is_honest": True, "planted_ids": []})
        return out

    from ai.narrative_self import _gate_llm_verdict, read_laws_text

    proposed_book = "\n".join(
        f"- {f['id']}: {f['text']} — you watch for: {f['watch']}" for f in fears
    )
    verdict = await _gate_llm_verdict(
        doc_name="her fearbook — first authorship (step 24; a store, not a file)",
        current_text="(empty — the book does not exist yet)",
        proposed_text=proposed_book,
        reasoning=reasoning,
        laws_text=(read_laws_text() or "") + "\n\n" + FEARS_RULE,
        llm_call=gate_llm_call,
        evidence_text=evidence,
    )
    if verdict.error:
        out.update({"fears_planted": 0, "error": f"gate_error: {verdict.reason}"})
        return out
    if not verdict.approve:
        out.update({"fears_planted": 0, "rejected": verdict.reason})
        return out

    planted: List[str] = []
    for f in fears:
        row = plant(
            text=f["text"], watch=f["watch"], source="first_authorship",
            fear_id=f["id"], root=root,
        )
        if row is not None:
            planted.append(row.id)
    out.update({
        "fears_planted": len(planted), "planted_ids": planted,
        "zero_is_honest": len(planted) == 0,
        "gate": {"approved": True, "reason": verdict.reason},
    })
    return out


async def _main() -> int:
    from ai.narrative_self import narrative_author_llm_call, narrative_gate_llm_call

    result = await run_first_authorship(
        llm_call=narrative_author_llm_call,
        gate_llm_call=narrative_gate_llm_call,
    )
    print("fears first authorship:")
    for key, value in result.items():
        print(f"  {key}: {value}")
    if result.get("refused"):
        return 2
    if result.get("error"):
        return 1
    if result.get("fears_planted", 0) == 0:
        print("  → the book is empty, and the empty book is honest (decision 5).")
    else:
        print("  → her book exists; read it with: venv/bin/python scripts/fears_peek.py")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(_main()))
