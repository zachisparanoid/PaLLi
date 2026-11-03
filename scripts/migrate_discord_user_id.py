#!/usr/bin/env python3
"""One-shot migration: set users.discord_user_id for the local owner.

Phase A task A6 of the 2026-05-08 completion refactor. Use this when
a user has been running PaLLi without LOCAL_OWNER_DISCORD_USER_ID set
and needs to populate the column without restarting. Setting the env
var and restarting achieves the same result via ensure_local_owner.

Usage:
    python scripts/migrate_discord_user_id.py --discord-user-id <your Discord user id>
    python scripts/migrate_discord_user_id.py --discord-user-id <your Discord user id> --apply
    python scripts/migrate_discord_user_id.py --status

The default is dry-run. Pass --apply to actually write. The script
backs up the sqlite to .pre-migration.bak before any UPDATE.
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
    """Return the app_user_id for the local owner.

    Looks for users.email = local@palli.local first; falls back to the
    smallest id (the local owner is always created first in
    ensure_local_owner).
    """
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
    owner_id = _resolve_local_owner_id(conn)
    row = conn.execute(
        "SELECT id, email, palli_user_id, discord_user_id FROM users WHERE id = ?",
        (owner_id,),
    ).fetchone()
    if row is None:
        print(f"Local owner row not found (resolved id={owner_id})")
        return
    print(f"Local owner: id={row[0]} email={row[1]} palli_user_id={row[2]} discord_user_id={row[3]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--discord-user-id",
        help="The Discord user_id to write into users.discord_user_id for the local owner.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the UPDATE. Default is dry-run.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show the current state of users.discord_user_id for the local owner and exit.",
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

        if not args.discord_user_id:
            parser.error("--discord-user-id is required unless --status is passed")

        try:
            int(args.discord_user_id)
        except ValueError:
            print(
                f"ERROR: --discord-user-id must be numeric, got {args.discord_user_id!r}",
                file=sys.stderr,
            )
            return 2

        owner_id = _resolve_local_owner_id(conn)
        current_row = conn.execute(
            "SELECT id, email, discord_user_id FROM users WHERE id = ?",
            (owner_id,),
        ).fetchone()
        if current_row is None:
            print(f"ERROR: local owner row not found (id={owner_id})", file=sys.stderr)
            return 2

        existing = current_row[2]
        print(f"Local owner: id={current_row[0]} email={current_row[1]}")
        print(f"  current discord_user_id: {existing!r}")
        print(f"  desired discord_user_id: {args.discord_user_id!r}")

        if existing == args.discord_user_id:
            print("No change needed — already matches desired value.")
            return 0

        if existing not in (None, ""):
            print(
                f"REFUSING: existing discord_user_id={existing!r} would be overwritten. "
                "Clear it manually first if you really want to change it.",
                file=sys.stderr,
            )
            return 2

        if not args.apply:
            print("\n(dry run — re-run with --apply to actually write)")
            return 0

        backup = DB_PATH.with_suffix(DB_PATH.suffix + ".pre-migration.bak")
        shutil.copy2(DB_PATH, backup)
        print(f"\nbacked up sqlite to {backup}")

        conn.execute(
            "UPDATE users SET discord_user_id = ? WHERE id = ?",
            (args.discord_user_id, owner_id),
        )
        conn.commit()

        verify_row = conn.execute(
            "SELECT discord_user_id FROM users WHERE id = ?",
            (owner_id,),
        ).fetchone()
        verify_value = verify_row[0] if verify_row is not None else None
        if verify_value != args.discord_user_id:
            print(
                f"ERROR: verification failed — wrote {args.discord_user_id!r} but read back {verify_value!r}",
                file=sys.stderr,
            )
            return 1
        print(f"OK — discord_user_id={verify_value!r} for local owner id={owner_id}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
