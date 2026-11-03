#!/usr/bin/env python3
"""Consolidation backfill driver — Wave 1a (spec §4.4).

Walks the archive day by day calling consolidate_day, then runs ONE
age_facts pass at the end (aging thresholds are now-relative; a per-day
sweep is incoherent). NEVER runs reconcile_day: replaying history day by
day would let a May day "correct" facts written in July (the time-travel
hazard) — reconciliation is a go-forward nightly job only.

Usage:
  venv/bin/python scripts/consolidation_backfill.py                 # dry-run (default; writes NOTHING)
      --apply                                                       # real writes (gated, below)
      --from 2026-04-25 --to 2026-07-14                             # window override
      --resume                                                      # skip checkpointed days
      --db path/to/copy.sqlite3                                     # sandbox target (ritual step 4) — gates skipped
      --backup-dir ~/projects/chatbot/backups                       # where --apply looks for a fresh backup
      --max-age-rewrites 1000

Gates on --apply against the LIVE record (no --db):
  (a) a *.sqlite3 backup newer than 12h must exist in --backup-dir;
  (b) a completed --dry-run for the SAME window must be checkpointed.

Artifacts: evals/consolidation/<ts>-dryrun.{md,json} (the full would-be
diff, for the owner to read BEFORE anything is real) and
<ts>-apply-manifest.json (every gist + supersede pair — the rollback map).
Checkpoint: data/consolidation/.backfill_state.json. All gitignored: they
carry her memory content and never enter history.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_FROM = "2026-04-25"  # first conversation row on file (verified 2026-07-15)
DEFAULT_BACKUP_DIR = Path.home() / "projects" / "chatbot" / "backups"
DEFAULT_OUT_DIR = REPO_ROOT / "evals" / "consolidation"
DEFAULT_STATE = REPO_ROOT / "data" / "consolidation" / ".backfill_state.json"


@dataclass
class BackfillDeps:
    """Everything injectable so tests (and the sandbox rehearsal) never touch
    live stores."""

    brain: Any
    owner_id: int
    day_llm: Callable
    fact_llm: Callable
    out_dir: Path = DEFAULT_OUT_DIR
    state_path: Path = DEFAULT_STATE
    gist_root: Optional[Path] = None  # None => ai.day_gist_store.DAY_GIST_DIR


def sandbox_paths(db_path: str) -> tuple:
    """Per-target file stores for a --db rehearsal (review fix): the sandbox
    must isolate ALL THREE stores — DB, gist dir, checkpoint — or a rehearsal
    contaminates the live record and pre-satisfies the live gates."""
    base = Path(db_path)
    return (
        base.with_name(base.stem + ".day_gists"),
        base.with_name(base.stem + ".backfill_state.json"),
    )


def backup_is_fresh(backup_dir: Path, *, max_age_hours: float = 12.0) -> bool:
    """The ritual, enforced in code: --apply refuses without a recent backup."""
    try:
        newest = max(
            (p.stat().st_mtime for p in Path(backup_dir).glob("*.sqlite3")),
            default=0.0,
        )
    except OSError:
        return False
    return newest > 0.0 and (time.time() - newest) <= max_age_hours * 3600.0


def load_checkpoint(state_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_checkpoint(state_path: Path, state: Dict[str, Any]) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _walk_days(from_day: str, to_day: str) -> List[str]:
    start = date.fromisoformat(from_day)
    end = date.fromisoformat(to_day)
    out: List[str] = []
    cursor = start
    while cursor <= end:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


async def run_backfill(
    deps: BackfillDeps,
    *,
    from_day: str,
    to_day: str,
    apply: bool,
    live_target: bool = False,
    resume: bool = False,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    max_age_rewrites: int = 1000,
) -> Dict[str, Any]:
    from ai.consolidation import age_facts, consolidate_day
    from ai.day_gist_store import write_day_gist
    from ai.inner_memory_index import index_day_gist

    window = f"{from_day}..{to_day}"

    if not live_target:
        # Review fix: a sandbox run must never touch the live checkpoint (it
        # would pre-satisfy the live gates) nor, when applying, the live gist
        # dir. Refuse rather than trust the caller wired isolation.
        if Path(deps.state_path) == Path(DEFAULT_STATE):
            print(
                "REFUSING sandbox run against the LIVE checkpoint "
                f"({DEFAULT_STATE}) — pass an isolated state_path "
                "(the CLI derives one from --db).",
                file=sys.stderr,
            )
            raise SystemExit(2)
        if apply and deps.gist_root is None:
            print(
                "REFUSING sandbox --apply against the LIVE day-gist store — "
                "pass an isolated gist_root (the CLI derives one from --db).",
                file=sys.stderr,
            )
            raise SystemExit(2)

    state = load_checkpoint(deps.state_path)

    if apply and live_target:
        if not backup_is_fresh(backup_dir):
            print(
                f"REFUSING --apply: no *.sqlite3 backup newer than 12h in {backup_dir}.\n"
                "Take the backup first — that ritual is the rollback story.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        if state.get("dry_run_done_for") != window:
            print(
                f"REFUSING --apply: no completed --dry-run checkpointed for {window} "
                f"(found: {state.get('dry_run_done_for') or 'none'}). Run the dry-run, "
                "read the report, then apply.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    done_days = set(state.get("days_done") or []) if (resume and apply) else set()
    now = time.time()
    llm_calls = {"day_gist": 0, "fact_gist": 0}

    def counted(fn, key):
        async def _wrapped(prompt):
            llm_calls[key] += 1
            return await fn(prompt)

        return _wrapped

    day_llm = counted(deps.day_llm, "day_gist")
    fact_llm = counted(deps.fact_llm, "fact_gist")

    # Writers: real for apply, collectors for dry-run (zero writes BY
    # CONSTRUCTION — the llm runs, the pens are swapped out).
    day_gists: List[Dict[str, Any]] = []
    if apply:

        def store_write(*, day, text, meta):
            path = write_day_gist(day=day, text=text, meta=meta, root=deps.gist_root)
            day_gists.append({"day": day, "chars": len(text), "entry_id": f"day_gist:{day}"})
            return path

        async def index_write(*, day, text, source_path=""):
            await index_day_gist(
                day=day, text=text, source_path=source_path, brain_client=deps.brain
            )

        async def supersede(**kwargs):
            return await deps.brain.supersede_user_memory_with_gist(**kwargs)

    else:

        def store_write(*, day, text, meta):
            day_gists.append({"day": day, "chars": len(text), "text": text, "meta": meta})
            return Path(f"(dry-run)/{day}.jsonl")

        async def index_write(*, day, text, source_path=""):
            return None

        async def supersede(**kwargs):
            return None

    days = _walk_days(from_day, to_day)
    results: List[Any] = []
    skipped_resume = 0
    for day in days:
        if day in done_days:
            skipped_resume += 1
            continue
        r = await consolidate_day(
            owner_id=deps.owner_id,
            day=day,
            llm_call=day_llm,
            now=now,
            brain_client=deps.brain,
            store_write=store_write,
            index_write=index_write,
        )
        results.append(r)
        if apply and (r.wrote or r.skipped):
            done_days.add(day)
            state.update({"window": window, "days_done": sorted(done_days)})
            save_checkpoint(deps.state_path, state)
        print(f"[{day}] " + (f"gist {r.chars} chars" if r.wrote else (r.reason or r.error or "?")))

    aging = await age_facts(
        owner_id=deps.owner_id,
        now=now,
        llm_call=fact_llm,
        max_rewrites=max_age_rewrites,
        brain_client=deps.brain,
        supersede=supersede,
    )

    if apply and live_target and aging.pairs:
        # Review fix: superseded facts must leave the workspace index NOW,
        # not at the next TTL-debounced rebuild.
        try:
            from core.memory_service import memory_workspace_service

            await memory_workspace_service.rebuild_index(owner_user_id=deps.owner_id)
            print("[index] workspace index rebuilt after aging")
        except Exception as exc:
            print(f"[index] rebuild failed (run scripts side rebuild later): {exc}", file=sys.stderr)

    deps.out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    wrote_days = [r for r in results if r.wrote]
    summary = {
        "mode": "apply" if apply else "dry_run",
        "window": window,
        "days_walked": len(days),
        "days_skipped_resume": skipped_resume,
        "day_gists_proposed": len(wrote_days),
        "aging_proposed": len(aging.proposals),
        "aging_applied": aging.rewritten,
        "llm_calls": dict(llm_calls),
        "errors": [r.day for r in results if r.error] + ([aging.error] if aging.error else []),
    }

    if apply:
        manifest = {
            **summary,
            "day_gists": day_gists,
            "supersedes": [{"old_id": old, "new_id": new} for old, new in aging.pairs],
        }
        (deps.out_dir / f"{ts}-apply-manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        state.update({"window": window, "days_done": sorted(done_days), "aging_done": True})
        save_checkpoint(deps.state_path, state)
    else:
        lines = [
            f"# Consolidation dry-run — {window}",
            "",
            "Every gist and every aging below is a PROPOSAL. Nothing was written.",
            "",
        ]
        for r in results:
            if r.wrote:
                lines += [f"## {r.day}", "", r.gist, ""]
            elif r.error:
                lines += [f"## {r.day} — ERROR: {r.error}", ""]
        if aging.proposals:
            lines += ["# Proposed fact aging (old → gist)", ""]
            for p in aging.proposals:
                lines += [
                    f"- **{p['old_id']}** ({p['from_day']}):",
                    f"  - old: {p['old_content']}",
                    f"  - gist: {p['gist']}",
                    "",
                ]
        lines += ["---", f"~{sum(llm_calls.values())} LLM calls " + json.dumps(llm_calls)]
        (deps.out_dir / f"{ts}-dryrun.md").write_text("\n".join(lines), encoding="utf-8")
        (deps.out_dir / f"{ts}-dryrun.json").write_text(
            json.dumps(
                {**summary, "day_gists": day_gists, "aging_proposals": aging.proposals},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        if summary["errors"]:
            # Review fix: an errored dry-run produced no reviewable diff for
            # those days — it must NOT unlock --apply.
            print(
                f"dry-run had errors ({summary['errors']}) — apply gate NOT "
                "stamped; re-run the dry-run clean first.",
                file=sys.stderr,
            )
        else:
            state.update({"dry_run_done_for": window})
        save_checkpoint(deps.state_path, state)

    print(json.dumps(summary, indent=2))
    return summary


async def run_replay(
    deps: BackfillDeps,
    *,
    dryrun_json: Path,
    live_target: bool = False,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> Dict[str, Any]:
    """Apply EXACTLY what the owner reviewed (review fix: plain --apply
    regenerated everything at temperature 0.6, so the reviewed report had no
    binding relation to what landed). Zero LLM calls: day-gists and aging
    gists are taken verbatim from the dry-run JSON; any fact whose content
    changed since the dry-run is skipped as drift."""
    from ai.day_gist_store import write_day_gist
    from ai.fact_retrieval import get_user_facts_raw
    from ai.inner_memory_index import index_day_gist

    if live_target and not backup_is_fresh(backup_dir):
        print(
            f"REFUSING replay: no *.sqlite3 backup newer than 12h in {backup_dir}.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    payload = json.loads(Path(dryrun_json).read_text(encoding="utf-8"))
    window = str(payload.get("window") or "")
    state = load_checkpoint(deps.state_path)
    done_days = set(state.get("days_done") or [])

    gists_written: List[Dict[str, Any]] = []
    for entry in payload.get("day_gists") or []:
        day = str(entry.get("day") or "")
        text = str(entry.get("text") or "")
        if not day or not text:
            continue
        path = write_day_gist(day=day, text=text, meta=dict(entry.get("meta") or {}),
                              root=deps.gist_root)
        await index_day_gist(day=day, text=text, source_path=str(path),
                             brain_client=deps.brain)
        gists_written.append({"day": day, "chars": len(text), "entry_id": f"day_gist:{day}"})
        done_days.add(day)
        print(f"[{day}] replayed gist {len(text)} chars")

    rows = {f["id"]: f for f in await get_user_facts_raw(user_id=deps.owner_id,
                                                          brain_client=deps.brain)}
    pass_day = time.strftime("%Y-%m-%d")
    supersedes: List[Dict[str, Any]] = []
    skipped_drift = 0
    for proposal in payload.get("aging_proposals") or []:
        old_id = int(proposal.get("old_id") or 0)
        gist = str(proposal.get("gist") or "")
        current = rows.get(old_id)
        if not gist or current is None:
            skipped_drift += 1
            continue
        if (str(current.get("content") or "") != str(proposal.get("old_content") or "")
                or str(current.get("status") or "active") != "active"):
            print(f"[aging {old_id}] SKIPPED — content/status drifted since the dry-run",
                  file=sys.stderr)
            skipped_drift += 1
            continue
        new_id = await deps.brain.supersede_user_memory_with_gist(
            user_id=deps.owner_id, memory_id=old_id, gist_content=gist,
            source_detail={"consolidated_from": old_id, "pass_day": pass_day,
                           "replayed_from": Path(dryrun_json).name},
        )
        if new_id:
            supersedes.append({"old_id": old_id, "new_id": int(new_id)})

    if live_target and supersedes:
        try:
            from core.memory_service import memory_workspace_service

            await memory_workspace_service.rebuild_index(owner_user_id=deps.owner_id)
            print("[index] workspace index rebuilt after replayed aging")
        except Exception as exc:
            print(f"[index] rebuild failed (run a manual rebuild later): {exc}", file=sys.stderr)

    deps.out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    summary = {
        "mode": "replay",
        "source_report": Path(dryrun_json).name,
        "window": window,
        "day_gists_applied": len(gists_written),
        "aging_applied": len(supersedes),
        "aging_skipped_drift": skipped_drift,
        "llm_calls": {},
    }
    (deps.out_dir / f"{ts}-apply-manifest.json").write_text(
        json.dumps({**summary, "day_gists": gists_written, "supersedes": supersedes},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    state.update({"window": window, "days_done": sorted(done_days), "aging_done": True})
    save_checkpoint(deps.state_path, state)
    print(json.dumps(summary, indent=2))
    return summary


async def _amain(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply", action="store_true", help="real writes (default is dry-run)")
    ap.add_argument("--from", dest="from_day", default=DEFAULT_FROM)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ap.add_argument("--to", dest="to_day", default=yesterday)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--db", default="", help="sandbox DB copy (ritual step 4); live-only gates skipped, file stores isolated per-target")
    ap.add_argument("--apply-from", dest="apply_from", default="",
                    help="replay a reviewed dry-run JSON verbatim (zero LLM calls)")
    ap.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    ap.add_argument("--max-age-rewrites", type=int, default=1000)
    args = ap.parse_args(argv)

    from ai.consolidation import day_gist_llm_call, fact_gist_llm_call
    from ai.night_cycle import _resolve_owner_id

    if args.db:
        from core.database import PalliBrain

        brain = PalliBrain(str(args.db))
        await brain.initialize()
        live_target = False
        gist_root, state_path = sandbox_paths(args.db)
        print(f"[sandbox] gist store → {gist_root}\n[sandbox] checkpoint → {state_path}")
    else:
        from core.database import brain

        live_target = True
        gist_root, state_path = None, DEFAULT_STATE

    owner_id = await _resolve_owner_id()
    deps = BackfillDeps(
        brain=brain, owner_id=owner_id, day_llm=day_gist_llm_call,
        fact_llm=fact_gist_llm_call, gist_root=gist_root, state_path=state_path,
    )
    try:
        if args.apply_from:
            await run_replay(
                deps,
                dryrun_json=Path(args.apply_from).expanduser(),
                live_target=live_target,
                backup_dir=Path(args.backup_dir).expanduser(),
            )
        else:
            await run_backfill(
                deps,
                from_day=args.from_day,
                to_day=args.to_day,
                apply=args.apply,
                live_target=live_target,
                resume=args.resume,
                backup_dir=Path(args.backup_dir).expanduser(),
                max_age_rewrites=args.max_age_rewrites,
            )
    finally:
        if args.db:
            await brain.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
