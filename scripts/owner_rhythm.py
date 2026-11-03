#!/usr/bin/env python
"""Her learned model of the owner's rhythm — preview or rebuild.

    venv/bin/python scripts/owner_rhythm.py            # compute + print only
    venv/bin/python scripts/owner_rhythm.py --write    # also write the file

Works with the flag off (this is the preview lever from spec §7): compute
is read-only against the DB. The night cycle also rebuilds the file every
night under companion.rhythm_refresh; quiet hours read it only under
arbiter_learned_quiet, while the surprise organ's expectations read it
whenever it exists.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.owner_rhythm import (  # noqa: E402
    DB_FILE,
    RHYTHM_FILE,
    compute_rhythm,
    gather_his_timestamps,
    refresh_rhythm_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="write data/rhythm/owner_rhythm.json")
    parser.add_argument("--db", default=str(DB_FILE))
    args = parser.parse_args()

    now = time.time()
    if args.write:
        rhythm = refresh_rhythm_file(now=now, db_path=Path(args.db))
        print(f"wrote {RHYTHM_FILE}")
    else:
        stamps = gather_his_timestamps(Path(args.db), now=now, lookback_days=56)
        rhythm = compute_rhythm(stamps, now=now)

    print(f"\n  {'hour':>4}  {'weekday':>16}  {'weekend':>16}")
    for h in range(24):
        row = []
        for kind in ("weekday", "weekend"):
            b = rhythm["buckets"][f"{kind}-{h:02d}"]
            mark = "QUIET" if b["quiet"] else "     "
            row.append(f"{b['active_days']:>3}/{b['observed_days']:<3} {mark}")
        print(f"  {h:02d}:00  {row[0]:>16}  {row[1]:>16}")
    quiet = [k for k, b in rhythm["buckets"].items() if b["quiet"]]
    print(f"\n  confidently quiet: {len(quiet)}/48 buckets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
