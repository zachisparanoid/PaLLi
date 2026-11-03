#!/usr/bin/env python3
"""Batch 5, Task 10 (2026-09-28): her memory of them, in her own voice — once,
over the file as it stands.

MEMORY.md and MEMORY_ARCHIVE.md hold hundreds of lines written as case notes
("User is…", "The user said…", "PaLLi told the user…"). The reflection writes
new lines in PaLLi's own voice from now on (prompts/turn_reflect.txt and the
belt in core/turn/reflect.py); this script brings the existing lines over,
once, with the owner reading the result first.

Run it from the repository root, on the owner's word, after the restart that
ships Batch 5:

  venv/bin/python scripts/memory_first_person.py --owner-id 1 --dry-run
  venv/bin/python scripts/memory_first_person.py --owner-id 1 --apply

--dry-run plans every rewrite — each candidate bullet of BOTH documents goes
to the model in batches of --batch (25) lines under the reflection's own
contract and guard (core/memory_first_person.py) — and writes TWO files
beside each other: the review document docs/rounds/memory-first-person-
diff.md (per bullet its section, the line before and after, or "kept as
written: <reason>"; totals at the top) and the plan behind it,
docs/rounds/memory-first-person-plan.json (every outcome with its document,
section, line index, the line before, the line after and the verdict, plus
the sha256 of each document's content as planned). Nothing else is written.

--apply makes NO model call (ruling R60): it loads the plan the dry run
saved and writes exactly the rewrites the owner read. It errors out plainly
(exit 4, nothing written) when there is no plan, and ABORTS (exit 2,
nothing written) when a document no longer matches the plan's sha256 or
changes between the read and the write (a reflection may have written
meanwhile). Then it writes a plain backup copy beside each changed file
(MEMORY.md.pre-first-person-<YYYY-MM-DD>.bak, never overwriting an earlier
one), writes each changed document through the identity store (reason
"first_person_migration"), and only THEN writes its own record,
docs/rounds/memory-first-person-applied.md, in the review document's
shape. The dry run's two files are never touched by --apply.

--apply --replan is the explicit way to ask the model again: it plans
afresh (a fresh model call), writes a fresh plan file first, then applies
that plan — the owner has not read those lines, so read the applied record
afterwards.

The guard keeps the facts of every line byte-exact — dates, [8-hex] ids,
paths (POSIX, Windows drive, UNC) and file names, URLs, emails, numbers
(money and percent tokens too), double-quoted spans and backtick code
spans — and every name by survival: each capitalized word of the old line
must still be in the new one (a sentence opener may be inflected, ruling
R58) and none may be added mid-sentence. A rewrite that moves any of them
is kept as written and listed with the reason (token kinds and counts,
never a value); a line the model returns unchanged is kept as written too.
A batch whose reply breaks the count contract is retried once, line by
line. An EMPTY reply is the provider failing (chat_completion returns ""
then): its lines are kept as written without a retry, and two empty
replies in a row stop the run (exit 3; --apply --replan then writes
nothing). Nothing is ever deleted. The script cannot run without --dry-run
or --apply.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import memory_first_person as fp  # noqa: E402
from core.memory_first_person import guard  # noqa: E402,F401 — re-exported (ruling R57): the guard test imports it from here

LANE = "reflect_first_person"           # the reflection's own lane — classified PATIENT by its prefix
REASON = "first_person_migration"
ACTOR_ID = "memory_first_person"
DEFAULT_BATCH = 25
DIFF_PATH = REPO_ROOT / "docs" / "rounds" / "memory-first-person-diff.md"
APPLIED_PATH = REPO_ROOT / "docs" / "rounds" / "memory-first-person-applied.md"
PLAN_PATH = REPO_ROOT / "docs" / "rounds" / "memory-first-person-plan.json"
STOP_REASON = "the run stopped after two empty replies in a row"
NO_REPLY_REASON = "no reply from the model"
DOC_NAMES = {"memory": "MEMORY.md", "memory_archive": "MEMORY_ARCHIVE.md"}

# ask(batch) -> ("ok", lines) | ("bad", None): the reply broke the contract
#              | ("empty", None): no reply at all — the provider failing
Ask = Callable[[list[str]], Awaitable[tuple[str, Optional[list[str]]]]]


@dataclass
class Outcome:
    candidate: fp.Candidate
    after: Optional[str] = None      # the accepted rewrite
    reason: Optional[str] = None     # or why the line is kept as written


@dataclass
class DocPlan:
    doc_key: str
    doc: Any
    content: str
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def name(self) -> str:
        return DOC_NAMES.get(self.doc_key, self.doc_key)

    @property
    def rewritten(self) -> int:
        return sum(1 for o in self.outcomes if o.after is not None)

    @property
    def kept(self) -> int:
        return sum(1 for o in self.outcomes if o.after is None)

    def new_content(self) -> str:
        return fp.replace_lines(
            self.content, {o.candidate.index: o.after for o in self.outcomes if o.after is not None}
        )


# --- the pure-ish middle: batches, the retry, the guard ------------------------

async def rewrite_lines(
    lines: Sequence[str], ask: Ask, *, batch_size: int, who: Optional[fp.Person] = None
) -> tuple[list[tuple[Optional[str], Optional[str]]], bool]:
    """(rewrite, None) or (None, reason) for each line, in order, and whether
    the run STOPPED early. ``ask`` takes a batch of lines and answers
    ("ok", lines), ("bad", None) when the reply broke the contract, or
    ("empty", None) when there was no reply at all. A "bad" batch is asked
    again once, one line at a time. An "empty" reply is the provider
    failing: its lines are kept as written with no retry, and the second
    empty reply in a row stops the run — every line after it is kept as
    written with ``STOP_REASON``. A line the model returns unchanged is
    kept as written (it is not a rewrite); every other line passes the guard
    or is kept with the guard's reason."""
    results: list[tuple[Optional[str], Optional[str]]] = []
    empties = 0
    stopped = False

    def _judge(old: str, new: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if new is None:
            return None, NO_REPLY_REASON
        if new == old:
            return None, "the model returned the line unchanged"
        reason = fp.guard_failure(old, new, allowed_names=(who.name,) if who and who.name else ())
        return (new, None) if reason is None else (None, reason)

    size = max(1, batch_size)
    for start in range(0, len(lines), size):
        batch = list(lines[start: start + size])
        if stopped:
            results.extend((None, STOP_REASON) for _ in batch)
            continue
        status, got = await ask(batch)
        if status == "empty":
            empties += 1
            results.extend((None, NO_REPLY_REASON) for _ in batch)
            stopped = empties >= 2
            continue
        empties = 0
        if status == "ok" and got is not None:
            results.extend(_judge(old, new) for old, new in zip(batch, got))
            continue
        # the batch broke the contract: once more, one line at a time
        for line in batch:
            if stopped:
                results.append((None, STOP_REASON))
                continue
            one_status, one = await ask([line])
            if one_status == "empty":
                empties += 1
                stopped = empties >= 2
                results.append((None, NO_REPLY_REASON))
                continue
            empties = 0
            results.append(_judge(line, one[0] if one_status == "ok" and one else None)
                           if one_status == "ok" else (None, "the reply broke the contract"))
    return results, stopped


def render_review(
    *, owner_id: int, mode: str, today: str, plans: Sequence[DocPlan], stopped: bool = False, note: str = "",
) -> str:
    """The review document: totals, then per document and section every
    candidate's line before and after — or why it was kept as written.
    ``note`` is one sentence under the header about where the plan is."""
    total_c = sum(len(p.outcomes) for p in plans)
    total_r = sum(p.rewritten for p in plans)
    total_k = sum(p.kept for p in plans)
    out = [
        f"# Memory in her own voice — {mode} ({today})",
        "",
        f"Owner {owner_id}. Every candidate bullet of {' and '.join(p.name for p in plans)} — a line "
        "that reads as a case note (\"User…\", \"The user…\", or \"PaLLi\" in the third person) — "
        "with its rewrite, or why it was kept as written. Kept byte-exact by the guard: dates, "
        "[8-hex] ids, paths (POSIX, Windows drive, UNC) and file names, URLs, emails, numbers, "
        "double-quoted spans and backtick code spans; every name of the old line must survive in "
        "the new one (a sentence opener may be inflected) and none may be added mid-sentence.",
        "",
    ]
    if note:
        out += [note, ""]
    out += [
        f"Totals — candidates: {total_c} · rewritten: {total_r} · kept as written: {total_k}",
        "",
    ]
    if stopped:
        out += [
            "Stopped after two empty replies in a row — the provider may be down; the lines after that were not asked.",
            "",
        ]
    for plan in plans:
        out.append(f"## {plan.name} — candidates: {len(plan.outcomes)} · rewritten: {plan.rewritten} · kept as written: {plan.kept}")
        out.append("")
        section = None
        for o in plan.outcomes:
            if o.candidate.section != section:
                section = o.candidate.section
                out.append(f"### {section or '(no section)'}")
                out.append("")
            out.append(f"before: {o.candidate.line}")
            if o.after is not None:
                out.append(f"after: {o.after}")
            else:
                out.append(f"kept as written: {o.reason}")
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _counts_line(label: str, candidates: int, rewritten: int, kept: int) -> str:
    return f"{label}: candidates: {candidates} · rewritten: {rewritten} · kept as written: {kept}"


# --- the plan file (ruling R60): what --dry-run saves and --apply applies -------

def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def render_plan(*, owner_id: int, today: str, plans: Sequence[DocPlan], stopped: bool = False) -> dict:
    """The plan as --dry-run saves it and --apply loads it: per document its
    name, the sha256 of its content as planned and every outcome — the
    section, the line index, the line before, the line after (null when
    kept) and the verdict ("rewrite", or "kept" with the reason)."""
    return {
        "owner_id": int(owner_id),
        "today": today,
        "stopped": bool(stopped),
        "documents": {
            plan.doc_key: {
                "name": plan.name,
                "sha256": _sha256(plan.content),
                "outcomes": [
                    {
                        "section": o.candidate.section,
                        "index": o.candidate.index,
                        "before": o.candidate.line,
                        "after": o.after,
                        "verdict": "rewrite" if o.after is not None else "kept",
                        "reason": o.reason,
                    }
                    for o in plan.outcomes
                ],
            }
            for plan in plans
        },
    }


class PlanUnusable(Exception):
    """The plan file cannot be applied; ``exit_code`` says which way: 4 when
    there is no usable plan (run --dry-run first), 2 when a document moved
    since the plan (run --dry-run again)."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def load_plan(path: Path, *, owner_id: int, plans: Sequence[DocPlan]) -> bool:
    """Fill each DocPlan's outcomes from the plan file, checking first that
    the file is there, readable, for this owner, and that every planned
    document is still there with the sha256 the dry run saw — and (ruling
    R66) that every outcome is sound against the document as re-read: the
    required keys present, the index an int inside the document, the line
    at that index the outcome's ``before``, a "rewrite" carrying a line.
    All of it before any backup or write. Returns whether the dry run
    STOPPED early. A document present now but absent from the plan has
    nothing planned for it. Raises PlanUnusable otherwise."""
    if not path.is_file():
        raise PlanUnusable(
            f"no plan to apply: {path} is missing — run --dry-run first, read the review document, then --apply",
            4,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanUnusable(f"the plan at {path} cannot be read ({type(exc).__name__}) — run --dry-run again", 4)
    if not isinstance(data, dict):
        raise PlanUnusable(f"the plan at {path} is not a plan (not a JSON object) — run --dry-run again", 4)
    try:
        plan_owner = int(data.get("owner_id"))
    except (TypeError, ValueError):
        raise PlanUnusable(f"the plan at {path} names no usable owner id ({data.get('owner_id')!r}) — run --dry-run again", 4)
    if plan_owner != int(owner_id):
        raise PlanUnusable(f"the plan at {path} is for owner {plan_owner}, not {owner_id} — run --dry-run again", 4)
    by_key = {plan.doc_key: plan for plan in plans}
    for doc_key, entry in (data.get("documents") or {}).items():
        plan = by_key.get(doc_key)
        name = DOC_NAMES.get(doc_key, doc_key)
        if plan is None:
            raise PlanUnusable(f"{name} was planned but is not there now — run --dry-run again", 2)
        if _sha256(plan.content) != (entry or {}).get("sha256"):
            raise PlanUnusable(
                f"{name} changed since the dry run — run --dry-run again and read the new review document",
                2,
            )
        lines = plan.content.split("\n")  # the split replace_lines writes by
        for position, o in enumerate((entry or {}).get("outcomes") or ()):
            where = f"{name}, outcome {position}"
            if not isinstance(o, dict) or not {"index", "before", "verdict"} <= set(o):
                raise PlanUnusable(f"{where} is missing index, before or verdict — run --dry-run again", 4)
            try:
                index = int(o["index"])
            except (TypeError, ValueError):
                raise PlanUnusable(f"{where} has no usable line index ({o['index']!r}) — run --dry-run again", 4)
            if not 0 <= index < len(lines):
                raise PlanUnusable(
                    f"{where} points at line {index} but {name} has {len(lines)} line(s) — run --dry-run again", 4,
                )
            before = str(o["before"])
            if lines[index].rstrip("\r").strip() != before.strip():
                raise PlanUnusable(
                    f"{where} says line {index} is a different line than {name} holds there — run --dry-run again", 4,
                )
            after = o.get("after")
            if o["verdict"] == "rewrite" and not (isinstance(after, str) and after.strip()):
                raise PlanUnusable(f"{where} is a rewrite with no line after — run --dry-run again", 4)
            plan.outcomes.append(Outcome(
                candidate=fp.Candidate(index=index, section=str(o.get("section") or ""), line=before),
                after=after if o["verdict"] == "rewrite" else None,
                reason=o.get("reason"),
            ))
    return bool(data.get("stopped"))


# --- the thin I/O ---------------------------------------------------------------


def _resolve_person(bundle: Any, *, name: Optional[str], pronouns: Optional[str]) -> fp.Person:
    """--name/--pronouns win; else the owner's profile the way the reflection
    resolves it (from the bundle already read — never a second read); else
    neutral."""
    if name is None or pronouns is None:
        try:
            from core.agent.context_adapter import resolve_owner_person
            found_name, found_pronouns = resolve_owner_person(bundle)
        except Exception:
            found_name, found_pronouns = "", ""
        name = found_name if name is None else name
        pronouns = found_pronouns if pronouns is None else pronouns
    return fp.person(name or "", pronouns or "")


def _load_bundle_and_docs(service: Any, owner_id: int) -> tuple[Any, dict[str, Any]]:
    """One read serves both the documents and the person lookup — never a
    second read, which the apply's moved-document check would mistake for
    a change."""
    bundle = service.compile_for_owner(owner_id=owner_id)
    return bundle, {key: bundle.owner.get(key) for key in DOC_NAMES}


def _load_docs(service: Any, owner_id: int) -> dict[str, Any]:
    return _load_bundle_and_docs(service, owner_id)[1]


def _backup_path(doc_path: Path, today: str) -> Path:
    """``MEMORY.md.pre-first-person-<today>.bak`` beside the file; ``.2``,
    ``.3``… when one already exists — an earlier backup is never overwritten."""
    base = doc_path.with_name(f"{doc_path.name}.pre-first-person-{today}.bak")
    candidate, n = base, 1
    while candidate.exists():
        n += 1
        candidate = base.with_name(f"{base.name}.{n}")
    return candidate


def write_backup(doc: Any, today: str) -> Path:
    """A plain copy of the document's file as it is on disk (its frontmatter
    included); rendered from the loaded document if the file is not there."""
    from core.identity.frontmatter import dump_frontmatter

    doc_path = Path(doc.path)
    target = _backup_path(doc_path, today)
    if doc_path.is_file():
        shutil.copy2(doc_path, target)
    else:
        target.write_text(dump_frontmatter(doc.frontmatter, doc.content or ""), encoding="utf-8")
    return target


async def _amain(
    argv: Optional[Sequence[str]] = None,
    *,
    service: Any = None,
    llm_client: Any = None,
    diff_path: Optional[Path] = None,
    applied_path: Optional[Path] = None,
    plan_path: Optional[Path] = None,
    today: Optional[str] = None,
) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--owner-id", dest="owner_id", type=int, required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="plan every rewrite; write the review document and the plan beside it")
    mode.add_argument("--apply", dest="apply", action="store_true",
                      help="apply the dry run's plan (no model call): back up, then write both documents through the store")
    ap.add_argument("--replan", dest="replan", action="store_true",
                    help="with --apply: ask the model again and write a fresh plan first (the default applies the dry run's plan)")
    ap.add_argument("--batch", dest="batch", type=int, default=DEFAULT_BATCH,
                    help=f"lines per model call (default {DEFAULT_BATCH})")
    ap.add_argument("--name", dest="name", default=None,
                    help="their name, as she should write it (default: the owner's profile, else neutral)")
    ap.add_argument("--pronouns", dest="pronouns", default=None,
                    help="he/him, she/her or they/them (default: the owner's profile or LOCAL_OWNER_PRONOUNS)")
    ap.add_argument("--model", dest="model", default=None,
                    help="the model to ask (default: the reflection's own)")
    args = ap.parse_args(argv)
    if args.batch < 1:
        ap.error("--batch must be at least 1")
    if args.replan and not args.apply:
        ap.error("--replan goes with --apply")

    if service is None:
        import core.config  # noqa: F401 — the same bootstrap as scripts/scrub_memory_secrets.py
        from core.identity.service import identity_service as service
    today = today or time.strftime("%Y-%m-%d")
    diff_path = Path(diff_path) if diff_path else DIFF_PATH
    applied_path = Path(applied_path) if applied_path else APPLIED_PATH
    plan_path = Path(plan_path) if plan_path else PLAN_PATH
    mode_word = "dry run" if args.dry_run else "applied"
    from_plan = args.apply and not args.replan

    bundle, docs = _load_bundle_and_docs(service, args.owner_id)
    who = _resolve_person(bundle, name=args.name, pronouns=args.pronouns)
    print(f"written as: {who.name + ' (' + who.pronouns + ')' if who.name else 'they/them (no name known)'}")
    if docs.get("memory") is None:
        print(f"owner {args.owner_id}: no memory document — nothing to do")
        return 1
    plans = [DocPlan(doc_key=key, doc=doc, content=doc.content or "") for key, doc in docs.items() if doc is not None]

    if from_plan:
        # Ruling R60: --apply applies the diff the owner read — the dry run's
        # plan, checked against both documents as they are now. No model call.
        try:
            stopped = load_plan(plan_path, owner_id=args.owner_id, plans=plans)
        except PlanUnusable as exc:
            print(f"{exc} — nothing written")
            return exc.exit_code
        candidates: list[tuple[DocPlan, fp.Candidate]] = [(plan, o.candidate) for plan in plans for o in plan.outcomes]
        print(f"plan loaded: {plan_path}")
        if stopped:
            print("the dry run stopped early (two empty replies in a row) — its accepted rewrites are applied; "
                  "run --dry-run again later for the lines it never asked")
    else:
        if llm_client is None:
            from ai.llm_client import llm as llm_client
        model = args.model
        if not model:
            from core.turn.reflect import _resolve_reflect_model
            model = _resolve_reflect_model()

        # The plan: every candidate of both documents, in document order.
        candidates = [(plan, cand) for plan in plans for cand in fp.plan_candidates(plan.content)]

        async def ask(lines: list[str]) -> tuple[str, Optional[list[str]]]:
            try:
                raw = await llm_client.chat_completion(
                    messages=fp.build_rewrite_messages(lines, who=who),
                    model=model,
                    temperature=0.1,
                    max_tokens=4000,
                    log_context=LANE,
                )
            except Exception as exc:  # no reply: the line(s) are kept as written; the review says so
                print(f"model call failed for {len(lines)} line(s): {type(exc).__name__}")
                return "empty", None
            if not raw or not str(raw).strip():
                return "empty", None
            parsed = fp.parse_rewrite_reply(raw, len(lines))
            return ("ok", parsed) if parsed is not None else ("bad", None)

        results, stopped = await rewrite_lines([c.line for _p, c in candidates], ask, batch_size=args.batch, who=who)
        for (plan, cand), (after, reason) in zip(candidates, results):
            plan.outcomes.append(Outcome(candidate=cand, after=after, reason=reason))

    for plan in plans:
        print(_counts_line(f"owner {args.owner_id} — {plan.name}", len(plan.outcomes), plan.rewritten, plan.kept))
    print(_counts_line("total", len(candidates), sum(p.rewritten for p in plans), sum(p.kept for p in plans)))

    if stopped and not from_plan:
        print("stopped after two empty replies in a row — the provider may be down; the lines after that were not asked")

    def _write_record(path: Path, note: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_review(owner_id=args.owner_id, mode=mode_word, today=today, plans=plans, stopped=stopped, note=note),
            encoding="utf-8",
        )

    def _write_plan(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(render_plan(owner_id=args.owner_id, today=today, plans=plans, stopped=stopped),
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.dry_run:
        _write_record(
            diff_path,
            f"The plan behind this document is saved beside it as {plan_path.name}; --apply writes exactly "
            "the rewrites listed here, with no new model call (--apply --replan asks the model again).",
        )
        _write_plan(plan_path)
        print(f"review document written: {diff_path}")
        print(f"plan written: {plan_path}")
        print("dry run — nothing else written")
        return 3 if stopped else 0

    if stopped and not from_plan:
        print("nothing written — run it again when the provider answers")
        return 3

    changed = [p for p in plans if p.rewritten]
    if not changed:
        print("nothing to change")
        return 0

    if args.replan:
        # A fresh plan first — the record of what --replan is about to apply.
        _write_plan(plan_path)
        print(f"plan written (--replan): {plan_path}")

    # A reflection may have written between the plan and now: never overwrite it.
    again = _load_docs(service, args.owner_id)
    for plan in plans:
        current = again.get(plan.doc_key)
        if current is None or (current.content or "") != plan.content:
            print(f"{plan.name} changed {'since the dry run' if from_plan else 'while planning'} — nothing written; run it again")
            return 2

    from core.identity.frontmatter import dump_frontmatter
    for plan in changed:
        print(f"backup written: {write_backup(plan.doc, today)}")
    for plan in changed:
        await service.update_raw_document(
            user_id=args.owner_id,
            platform="owner",
            doc_key=plan.doc_key,
            markdown=dump_frontmatter(plan.doc.frontmatter, plan.new_content()),
            actor_type="system",
            actor_id=ACTOR_ID,
            reason=REASON,
            allow_admin=True,
            identity_owner_id=args.owner_id,
        )
        print(f"{plan.name} updated ({plan.rewritten} line(s) rewritten)")
    # The record of what was applied — written last, to its own file; the
    # dry run the owner read stays exactly as it was (fix round 1, M4).
    _write_record(
        applied_path,
        f"Applied from the plan the dry run saved ({plan_path.name}), with no new model call." if from_plan
        else f"Applied from a fresh plan (--replan), saved as {plan_path.name}; the owner did not read these lines first.",
    )
    print(f"record written: {applied_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
