#!/usr/bin/env python3
"""Peek at PaLLi's fearbook — read-only, zero live-path risk (step 24).

Usage (from the repo root):
    venv/bin/python scripts/fears_peek.py           # held fears + counts
    venv/bin/python scripts/fears_peek.py --all     # include faced/released history

Reads the append-only jsonl through the store's own fold (never writes;
the store only appends on plant/touch/face/release, none of which this
script calls). An absent book is the honest empty book (decision 5), not
an error: zero fears is a valid state at every point in the organ's life.
Honors FEARS_ROOT the same way the store does.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ai.fear_store import _events_path, load_fears  # noqa: E402


def _age(ts: float) -> str:
    seconds = max(0.0, time.time() - float(ts))
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Peek at PaLLi's fearbook (read-only)."
    )
    parser.add_argument("--all", action="store_true",
                        help="include faced/released history")
    args = parser.parse_args()

    path = _events_path(None)
    fears = load_fears()
    if not fears:
        where = "no book on disk" if not path.exists() else f"empty book at {path}"
        print(f"Her fearbook holds nothing ({where}).")
        print("The empty book is honest — zero fears is a valid state (decision 5).")
        return 0

    held = sorted((f for f in fears.values() if f.status == "held"),
                  key=lambda f: f.planted_at)
    closed = sorted((f for f in fears.values() if f.status != "held"),
                    key=lambda f: f.planted_at)

    print(f"Her fearbook ({path}) — {len(held)} held, "
          f"{len(closed)} faced/released:\n")
    for f in held:
        touched = (
            f"touched {f.touches}x, last {_age(f.last_touched_at)} ago"
            if f.touches else "never touched"
        )
        print(f"  [{f.id}] held {_age(f.planted_at)} · {f.source} · {touched}")
        print(f"      {f.text}")
        print(f"      she watches for: {f.watch}\n")

    if args.all and closed:
        print("— the rest of the book (nothing deletes; her words closed them):")
        for f in closed:
            print(f"  [{f.id}] {f.status} · planted {_age(f.planted_at)} ago")
            print(f"      {f.text}")
            for h in f.history:
                print(f"      {h['op']} ({time.strftime('%Y-%m-%d', time.localtime(h['at']))}): {h['note']}")
            print()
    elif closed and not args.all:
        print(f"(+{len(closed)} faced/released — --all shows them)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
