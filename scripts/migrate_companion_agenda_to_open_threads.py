"""One-time migration: snapshot companion_agenda_items → drop the live table.

Idempotent — safe to re-run if interrupted. The new open_threads table
is created by the schema catalog on next bootstrap (already there from
plan Task 3).

Run:
    python scripts/migrate_companion_agenda_to_open_threads.py [--db PATH]

This script is the cutover step (plan Task 14). Pair it with the flag
flip to ENABLE_OPEN_THREADS_AGENDA=true in data/palli.yaml in the same
commit.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def run_migration(conn: sqlite3.Connection) -> None:
    """Snapshot companion_agenda_items to backup and drop the original.

    Idempotent: if the live table doesn't exist, no-op. If the backup
    already exists from a prior run, leave it alone.
    """
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='companion_agenda_items'"
    )
    if not cur.fetchall():
        return  # already migrated

    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='companion_agenda_items_pre_rebuild_backup'"
    )
    backup_exists = bool(cur.fetchall())

    if not backup_exists:
        conn.execute(
            "ALTER TABLE companion_agenda_items "
            "RENAME TO companion_agenda_items_pre_rebuild_backup"
        )
    else:
        conn.execute("DROP TABLE companion_agenda_items")

    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/palli-local.sqlite3")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        run_migration(conn)
        print(f"Migration complete: {db_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
