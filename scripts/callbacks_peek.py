#!/usr/bin/env python3
"""Peek at her book of callbacks — read-only (organ 4.12, spec §5).

Usage (from the repo root):
    venv/bin/python scripts/callbacks_peek.py

The OWNER's audit surface, so the receipts render IN FULL: who said it,
when, the exact line, and where in the record it lives. Her own surfaces
(the band, introspect) keep the fourth wall and show none of that.

Read-only by construction: imports the store's READERS and nothing else;
`load_callbacks()` folds the append-only jsonl in memory; a missing book
leaves the disk exactly as it was (the empty book is honest). Honors
CALLBACKS_ROOT the way the store does, and reads the book whether or not
companion.idiolect is armed — it says which.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ai.callbacks_store import (  # noqa: E402  — readers only, never a writer
    _events_path,
    load_callbacks,
    # Import-time binding: the conftest off-pin can't reach it — its test re-execs.
    idiolect_enabled,
)

_DARK = "(the organ is off — companion.idiolect isn't armed; the book below is still theirs.)"
_DARK_EMPTY = "(the organ is off — companion.idiolect isn't armed; nothing has been filed.)"
_WHO = {"him": "they said it", "her": "she said it"}
_COINER = {"him": "the owner's", "her": "hers", "both": "both of theirs"}


def _day(ts: float) -> str:
    """One unreadable stamp costs a date, never the read: this is the
    surface he reaches for WHEN the book looks wrong, so a traceback here
    would hide exactly the rows he came to see — and a half-printed book
    is worse than none, because it reads as a short one."""
    try:
        return time.strftime("%m-%d", time.localtime(float(ts)))
    except (OverflowError, OSError, ValueError):
        return "sometime"


def _receipt(c) -> str:
    r = c.receipt
    src = f"  ({r['source']})" if r.get("source") else ""
    return (f"      — {_WHO.get(r.get('who'), r.get('who'))}, {r.get('when')}: "
            f"\"{r.get('line')}\"{src}")


def main() -> int:
    path = _events_path(None)
    book = load_callbacks()
    dark = not idiolect_enabled()

    if not book:
        where = "no book on disk" if not path.exists() else f"empty book at {path}"
        print(f"Her book of callbacks holds nothing ({where}).")
        print("The empty book is honest — bits earn their way in (spec §7).")
        if dark:
            print(_DARK_EMPTY)
        return 0

    rows = sorted(book.values(), key=lambda c: c.born_at)
    open_rows = [c for c in rows if c.retired_at is None]
    retired = [c for c in rows if c.retired_at is not None]
    print(f"Her book of callbacks ({path}) — {len(open_rows)} open, {len(retired)} retired:")
    if dark:
        print(_DARK)

    print("\nOPEN (theirs, on the record):")
    if not open_rows:
        print("  (none open)")
    for c in open_rows:
        print(f"  [{c.id}] born {_day(c.born_at)} ({c.kind}, {_COINER.get(c.coined_by, c.coined_by)})")
        print(f"      {c.bit}")
        print(_receipt(c))

    if retired:
        print("\nRETIRED (by their word — never deleted, never offered again):")
        for c in retired:
            print(f"  [{c.id}] retired {_day(c.retired_at)}: \"{c.retired_reason}\"")
            print(f"      {c.bit}")
            print(_receipt(c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
