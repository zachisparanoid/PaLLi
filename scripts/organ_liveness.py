#!/usr/bin/env python
"""Did every organ write? Reads the install this script lives in, from any
working directory (or pass --data-root).

    venv/bin/python scripts/organ_liveness.py

Read-only. Exit 0 = nothing is stale; exit 2 = an organ should have written
and did not. Each organ reads wrote, quiet (it ran and had nothing to do)
or stale; an organ whose flag is off in <data-root>/palli.yaml is left out,
and occasioned organs (daydream, workbench, arbiter, corrections, forge,
regret, hands) never fail — silence can be lawful there. The engine logs
the same summary once a day after the night pass (06:30) and `palli_ctl doctor`
prints the same table; this script exists for cron and for hands.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.organ_liveness import install_report, night_cycle_switched_off  # noqa: E402

# Exactly the three status words; a fourth would be a KeyError here, loudly.
MARKS = {"wrote": "ok   ", "quiet": "quiet", "stale": "STALE"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        default=None,
        help="the data directory to inspect (default: data/ in the repo this script lives in)",
    )
    args = parser.parse_args(argv)

    # Left out, the data root is the repo's own, anchored where the database
    # and her repo are (install_report) — never the working directory's.
    data_root = Path(args.data_root) if args.data_root else None
    report = install_report(ROOT, now=datetime.now(), data_root=data_root)
    if report is None:
        if night_cycle_switched_off(data_root if data_root is not None else ROOT / "data"):
            print("organ_liveness: the night cycle is switched off, so no night runs "
                  "and nothing nightly is due.")
        else:
            print("organ_liveness: no night has run on this install yet — nothing is due.")
        return 0
    width = max((len(e.name) for e in report.entries), default=0)
    for entry in report.entries:
        print(f"  {entry.name:<{width}}  {MARKS[entry.status]}  {entry.detail}")
    print()
    print(report.summary())
    return 0 if report.healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
