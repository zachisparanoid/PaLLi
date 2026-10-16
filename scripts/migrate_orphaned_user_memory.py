#!/usr/bin/env python3
"""One-shot migration: re-attribute orphaned user_memory rows to the local owner.

Phase A task A7 of the 2026-05-08 completion refactor. Before A5
landed, voice and Discord text were attributing turns to different
user_ids (1 or the speaker's Discord id). The result for an existing
install is a `user_memory` table with rows split across the legacy
Discord user_id and user_id=1 — and decay-ranked recall only sees
one of them at a time.

This script UPDATES every user_memory row from the legacy id to the
local owner's app_user_id. There are no UNIQUE constraints on the
table beyond the primary key, so the UPDATE cannot violate any
schema invariants.

Usage:
    python scripts/migrate_orphaned_user_memory.py --status
    python scripts/migrate_orphaned_user_memory.py --legacy-user-id <the old Discord user id>
    python scripts/migrate_orphaned_user_memory.py --legacy-user-id <the old Discord user id> --apply

The default is dry-run. Pass --apply to actually write. Backs up the
sqlite to .pre-migration.bak before any UPDATE.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "palli-local.sqlite3"


def _resolve_local_owner_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM users WHERE email = ?",
        ("local@palli.local",),
    ).fetchone()
    if row is not None:
        return int(row[0])
    row = conn.execute("SELECT MIN(id) FROM users").fetchone()
    if row is None or row[0] is None:
        raise SystemExit("No users found in the database")
    return int(row[0])


def _show_status(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT user_id, COUNT(*) FROM user_memory GROUP BY user_id ORDER BY user_id"
    ).fetchall()
    print("user_memory rows by user_id:")
    if not rows:
        print("  (none)")
    for user_id, count in rows:
        print(f"  user_id={user_id}: {count} rows")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--legacy-user-id",
        type=int,
        help="The user_id under which the orphaned rows are currently stored.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the UPDATE. Default is dry-run.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show the row distribution by user_id and exit.",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: sqlite database not found at {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(DB_PATH))
    try:
        if args.status:
            _show_status(conn)
            return 0

        if args.legacy_user_id is None:
            parser.error("--legacy-user-id is required unless --status is passed")

        owner_id = _resolve_local_owner_id(conn)
        if args.legacy_user_id == owner_id:
            print(
                f"REFUSING: legacy_user_id={args.legacy_user_id} is the same as the "
                f"local owner id; nothing to migrate.",
                file=sys.stderr,
            )
            return 2

        legacy_count = conn.execute(
            "SELECT COUNT(*) FROM user_memory WHERE user_id = ?",
            (args.legacy_user_id,),
        ).fetchone()[0]

        owner_count_before = conn.execute(
            "SELECT COUNT(*) FROM user_memory WHERE user_id = ?",
            (owner_id,),
        ).fetchone()[0]

        print(f"Local owner id: {owner_id}")
        print(f"Legacy user_id: {args.legacy_user_id}")
        print(f"  rows under legacy: {legacy_count}")
        print(f"  rows under owner: {owner_count_before}")

        if legacy_count == 0:
            print("Nothing to migrate — no rows under legacy user_id.")
            return 0

        if not args.apply:
            print(f"\n(dry run — would move {legacy_count} rows. Re-run with --apply to actually write.)")
            return 0

        backup = DB_PATH.with_suffix(DB_PATH.suffix + ".pre-migration.bak")
        shutil.copy2(DB_PATH, backup)
        print(f"\nbacked up sqlite to {backup}")

        result = conn.execute(
            "UPDATE user_memory SET user_id = ? WHERE user_id = ?",
            (owner_id, args.legacy_user_id),
        )
        moved = result.rowcount
        conn.commit()

        legacy_after = conn.execute(
            "SELECT COUNT(*) FROM user_memory WHERE user_id = ?",
            (args.legacy_user_id,),
        ).fetchone()[0]
        owner_after = conn.execute(
            "SELECT COUNT(*) FROM user_memory WHERE user_id = ?",
            (owner_id,),
        ).fetchone()[0]

        print(f"\nUPDATE moved {moved} rows.")
        print(f"  rows under legacy: {legacy_count} -> {legacy_after}")
        print(f"  rows under owner: {owner_count_before} -> {owner_after}")

        if legacy_after != 0:
            print(f"WARNING: legacy user_id still has {legacy_after} rows after migration", file=sys.stderr)
            return 1
        if owner_after != owner_count_before + legacy_count:
            print(
                f"WARNING: owner row count delta ({owner_after - owner_count_before}) "
                f"does not match legacy count ({legacy_count})",
                file=sys.stderr,
            )
            return 1
        print("OK — migration verified.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
