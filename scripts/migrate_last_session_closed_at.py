#!/usr/bin/env python3
"""One-shot migration: move last_session_closed_at out of user_memory into users.

Phase B / B4 step 1 of the 2026-05-08 completion refactor. Resolves
audit finding IM-HIGH-2: `last_session_closed_at` was being stored as
a `user_fact` row in `user_memory` alongside real user-facing facts.
The decay-weighted recall budget had to scan past these rows to find
actual facts. They're metadata, not memory — they belong in
user-level config, not memory.

This script reads every `last_session_closed_at: <ts>|<reason>` row
out of `user_memory` (matched by `memory_type='user_fact'` AND
`content LIKE 'last_session_closed_at:%'`), picks the LATEST timestamp
per user, writes it to `users.last_session_closed_at` (preserving any
already-newer value the runtime may have written via the new path),
and deletes the source rows.

Architectural decision (documented in the commit body):
  - Home: column `last_session_closed_at DOUBLE PRECISION NULL` on
    `users` (NOT `user_profiles`). Rationale: the writer in
    `core/turn/reflect._record_session_close` keys on `owner_id`
    only — there is no per-platform dimension. `user_profiles` is
    composite-keyed on `(user_id, platform)`, which would require
    fan-out across all the user's platform rows for a value that is
    semantically global to the user. `users` is a single row per
    user; that matches the data shape exactly.
  - Type: `DOUBLE PRECISION NULL` (epoch seconds), matching the
    existing convention for *_at columns on `users` (e.g.
    `email_verified_at`, `tos_accepted_at`, `beta_joined_at`). NULL
    means "never observed a session close for this user yet" — the
    state of every existing install before this migration ran.

Usage:
    python scripts/migrate_last_session_closed_at.py --status
    python scripts/migrate_last_session_closed_at.py
    python scripts/migrate_last_session_closed_at.py --apply

The default is dry-run. Pass --apply to actually write. On --apply,
the script backs up the sqlite to .pre-migration.bak BEFORE any
mutation — including the schema ALTER that adds the new column to
existing installs. The .bak therefore captures truly pre-everything
state and `cp .bak palli-local.sqlite3` is a clean rollback to
where the operator started.

Idempotence: re-running --apply on an already-migrated DB is a
no-op (no rows to migrate, no UPDATE issued). Re-running --apply
after the runtime has updated `users.last_session_closed_at` to a
NEWER value is also safe — the migration takes the max and never
overwrites a newer value with an older one. The script also tolerates
a corrupted `users.last_session_closed_at` value (non-numeric TEXT
left over from format drift or manual SQL edits): the corrupt value
is logged at WARNING and treated as missing, so the migration's
latest_ts wins.

Safety: rows with unparseable timestamps are skipped (NOT deleted),
so a future content-format change cannot lose data; the operator
gets a chance to inspect them manually. If the LIKE filter matches
rows but NONE are parseable, --status and the dry-run flag the
situation prominently and exit non-zero so the operator cannot
silently skip them.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "palli-local.sqlite3"

# Match "last_session_closed_at: <ts>|<reason>" — the format
# core/turn/reflect._record_session_close writes today.
_CONTENT_PREFIX = "last_session_closed_at:"
_TIMESTAMP_RE = re.compile(r"^\s*last_session_closed_at\s*:\s*(\d+(?:\.\d+)?)\s*\|", re.IGNORECASE)


def _extract_timestamp(content: str) -> Optional[float]:
    """Parse the leading numeric timestamp out of the content string.

    Returns None for empty, non-matching, or unparseable input. We
    deliberately reject lines that don't have the expected
    `key: <number>|<reason>` shape — this guards against accidentally
    deleting rows whose format we don't fully understand.
    """
    if not content:
        return None
    match = _TIMESTAMP_RE.match(content)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def _coerce_current_value(raw: Any) -> Optional[float]:
    """Defensively coerce `users.last_session_closed_at` to a float.

    SQLite has type *affinity* but no strict type enforcement, and
    a non-numeric string can end up in the column from a corrupted
    DB, format drift between deployments, or a manual SQL edit.
    Treating that value as "missing" lets the migration win
    (overwrite with `latest_ts`) rather than crashing mid-loop on
    `float('garbage')`. The corruption is logged at WARNING so the
    operator sees what was overwritten.
    """
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Existing users.last_session_closed_at is unparseable: %r — "
            "treating as missing; migration's latest_ts will win.",
            raw,
        )
        return None


def _users_has_last_session_closed_at(conn: sqlite3.Connection) -> bool:
    """Return True iff the `users.last_session_closed_at` column exists."""
    rows = conn.execute("PRAGMA table_info(users)").fetchall()
    for row in rows:
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        if str(row[1]) == "last_session_closed_at":
            return True
    return False


def _ensure_column_exists(conn: sqlite3.Connection) -> bool:
    """Add the `last_session_closed_at` column to `users` if missing.

    Returns True iff the ALTER was issued (i.e. the column was added
    just now). Idempotent — calling it on a DB that already has the
    column is a no-op.

    PaLLi normally adds this column on the next bootstrap via the
    DEFAULT_SQLITE_MIGRATION_STATEMENTS list, but operators may run
    this migration script before restarting the app, so we
    self-prepare the schema rather than failing with a confusing
    OperationalError.
    """
    if _users_has_last_session_closed_at(conn):
        return False
    conn.execute("ALTER TABLE users ADD COLUMN last_session_closed_at DOUBLE PRECISION")
    conn.commit()
    return True


def _plan_migration(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Inspect the DB and return a per-user migration plan.

    The shape of each entry:
        plan[user_id] = {
            "row_ids":       [int, ...],   # user_memory rows to DELETE
            "latest_ts":     float,        # max parseable timestamp
            "current_value": Optional[float],  # users.last_session_closed_at
        }

    Users with NO parseable session-close rows are NOT included in
    the plan (so an empty plan == nothing to do). Users with rows
    whose timestamps are all unparseable are also excluded.
    """
    rows = conn.execute(
        """
        SELECT id, user_id, content
        FROM user_memory
        WHERE memory_type = 'user_fact'
          AND content LIKE ?
        """,
        (f"{_CONTENT_PREFIX}%",),
    ).fetchall()

    plan: dict[int, dict[str, Any]] = {}
    for row_id, user_id, content in rows:
        ts = _extract_timestamp(str(content or ""))
        if ts is None:
            # Unparseable: skip silently. We do not delete these so a
            # human can review them. _apply_migration relies on this.
            continue
        uid = int(user_id)
        entry = plan.setdefault(
            uid,
            {"row_ids": [], "latest_ts": None, "current_value": None},
        )
        entry["row_ids"].append(int(row_id))
        if entry["latest_ts"] is None or ts > entry["latest_ts"]:
            entry["latest_ts"] = ts

    # Annotate each user with the current value of users.last_session_closed_at
    for uid, entry in plan.items():
        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?",
            (uid,),
        ).fetchone()
        entry["current_value"] = None if row is None else row[0]

    return plan


def _apply_migration(
    conn: sqlite3.Connection,
    plan: dict[int, dict[str, Any]],
) -> dict[str, int]:
    """Apply the plan: write users.last_session_closed_at (taking
    max(plan, current)), then DELETE the source rows. Returns counts
    of users updated and rows deleted.
    """
    users_updated = 0
    rows_deleted = 0
    for uid, entry in plan.items():
        latest_ts: float = float(entry["latest_ts"])
        # `_coerce_current_value` returns None for missing OR
        # corrupted values (logging the corruption at WARNING). This
        # protects against `float('garbage')` raising ValueError
        # mid-loop and abandoning the remaining users.
        current_float = _coerce_current_value(entry["current_value"])
        # Take the max of the existing column value and the migrated
        # timestamp. This protects against the case where the runtime
        # has already started writing to the new home with a NEWER
        # value before this migration ran.
        if current_float is None or latest_ts > current_float:
            conn.execute(
                "UPDATE users SET last_session_closed_at = ? WHERE id = ?",
                (latest_ts, uid),
            )
            users_updated += 1
        # Delete the source rows regardless of whether we wrote a new
        # value — those rows are stale either way once the column is
        # the canonical home.
        if entry["row_ids"]:
            placeholders = ",".join("?" for _ in entry["row_ids"])
            cur = conn.execute(
                f"DELETE FROM user_memory WHERE id IN ({placeholders})",
                tuple(entry["row_ids"]),
            )
            rows_deleted += int(cur.rowcount or 0)
    return {"users_updated": users_updated, "rows_deleted": rows_deleted}


def _count_unparseable_rows(conn: sqlite3.Connection) -> int:
    """Count user_memory rows whose content matches the LIKE filter
    but whose timestamp does NOT parse via `_extract_timestamp`.
    These are skipped by `_plan_migration` and require human review.
    """
    count = 0
    for (content,) in conn.execute(
        """
        SELECT content FROM user_memory
        WHERE memory_type = 'user_fact' AND content LIKE ?
        """,
        (f"{_CONTENT_PREFIX}%",),
    ).fetchall():
        if _extract_timestamp(str(content or "")) is None:
            count += 1
    return count


def _show_status(conn: sqlite3.Connection) -> None:
    plan = _plan_migration(conn)

    # Count of total session-close-shaped rows (including unparseable)
    total_raw = conn.execute(
        """
        SELECT COUNT(*) FROM user_memory
        WHERE memory_type = 'user_fact' AND content LIKE ?
        """,
        (f"{_CONTENT_PREFIX}%",),
    ).fetchone()[0]
    print(f"user_memory rows matching last_session_closed_at:%  {total_raw}")

    unparseable = _count_unparseable_rows(conn)
    if unparseable:
        print(
            f"  ({unparseable} of those have unparseable timestamps and require "
            f"manual review; --apply will NOT delete them.)"
        )

    if not plan:
        if total_raw == 0:
            print("Nothing to migrate.")
        else:
            # All matching rows had unparseable timestamps. Make this
            # impossible to miss — a "Nothing to migrate" message here
            # would be misleading because there ARE rows; they just
            # need manual attention.
            print(
                f"WARNING: {total_raw} row(s) matched but ALL had unparseable "
                f"timestamps — inspect manually before --apply."
            )
    else:
        print(f"\nUsers with session-close rows pending migration: {len(plan)}")
        for uid in sorted(plan.keys()):
            entry = plan[uid]
            print(
                f"  user_id={uid}: rows_to_delete={len(entry['row_ids'])}, "
                f"latest_ts={entry['latest_ts']!r}, "
                f"current_users.last_session_closed_at={entry['current_value']!r}"
            )

    # Show the current users.last_session_closed_at column for every user
    print("\nusers.last_session_closed_at by user_id:")
    rows = conn.execute(
        "SELECT id, email, last_session_closed_at FROM users ORDER BY id"
    ).fetchall()
    for uid, email, lsc in rows:
        print(f"  id={uid} email={email!r} last_session_closed_at={lsc!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the UPDATE/DELETE. Default is dry-run.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show the row distribution and current column values; exit.",
    )
    args = parser.parse_args()

    # --status and --apply are mutually exclusive. Previously --status
    # silently won and --apply was dropped, which is confusing.
    if args.status and args.apply:
        parser.error("--status and --apply are mutually exclusive")

    # Ensure WARNING-level logs from `_coerce_current_value` and
    # friends are visible to the operator on stderr — pure scripts
    # don't get a logging configuration by default.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if not DB_PATH.exists():
        print(f"ERROR: sqlite database not found at {DB_PATH}", file=sys.stderr)
        return 2

    # Issue #4: take the backup BEFORE any mutation — including the
    # schema ALTER. Doing this here, before opening the connection
    # used for read+write, guarantees the .bak captures the truly
    # pre-everything state. If the operator wants a clean rollback,
    # `cp .bak palli-local.sqlite3` lands them back at the start.
    if args.apply:
        backup = DB_PATH.with_suffix(DB_PATH.suffix + ".pre-migration.bak")
        shutil.copy2(DB_PATH, backup)
        print(f"backed up sqlite to {backup}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        # The schema bootstrap ALTER may not have run yet on an
        # existing install — make sure the column exists before we
        # try to read or write it. This is a no-op once PaLLi has
        # restarted post-deploy (the catalog adds it on bootstrap).
        added = _ensure_column_exists(conn)
        if added:
            print("Added missing column users.last_session_closed_at")

        if args.status:
            _show_status(conn)
            return 0

        plan = _plan_migration(conn)

        # Count rows that matched the LIKE filter but had unparseable
        # timestamps. These are NOT in the plan and would otherwise be
        # silently skipped. The dry-run / apply path must surface them.
        unparseable = _count_unparseable_rows(conn)

        if not plan:
            if unparseable:
                # Issue #2: matching rows exist but ALL are unparseable.
                # Don't claim "Nothing to migrate"; flag the rows and
                # exit non-zero so the operator can't accidentally
                # treat this as a successful no-op.
                print(
                    f"WARNING: {unparseable} row(s) matched last_session_closed_at:% "
                    f"but had unparseable timestamps. They will NOT be deleted by "
                    f"--apply; inspect them manually first.",
                    file=sys.stderr,
                )
                return 1
            print("Nothing to migrate — no last_session_closed_at rows found.")
            return 0

        print(f"Planned migration:")
        total_rows = 0
        for uid in sorted(plan.keys()):
            entry = plan[uid]
            total_rows += len(entry["row_ids"])
            print(
                f"  user_id={uid}: would delete {len(entry['row_ids'])} rows, "
                f"would set users.last_session_closed_at to "
                f"max({entry['current_value']!r}, {entry['latest_ts']!r})"
            )
        print(f"  total rows to delete: {total_rows}")
        if unparseable:
            print(
                f"  ({unparseable} additional row(s) had unparseable timestamps "
                f"and will be PRESERVED — inspect manually.)"
            )

        if not args.apply:
            print("\n(dry run — re-run with --apply to actually write)")
            return 0

        result = _apply_migration(conn, plan)
        conn.commit()

        print(
            f"\nApplied: users_updated={result['users_updated']}, "
            f"rows_deleted={result['rows_deleted']}"
        )

        # Verify
        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM user_memory
            WHERE memory_type = 'user_fact' AND content LIKE ?
            """,
            (f"{_CONTENT_PREFIX}%",),
        ).fetchone()[0]
        # Only unparseable rows should remain. If parseable rows
        # remain, something went wrong.
        leftover_parseable = 0
        for row_id, content in conn.execute(
            """
            SELECT id, content FROM user_memory
            WHERE memory_type = 'user_fact' AND content LIKE ?
            """,
            (f"{_CONTENT_PREFIX}%",),
        ).fetchall():
            if _extract_timestamp(str(content or "")) is not None:
                leftover_parseable += 1

        print(f"  remaining session-close-shaped rows (incl. unparseable): {remaining}")
        if leftover_parseable:
            print(
                f"WARNING: {leftover_parseable} parseable rows still present after migration",
                file=sys.stderr,
            )
            return 1
        if remaining:
            print(
                f"  ({remaining} row(s) had unparseable timestamps and were preserved "
                f"for human review.)"
            )
        print("OK — migration verified.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
