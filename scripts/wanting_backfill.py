#!/usr/bin/env python3
"""Plant quest #1 — his 2026-07-28 standing invite (re-spec §3 backfill).

The wish that proved the gap: he said it, her monologue registered it that
evening ("he gave me a standing invite to explore and surprise him"), and
nothing could hold it. It should be the first thing the store holds.

Default is a dry run; --apply writes. Idempotent by stable quest id.
Run with: venv/bin/python -m scripts.wanting_backfill [--apply]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

QUEST_ID = "surprise-me-2026-07-28"
TEXT = "look up whatever you want… create something to show me. surprise me 😉"
PROVENANCE = (
    "his words in conversation, 2026-07-28 — the standing invite her "
    "monologue registered that evening and nothing could hold"
)


def run_backfill(*, apply: bool) -> dict:
    from ai.quest_store import load_quests, plant

    if QUEST_ID in load_quests():
        return {"would_plant": False, "planted": False, "why": "already present"}
    if not apply:
        return {"would_plant": True, "planted": False, "why": "dry run"}
    q, _ = plant(
        text=TEXT, source="him-spoken", horizon="standing",
        provenance=PROVENANCE, quest_id=QUEST_ID,
    )
    return {"would_plant": True, "planted": q is not None, "why": "applied"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()
    result = run_backfill(apply=args.apply)
    print(result)


if __name__ == "__main__":
    main()
