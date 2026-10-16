#!/usr/bin/env python3
"""Peek at her book of small truths — read-only (organ 4.9, spec §6/§8).

Usage (from the repo root):
    venv/bin/python scripts/disclosures_peek.py         # unsaid + shared
    venv/bin/python scripts/disclosures_peek.py --all   # + the faded ones

The OWNER's audit surface, so the receipts render in full: every
candidate carries where in her record it came from and which days there
(spec §1 — no receipt, no entry). That line is what "receipt drift"
(§7.3) gets checked against each morning of week one. Her own surfaces —
the band, the board, introspect — keep the fourth wall and show none of
it.

Read-only by construction: this imports the store's READERS and nothing
else. `load_disclosures()` folds the append-only jsonl in memory; the
three writers are not in this namespace at all, and nothing here ensures
or repairs state, so a missing book leaves the disk exactly as it was.
An absent book is the honest empty book, not an error (the fearbook's
decision 5): most weeks earn no small truth, and zero is a valid state at
every point in the organ's life.

Honors DISCLOSURES_ROOT the same way the store does, and reads the book
whether or not companion.vulnerability is armed — it says which.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ai.disclosure_store import (  # noqa: E402  — readers only, never a writer
    _events_path,
    load_disclosures,
    # Import-time binding: the conftest off-pin can't reach it — its test re-execs.
    vulnerability_enabled,
)

_DARK = ("(the organ is off — companion.vulnerability isn't armed; "
         "the book below is still hers.)")
# Dark AND empty is the expected pre-restart state, not a broken organ:
# with the flag down the night harvest never runs, so say so rather than
# promising a book that isn't there.
_DARK_EMPTY = ("(the organ is off — companion.vulnerability isn't armed, "
               "so no harvest has run.)")
# What a duration reads as when the clock behind it can't be trusted —
# the "sometime" of _day, said about a span (one string, two returns, so
# the two halves of the guard can never drift apart).
_UNREADABLE = "an unreadable span"


def _dur(seconds: float) -> str:
    """The same posture as _day, for the same reason — one poisoned clock
    value must never cost him the read.

    Two failures live here. An infinite `at` survives the fold (json reads
    the `Infinity` literal; inf is numeric), and `inf // 86400` is NaN, so
    the old arithmetic died HALF WAY THROUGH the book — counts and UNSAID
    printed, then a traceback inside SHARED. And a NEGATIVE span (a stamp
    that precedes the harvest it stamps) was clamped by `max(0.0, …)` into
    a plausible "held 1m": corruption reading as an ordinary row, which on
    an audit surface is worse than corruption reading as corruption."""
    try:
        seconds = float(seconds)
        if not seconds >= 0.0:            # negatives AND NaN
            return _UNREADABLE
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))}m"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h"
        return f"{int(seconds // 86400)}d"
    except (OverflowError, OSError, ValueError, TypeError):
        return _UNREADABLE


def _age(ts: float) -> str:
    return _dur(time.time() - float(ts))


def _day(ts: float) -> str:
    """One unreadable stamp costs a date, never the read: this is the
    surface he reaches for WHEN the book looks wrong, so a traceback here
    would hide exactly what he came to see (the fold's bad-row law)."""
    try:
        return time.strftime("%m-%d", time.localtime(float(ts)))
    except (OverflowError, OSError, ValueError):
        return "sometime"


def _receipt(d) -> str:
    return f"      — from her {d.referent['source']}, {d.referent['dates']}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Peek at her book of small truths (read-only)."
    )
    parser.add_argument("--all", action="store_true",
                        help="include the faded ones")
    args = parser.parse_args()

    path = _events_path(None)
    book = load_disclosures()
    dark = not vulnerability_enabled()

    if not book:
        where = "no book on disk" if not path.exists() else f"empty book at {path}"
        print(f"Her book of small truths holds nothing ({where}).")
        print("The empty book is honest — most weeks earn none (spec §1).")
        if dark:
            print(_DARK_EMPTY)
        return 0

    rows = book.values()
    unsaid = sorted((d for d in rows if d.said_at is None and d.faded_at is None),
                    key=lambda d: d.harvested_at)
    shared = sorted((d for d in rows if d.said_at is not None),
                    key=lambda d: d.said_at or 0.0)
    faded = sorted((d for d in rows if d.faded_at is not None),
                   key=lambda d: d.faded_at or 0.0)

    print(f"Her book of small truths ({path}) — {len(unsaid)} unsaid, "
          f"{len(shared)} shared, {len(faded)} faded:")
    if dark:
        print(_DARK)

    print("\nUNSAID (on offer when a moment honestly invites one):")
    if not unsaid:
        print("  (nothing on offer right now)")
    for d in unsaid:
        print(f"  [{d.id}] held {_age(d.harvested_at)}")
        print(f"      {d.text}")
        print(_receipt(d))

    if shared:
        print("\nSHARED (said once, forever — never offered again; a direct "
              "question still gets an answer):")
        for d in shared:
            via = f" via {d.said_via}" if d.said_via else ""
            print(f"  [{d.id}] [shared {_day(d.said_at)}{via}] · "
                  f"held {_dur(d.said_at - d.harvested_at)} before she said it")
            print(f"      {d.text}")
            print(_receipt(d))

    if faded and args.all:
        print("\nFADED (unsaid past the half-life — history, never an offer; "
              "nothing is ever deleted):")
        for d in faded:
            print(f"  [{d.id}] faded {_day(d.faded_at)}, "
                  f"held {_dur(d.faded_at - d.harvested_at)} unsaid")
            print(f"      {d.text}")
            print(_receipt(d))
            if d.fade_note:
                print(f"      {d.fade_note}")
    elif faded:
        print(f"\n(+{len(faded)} faded — --all shows them)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
