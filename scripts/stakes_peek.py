#!/usr/bin/env python3
"""Peek at PaLLi's stake ledger — read-only, zero live-path risk.

Usage (from the repo root):
    venv/bin/python scripts/stakes_peek.py           # open + recent movements
    venv/bin/python scripts/stakes_peek.py --all     # include full faded/resolved history

Opens the live DB in SQLite read-only mode (mode=ro), so it can never write,
lock-block, or create the table. A missing table just means the ledger hasn't
had its first appraisal yet.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time


def _db_path() -> str:
    # Mirror core.database.resolve_database_path: env DATABASE_PATH, default
    # data/palli-local.sqlite3, anchored to the repo root (this file's parent's
    # parent) — without importing core.config (which loads dotenv, etc.).
    raw = os.environ.get("DATABASE_PATH", "data/palli-local.sqlite3")
    if os.path.isabs(raw):
        return raw
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(repo_root, raw)


def _age(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _fmt_ts(ts) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))


def _col(row, name: str):
    """A column that may be absent on a ledger born before its migration —
    this peek reads live rows and never migrates (mode=ro)."""
    return row[name] if name in row.keys() else None


def _tell(row) -> str:
    """The one marker a row's header line may carry.

    Step 25 · Regret: a voiced regret shows the day it was said (one voicing
    ever). Organ 4.13 · Opinions: a view is LAST-said, not said-once, so it
    shows the most recent `[said MM-DD]` — unless he asked her to drop it,
    and `[held quietly MM-DD]` outranks the tell (spec §0.1: silenced, never
    deleted). Every other kind: "".
    """
    kind = row["kind"]
    if kind == "regret":
        voiced = _col(row, "voiced_at")
        if voiced is not None:
            return " [said to them " + time.strftime("%m-%d", time.localtime(float(voiced))) + "]"
        return ""
    if kind == "view":
        muted = _col(row, "muted_at")
        if muted is not None:
            return " [held quietly " + time.strftime("%m-%d", time.localtime(float(muted))) + "]"
        voiced = _col(row, "voiced_at")
        if voiced is not None:
            return " [said " + time.strftime("%m-%d", time.localtime(float(voiced))) + "]"
    return ""


def _evidence(row) -> str:
    """A view's evidence, in her words — the admission ticket a view cannot
    be filed without (spec §1). View-only, and capped at 240 like the block
    renderer (`ai.stake_ledger.render_stakes_block`)."""
    if row["kind"] != "view":
        return ""
    ev = (_col(row, "evidence") or "").strip()
    return f" — evidence: {ev[:240]}" if ev else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Peek at PaLLi's stake ledger (read-only).")
    parser.add_argument("--all", action="store_true", help="show full closed history")
    args = parser.parse_args()

    path = _db_path()
    if not os.path.isfile(path):
        print(f"No database at {path}")
        return 1
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        print(f"Could not open {path} read-only: {exc}")
        return 1

    try:
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stakes'"
        ).fetchone()
        if table is None:
            print("The stake ledger hasn't been written yet (no `stakes` table).")
            print("It appears after the first post-reply appraisal with companion.stake_ledger on.")
            return 0

        now = time.time()
        open_rows = con.execute(
            "SELECT * FROM stakes WHERE status='open' "
            "ORDER BY (horizon_at IS NULL) ASC, horizon_at ASC, updated_at DESC"
        ).fetchall()

        print(f"── What PaLLi is holding ({len(open_rows)} open) " + "─" * 30)
        if not open_rows:
            print("  (nothing right now)")
        for r in open_rows:
            bits = [f"held {_age(now - r['created_at'])}"]
            if r["horizon_at"] is not None:
                d = r["horizon_at"] - now
                bits.append(f"horizon {'in ' + _age(d) if d >= 0 else _age(-d) + ' overdue'}")
            if r["last_surfaced_at"] is not None:
                bits.append(f"last raised {_age(now - r['last_surfaced_at'])} ago")
            print(f"  [{r['id']}] ({r['kind']}, {', '.join(bits)}){_tell(r)}")
            print(f"      {r['stake']}{_evidence(r)}")

        # Organ 4.13 · Opinions (spec §6): the views filed at a full slate —
        # kept, never rendered to him, waiting for a slot. Mirrors
        # ai.stake_ledger.dormant_views() (status/kind filter, oldest first:
        # the longest wait has most earned a promote) rather than calling it,
        # because that function runs ensure_schema() on a READ-WRITE
        # connection — this peek's whole contract is mode=ro. One deliberate
        # difference: the store takes a LIMIT (default 10); this audit surface
        # shows every waiting view.
        # Printed only when some are waiting.
        dormant = con.execute(
            "SELECT id, stake FROM stakes WHERE status='dormant' AND kind='view' "
            "ORDER BY created_at ASC"
        ).fetchall()
        if dormant:
            print("\n  WAITING (dormant views): filed at a full slate — kept, never displacing")
            for r in dormant:
                print(f"    [{r['id']}] {r['stake']}")

        limit = "" if args.all else " LIMIT 10"
        # 'settled' belongs here: it is a view's ONLY terminal (spec §6 —
        # resolve/fade are kind-guarded away, and views never die of age), and
        # settle stamps resolved_at like the other two. Without it a settled
        # view matches no section and the first settle would read as a deletion.
        closed = con.execute(
            "SELECT * FROM stakes WHERE status IN ('resolved','faded','settled') "
            f"ORDER BY resolved_at DESC{limit}"
        ).fetchall()
        if closed:
            print(f"\n── How things landed (latest {len(closed)}) " + "─" * 28)
            for r in closed:
                print(f"  [{r['id']}] {r['status']} {_fmt_ts(r['resolved_at'])} ({r['kind']})")
                print(f"      was: {r['stake']}")
                if r["resolution"]:
                    print(f"      now: {r['resolution']}")

        c = dict(
            con.execute("SELECT status, COUNT(*) FROM stakes GROUP BY status").fetchall()
        )
        print(f"\n  totals: {c or '(empty)'}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
