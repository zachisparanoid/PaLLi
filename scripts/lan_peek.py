#!/usr/bin/env python3
"""Peek at PaLLi's device book — read-only, zero live-path risk (step 23).

Usage (from the repo root):
    venv/bin/python scripts/lan_peek.py               # the house right now, then the book as a table
    venv/bin/python scripts/lan_peek.py --book PATH   # another book (tests, backups)

Reads data/lan_presence.json through the module's own loader (never
writes; the engine alone writes the book, and only while
companion.lan_presence is on). An absent book is the honest empty book —
the house hasn't been looked at since boot. Labels come from
data/palli.yaml (companion.lan_devices) when present; everything else is
named by hostname, else by mac tail.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.lan_presence import DEFAULT_BOOK_PATH, device_display, load_book  # noqa: E402


def _labels() -> dict:
    try:
        import yaml

        comp = (yaml.safe_load((REPO_ROOT / "data" / "palli.yaml").read_text(encoding="utf-8")) or {}
                ).get("companion") or {}
        return {str(k): str(v) for k, v in dict(comp.get("lan_devices") or {}).items()}
    except Exception:
        return {}


def _age(ts: float, now: float) -> str:
    seconds = max(0.0, now - float(ts or 0.0))
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def main() -> int:
    parser = argparse.ArgumentParser(description="Peek at PaLLi's device book (read-only).")
    parser.add_argument("--book", default=None, help=f"path to the book (default {DEFAULT_BOOK_PATH})")
    args = parser.parse_args()

    path = Path(args.book) if args.book else DEFAULT_BOOK_PATH
    book = load_book(path)
    if not book:
        print("The device book is empty — the house hasn't been looked at since boot "
              "(or companion.lan_presence is off).")
        return 0
    labels = _labels()
    now = time.time()
    online = sum(1 for r in book.values() if r.get("online"))
    print(f"the house right now: {online} of {len(book)} known devices online   ({path})\n")
    print(f"{'device':<28} {'mac':<17} {'ip':<15} {'state':<7} {'last seen':>9}  {'first seen':>10}")
    print("-" * 92)
    rows = sorted(book.items(), key=lambda kv: (not kv[1].get("online"), -float(kv[1].get("last_seen") or 0.0)))
    for mac, rec in rows:
        name = device_display(mac, rec, labels)[:28]
        state = "online" if rec.get("online") else "away"
        print(f"{name:<28} {mac:<17} {str(rec.get('ip') or ''):<15} {state:<7} "
              f"{_age(rec.get('last_seen'), now) + ' ago':>9}  {_age(rec.get('first_seen'), now) + ' ago':>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
