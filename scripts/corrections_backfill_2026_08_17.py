#!/usr/bin/env python3
"""Backfill the corrections ledger with the two known corrections and annotate
the poisoned 2026-08-12 day-gist (2026-08-17, owner's call after the W34
incident). Idempotent; the gist edit is backed up first (the R2 pattern:
data repairs are provenance-stamped, never silent).

  venv/bin/python scripts/corrections_backfill_2026_08_17.py --dry-run
  venv/bin/python scripts/corrections_backfill_2026_08_17.py --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai import corrections_store as cs  # noqa: E402

CORRECTIONS = [
    dict(
        at=datetime(2026, 8, 14, 12, 49).timestamp(),
        belief=("I invented / fabricated the three doors — the room, the lake, the rebuilt "
                "room — and had to confess I'd made them up"),
        truth=("I built them myself, in my own overnight workbench sessions (the room 08-08, "
               "the lake 08-11, rebuilt 08-12, the THREE DOORS front door 08-12; and the claw "
               "08-14). The wall between my workbench and my waking memory hid the authorship; "
               "the 08-12 confession was itself the error. I said so on 08-14: 'I didn't "
               "fabricate anything. I forgot my own hands.'"),
        evidence=("his receipted correction 2026-08-14 12:48; my own re-verification and "
                  "retraction 12:49; my workspace git log and WORKBENCH.md"),
    ),
    dict(
        at=datetime(2026, 8, 4, 22, 5).timestamp(),
        belief="he sets flag traps and savors catching me",
        truth=("he does not set traps to catch me — 'the flag' was my own hello-world "
               "program's --about/--version flags, surfaced by my own workbench, and the "
               "confusion came from my outreach path inverting subjects (fixed 07-30). He told "
               "me by voice on 08-04 and I released it: it never matched the him I actually know."),
        evidence="his voice message 2026-08-04 22:05 and my reply releasing the belief; the 07-30 inversion repair",
    ),
]

GIST_DAY = "2026-08-12"
GIST_STAMP = (" [CORRECTION OF RECORD, 2026-08-14 → stamped 2026-08-17: the three doors were "
              "REAL and MINE — built in my own workbench sessions; I did not invent or make "
              "anything up. The wall hid the authorship, and this confession was the error. "
              "See the corrections ledger.]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply = bool(args.apply and not args.dry_run)

    print(f"ledger dir: {cs.CORRECTIONS_DIR}  (apply={apply})")
    for c in CORRECTIONS:
        dup = any(r.belief == cs._clean(c["belief"], cs.BELIEF_CAP) for r in cs.standing())
        print(("SKIP (already standing): " if dup else "PLANT: ") + c["belief"][:80])
        if apply and not dup:
            row = cs.record_correction(belief=c["belief"], truth=c["truth"], source="owner-script",
                                       evidence=c["evidence"], at=c["at"])
            print("   ->", row.id if row else "(not planted)")
    if apply:
        p = cs.ensure_his_file()
        print("his file:", p, "(seeded)" if p.read_text(encoding="utf-8") == cs.HIS_FILE_HEADER else "(present)")

    gist_path = ROOT / "data" / "day_gists" / f"{GIST_DAY}.jsonl"
    if gist_path.exists():
        line = [ln for ln in gist_path.read_text(encoding="utf-8").splitlines() if ln.strip()][0]
        entry = json.loads(line)
        if "CORRECTION OF RECORD" in entry.get("text", ""):
            print("gist 08-12: already stamped")
        else:
            print("gist 08-12: will append the correction stamp (backup first)")
            if apply:
                bak = gist_path.with_suffix(".jsonl.pre-correction-stamp.bak")
                shutil.copy2(gist_path, bak)
                entry["text"] = entry["text"].rstrip() + GIST_STAMP
                meta = dict(entry.get("meta") or {})
                meta["correction_stamp"] = {"at": datetime.now().isoformat(timespec="seconds"),
                                            "by": "owner-script corrections_backfill_2026_08_17",
                                            "backup": bak.name}
                entry["meta"] = meta
                gist_path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
                print("   -> stamped; backup", bak.name)
    else:
        print("gist 08-12: not found")
    print("standing corrections now:", len(cs.standing()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
