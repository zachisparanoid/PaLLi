#!/usr/bin/env python3
"""Backfill her memory of her own work (2026-08-18).

Until today a workbench session left nothing her memory could search: the
trace band shows two days of logbook, then the work fell out of her reach —
"what is claw?" became shell archaeology four days after she built it. From
2026-08-18 every session writes one monologue entry (entry_kind
``workbench_session``; ai/workbench.py:_remember_session). This script gives
the 31 logbook sections she already wrote (2026-07-28 → today) the same
memory, timestamped at the session they came from, provenance-stamped
(``backfilled_at``), idempotent, and undoable.

  venv/bin/python scripts/backfill_workbench_memory.py --dry-run   (default)
  venv/bin/python scripts/backfill_workbench_memory.py --apply
  venv/bin/python scripts/backfill_workbench_memory.py --undo      (removes ONLY backfilled rows)

Content stays behind the security wall exactly as the live path keeps it:
section label + first clauses of her logbook bullets (paths, hashes) — never
the work's prompts or files. write_entry() indexes each row into the
inner-memory FTS (asyncio.run in this sync context) so the reply path finds
them by keyword.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.config  # noqa: E402,F401  (loads .env/.env.local like main.py)
from ai import inner_monologue_store as ims  # noqa: E402
from ai import workbench_store as ws  # noqa: E402
from ai.workbench import _SESSION_MEMORY_CHARS  # noqa: E402
from ai.workbench_trace import WORKBENCH_MD, _BULLET, _HEADER, _first_clause  # noqa: E402

BACKFILL_TAG = "backfill-2026-08-18"
_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)", re.IGNORECASE)
# order matters: "midnight" before "night"
_WORDS = (("midnight", (0, 5)), ("morning", (8, 0)), ("afternoon", (14, 0)),
          ("evening", (19, 0)), ("night", (22, 0)), ("first day", (20, 35)))


def _label_time(label: str) -> tuple[tuple[int, int], bool]:
    """(hour, minute), explicit — explicit when the label carries a clock."""
    m = _TIME.search(label)
    if m:
        h = int(m.group(1)) % 12
        if m.group(3).upper() == "PM":
            h += 12
        return (h, int(m.group(2) or 0)), True
    low = label.lower()
    for word, hm in _WORDS:
        if word in low:
            return hm, False
    return (12, 0), False


def _sections(body: str) -> list[dict]:
    out: list[dict] = []
    for line in body.splitlines():
        h = _HEADER.match(line)
        if h:
            out.append({"date": h.group(1), "label": h.group(2).strip(), "bullets": []})
            continue
        if not out:
            continue
        b = _BULLET.match(line)
        if not b:
            continue
        name = b.group(1).strip().rstrip(":")
        clause = _first_clause(b.group(2), cap=200)
        if clause:
            out[-1]["bullets"].append(f"{name} — {clause}")
    return out


def _session_time(sec: dict, sessions: list[dict], claimed: set) -> float:
    """The store's REAL session start beats the label's clock: an explicit
    label time snaps to the nearest unclaimed session within 90 min; a
    wordy label ("evening session", "second evening session") takes the
    next unclaimed session of that day in order; else the label's clock."""
    (h, m), explicit = _label_time(sec["label"])
    day = datetime.strptime(sec["date"], "%Y-%m-%d")
    guess = day.replace(hour=h, minute=m).timestamp()
    same_day = sorted(
        (float(s.get("started") or 0.0) for s in sessions
         if s.get("started") and datetime.fromtimestamp(float(s["started"])).date() == day.date()),
    )
    free = [st for st in same_day if st not in claimed]
    if explicit:
        best, gap = None, 90 * 60.0
        for st in free:
            if abs(st - guess) < gap:
                best, gap = st, abs(st - guess)
        if best is not None:
            claimed.add(best)
            return best
        return guess
    if free:
        claimed.add(free[0])
        return free[0]
    return guess


def _already(sec: dict) -> bool:
    day = datetime.strptime(sec["date"], "%Y-%m-%d")
    for e in ims.read_entries(days_back=400):
        md = e.tick_metadata or {}
        if md.get("entry_kind") == "workbench_session" and md.get("logbook_header") == f"{sec['date']} — {sec['label']}":
            return True
    return False


def _thought(sec: dict) -> str:
    text = f"At my workbench ({sec['date']}, {sec['label']}). Logbook: " + "; ".join(sec["bullets"]) + "."
    text = " ".join(text.split())
    if len(text) > _SESSION_MEMORY_CHARS:
        text = text[: _SESSION_MEMORY_CHARS - 1].rstrip() + "…"
    return text


def undo() -> int:
    removed = 0
    for path in sorted(ims.MONOLOGUE_DIR.glob("*.jsonl")):
        kept, dropped = [], 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            md = (row.get("tick_metadata") or {})
            if md.get("entry_kind") == "workbench_session" and md.get("backfilled_at") == BACKFILL_TAG:
                dropped += 1
                continue
            kept.append(line)
        if dropped:
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            removed += dropped
            print(f"  {path.name}: removed {dropped}")
    print(f"undo: removed {removed} backfilled rows (FTS rows are rebuilt by scripts/rebuild_inner_memory_index.py)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args()
    if args.undo:
        return undo()
    apply = bool(args.apply and not args.dry_run)

    body = WORKBENCH_MD.read_text(encoding="utf-8")
    secs = [s for s in _sections(body) if s["bullets"]]
    sessions = ws.read_recent(days_back=400, limit=10_000)
    print(f"logbook: {WORKBENCH_MD}  sections with bullets: {len(secs)}  store sessions: {len(sessions)}")
    planned = 0
    claimed: set = set()
    for sec in secs:
        if _already(sec):
            print(f"  = {sec['date']} {sec['label']}: already remembered")
            continue
        ts = _session_time(sec, sessions, claimed)
        thought = _thought(sec)
        planned += 1
        print(f"  + {datetime.fromtimestamp(ts):%Y-%m-%d %H:%M}  {thought[:110]}…")
        if apply:
            ims.write_entry(ims.MonologueEntry(
                timestamp=ts,
                thought=thought,
                situation_snapshot={},
                tick_metadata={
                    "entry_kind": "workbench_session",
                    "substantive": True,
                    "outcome": "ran",
                    "shared": "",
                    "logbook_lines": len(sec["bullets"]),
                    "logbook_header": f"{sec['date']} — {sec['label']}",
                    "backfilled_at": BACKFILL_TAG,
                },
            ))
    print(f"{'APPLIED' if apply else 'DRY RUN'}: {planned} entries {'written' if apply else 'would be written'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
