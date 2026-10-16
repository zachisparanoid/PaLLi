#!/usr/bin/env python
"""One-time: mark the pre-2026-08-06 workbench SHARE rows with
source_id='workbench_share'. They are identifiable only by the constant
why_it_matters string _file_share_thread always wrote. The marker lives in
source_id (unconstrained) because the live table's source_kind CHECK pins
that vocabulary. Dry-run by default; --apply to write. Idempotent."""
import argparse
import sqlite3
import sys

MAGIC = "her own work — something she genuinely wanted to tell him"
DB = "data/palli-local.sqlite3"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT id, substr(topic_summary,1,60), status FROM open_threads "
        "WHERE why_it_matters = ? "
        "AND (source_id IS NULL OR source_id != 'workbench_share')",
        (MAGIC,),
    ).fetchall()
    for r in rows:
        print(f"  {r[0][:8]}  [{r[2]}]  {r[1]}")
    print(f"{len(rows)} share rows to label.")
    if not args.apply:
        print("Dry-run — pass --apply to write.")
        return 0
    with conn:
        conn.execute(
            "UPDATE open_threads SET source_id = 'workbench_share' "
            "WHERE why_it_matters = ? "
            "AND (source_id IS NULL OR source_id != 'workbench_share')",
            (MAGIC,),
        )
    print("Applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
