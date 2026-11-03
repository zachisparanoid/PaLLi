#!/usr/bin/env python
"""Regenerate a lost nightly reflection from that date's surviving material.

Built for R2's last piece (2026-08-05): the 2026-08-02 reflection was cut to a
54-char mid-clause fragment by the D1 reasoning burn and stored as a success.
Its inputs survive, so it can be honestly regenerated.

Usage:
  venv/bin/python scripts/reflection_backfill.py --date 2026-08-02          # dry-run (default)
  venv/bin/python scripts/reflection_backfill.py --apply-from <candidate>   # replay the REVIEWED text

The ritual, enforced in code:
  * dry-run gathers material FOR THE TARGET DATE (the live engine's gather
    reads monologue "recent relative to now" — reusing it would poison a
    backfill with today's thoughts), makes ONE LLM call, writes the candidate
    to evals/consolidation/, and touches no store;
  * --apply-from requires a *.sqlite3 backup newer than 12h and replays the
    reviewed candidate VERBATIM — zero LLM calls;
  * the store appends and readers take the last line, so the fragment stays in
    the file as the scar's own record, and the new entry carries provenance
    meta (backfilled / replaced_fragment / RECONSTRUCTED note) so a
    reconstruction can never be mistaken for a memory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date as _date
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_MONOLOGUE_ROOT = REPO_ROOT / "data" / "inner_monologue"
DEFAULT_STORE_ROOT = None  # night_cycle_store's own default (data/night_cycle)
DEFAULT_OUT_DIR = REPO_ROOT / "evals" / "consolidation"
DEFAULT_BACKUP_DIR = Path.home() / "projects" / "chatbot" / "backups"
BACKUP_MAX_AGE_S = 12 * 3600


async def _default_day_log(owner_user_id: int, day: str) -> Optional[Dict[str, Any]]:
    from core.memory_service import memory_workspace_service

    return await memory_workspace_service.get_day(owner_user_id=owner_user_id, day=day)


async def gather_material_for_date(
    *,
    owner_id: int,
    date: str,
    monologue_root: Optional[Path] = None,
    day_log_fn: Optional[Callable[[int, str], Awaitable[Any]]] = None,
) -> str:
    """Date-scoped twin of ai.night_cycle._gather_day_material. Same sections,
    same section headers, but the monologue half reads the per-date file
    instead of "recent relative to now". Never raises; "" on total failure."""
    parts: List[str] = []
    day_log_fn = day_log_fn or _default_day_log
    try:
        day = await day_log_fn(int(owner_id), date)
        rendered = str((day or {}).get("markdown") or "").strip()
        if rendered:
            parts.append("## The day with him\n" + rendered[:6000])
    except Exception:
        pass
    try:
        root = Path(monologue_root or DEFAULT_MONOLOGUE_ROOT)
        filepath = root / f"{date}.jsonl"
        thoughts: List[str] = []
        if filepath.exists():
            for line in filepath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                thought = str(entry.get("thought") or "").strip()
                if thought:
                    thoughts.append(f"- {thought}")
        if thoughts:
            parts.append(
                "## My own quiet thoughts today\n" + "\n".join(thoughts)[:4000]
            )
    except Exception:
        pass
    return "\n\n".join(p for p in parts if p.strip())


def _render_prompt(material: str) -> str:
    try:
        from ai.prompt_loader import prompt_loader

        template = prompt_loader.load_prompt("night_cycle_daily_meaning")
        if template:
            return template.replace("<<DAY_MATERIAL>>", material)
    except Exception:
        pass
    return material


async def run_dryrun(
    *,
    owner_id: int,
    date: str,
    monologue_root: Optional[Path] = None,
    day_log_fn: Optional[Callable[[int, str], Awaitable[Any]]] = None,
    llm_call: Callable[[str], Awaitable[str]],
    store_root: Optional[Path] = None,  # recorded only; dry-run never touches it
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    material = await gather_material_for_date(
        owner_id=owner_id, date=date,
        monologue_root=monologue_root, day_log_fn=day_log_fn,
    )
    if not material:
        print(f"[{date}] no surviving material — nothing to regenerate honestly")
        return {"candidate_path": "", "text": "", "gathered_chars": 0}
    text = (await llm_call(_render_prompt(material)) or "").strip()
    if not text:
        print(f"[{date}] LLM returned empty — no candidate written")
        return {"candidate_path": "", "text": "", "gathered_chars": len(material)}
    out = Path(out_dir or DEFAULT_OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = out / f"{ts}-reflection-dryrun.json"
    path.write_text(
        json.dumps(
            {"date": date, "text": text, "gathered_chars": len(material)},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[{date}] candidate ({len(text)} chars) -> {path}")
    print("---")
    print(text)
    return {"candidate_path": str(path), "text": text, "gathered_chars": len(material)}


def apply_candidate(
    *,
    candidate_path: Path,
    store_root: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
    owner_id: int,
) -> Dict[str, Any]:
    """Replay a reviewed candidate verbatim. Gated on a fresh backup; appends
    with full provenance; never overwrites the fragment line."""
    from ai.night_cycle_store import read_daily_meaning_for, write_daily_meaning

    backups = Path(backup_dir or DEFAULT_BACKUP_DIR)
    fresh = [
        p for p in backups.glob("*.sqlite3")
        if time.time() - p.stat().st_mtime < BACKUP_MAX_AGE_S
    ]
    if not fresh:
        raise SystemExit(
            f"REFUSING --apply: no *.sqlite3 backup newer than 12h in {backups}."
        )

    payload = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
    date, text = str(payload["date"]), str(payload["text"]).strip()
    if not text:
        raise SystemExit("REFUSING --apply: candidate text is empty.")

    prior = read_daily_meaning_for(date=date, root=store_root)
    replaced = prior.text if prior else ""

    meta = {
        "owner_id": int(owner_id),
        "backfilled": True,
        "backfilled_at": time.time(),
        "backfilled_by": "scripts/reflection_backfill.py",
        "backfill_note": (
            "RECONSTRUCTED, not written that night. The D1 reasoning burn cut "
            f"this date's reflection to a fragment; this entry was regenerated on "
            f"{_date.today().isoformat()} from that date's surviving day-log and "
            "monologue. The original fragment remains as the first line of this "
            "file — the scar's own record."
        ),
        "replaced_fragment": replaced,
    }
    path = write_daily_meaning(date=date, text=text, meta=meta, root=store_root)
    print(f"[{date}] applied ({len(text)} chars, fragment preserved) -> {path}")
    return {"applied": True, "path": str(path), "replaced_fragment": replaced}


async def _amain(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--date", default="2026-08-02")
    ap.add_argument("--apply-from", dest="apply_from", default="")
    ap.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    args = ap.parse_args(argv)

    from ai.night_cycle import _daily_meaning_llm_call, _resolve_owner_id

    owner_id = await _resolve_owner_id()
    if args.apply_from:
        apply_candidate(
            candidate_path=Path(args.apply_from).expanduser(),
            backup_dir=Path(args.backup_dir).expanduser(),
            owner_id=owner_id,
        )
        return
    await run_dryrun(
        owner_id=owner_id, date=args.date, llm_call=_daily_meaning_llm_call,
    )


if __name__ == "__main__":
    asyncio.run(_amain())
