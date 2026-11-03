#!/usr/bin/env python3
"""One-shot migration: absorb proactive_actions into open_threads.

Phase C, Cluster C1.5 of the 2026-05-08 unified completion refactor.
Implements Step 3 of the B5 design doc (queue-collapse schema):

    docs/superpowers/plans/2026-05-08-decision-5-queue-collapse-schema.md

The legacy ``proactive_actions`` table is collapsed into ``open_threads``
with ``source_kind='proactive_record'`` and the new ``status='logged'``
literal (B5 Q4). Each row's full original payload is preserved in
``open_threads.source_payload`` JSON under documented keys so the
rumination guard and daily-cap queries keep working after the rewire
in C1.5 step 4.

Column mapping (per B5 doc, "source_kind = 'proactive_record'" section):

    proactive_actions          -> open_threads
    -----------------          -- ------------
    id (BIGINT)                -> source_id (TEXT, str(id))
    user_id                    -> owner_user_id
    runtime_job_id             -> source_payload.runtime_job_id
    runtime_session_key        -> source_payload.runtime_session_key
    action_type                -> source_payload.action_type
    trigger_reason (JSON-blob) -> source_payload.original_trigger_reason
                                  (the JSON-in-TEXT blob, preserved verbatim
                                   as either dict-decoded if parseable or
                                   raw string. The rumination guard reads
                                   trigger_reason.candidate_tick_timestamp
                                   from this; it walks
                                   source_payload['original_trigger_reason']
                                   after migration.)
    content                    -> topic_summary
    scheduled_time             -> created_at (fall back to executed_time)
    executed_time              -> last_surfaced_at AND resolved_at
                                  (executed = both surfaced AND terminal)
    success                    -> source_payload.success
    user_response              -> source_payload.user_response
    effectiveness_score        -> source_payload.effectiveness_score
    (computed)                 -> status = 'logged'
    (computed)                 -> kind   = 'other' (B5 Q6: defer LLM rescore)
    (computed)                 -> urgency = 'historical'
    (computed)                 -> why_it_matters =
                                  "Proactive outreach record (rumination
                                   guard / daily-cap source)"
    (computed)                 -> resolution_reason = 'executed' if success,
                                  'failed' if success=False, else NULL
    (computed)                 -> surface_count = 1

Plus original_instruction / original_why_now keys are reserved on the
payload schema even though proactive_actions has no such fields, for
schema parallelism with the future RECURRING / FOLLOWTHROUGH absorbs.

The migration also widens the open_threads.status CHECK constraint to
include 'logged' on existing installs (the C1.5 schema-catalog change
applies to fresh CREATE TABLE; pre-C1.5 installs need a table rebuild
because SQLite cannot ALTER a CHECK constraint in place).

Usage:
    python scripts/migrate_proactive_actions_to_open_threads.py --status
    python scripts/migrate_proactive_actions_to_open_threads.py
    python scripts/migrate_proactive_actions_to_open_threads.py --apply

The default is dry-run. Pass ``--apply`` to write. Backs up the sqlite
to ``.pre-C1.5-migrate.bak`` before any write so a rollback is possible.
Idempotent: re-running ``--apply`` is safe; rows already migrated
(matched by ``source_id`` + ``source_kind='proactive_record'``) are
skipped.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "palli-local.sqlite3"

PROACTIVE_RECORD_KIND = "proactive_record"
LOGGED_STATUS = "logged"
DEFAULT_THREAD_KIND = "other"
DEFAULT_URGENCY = "historical"
WHY_IT_MATTERS = "Proactive outreach record (rumination guard / daily-cap source)"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _open_threads_status_check(conn: sqlite3.Connection) -> str:
    """Return the live CREATE TABLE SQL for open_threads (or '')."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='open_threads'"
    ).fetchone()
    return row[0] if row and row[0] else ""


def _open_threads_has_logged_status(conn: sqlite3.Connection) -> bool:
    """True if the live open_threads CHECK constraint allows 'logged'."""
    sql = _open_threads_status_check(conn).lower()
    if not sql:
        return True  # table doesn't exist yet; new CREATE will include 'logged'
    # The check constraint contains the literal 'logged' once widened.
    return "'logged'" in sql


def _open_threads_columns(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("PRAGMA table_info(open_threads)").fetchall()
    return [str(r[1]) for r in rows]


def _open_threads_has_source_columns(conn: sqlite3.Connection) -> bool:
    cols = set(_open_threads_columns(conn))
    return {"source_kind", "source_id", "source_payload"}.issubset(cols)


def _ensure_status_check_widened(conn: sqlite3.Connection) -> None:
    """Rebuild open_threads with the widened CHECK if needed.

    SQLite cannot ALTER a CHECK constraint in place. On pre-C1.5 installs
    the live table's CHECK still excludes 'logged' (rejecting INSERTs
    we're about to make). The fix is the documented SQLite table-rebuild
    pattern: rename old table, CREATE the new table, INSERT...SELECT,
    DROP old. This preserves rows AND any rowids.
    """
    if _open_threads_has_logged_status(conn):
        return  # already widened (fresh bootstrap or rerun)
    if not _table_exists(conn, "open_threads"):
        return  # table doesn't exist (test mode); nothing to widen

    print("widening open_threads CHECK(status) to include 'logged'...")

    has_source_cols = _open_threads_has_source_columns(conn)

    # Build the new CREATE; matches core/sqlite_schema_catalog.py post-C1.5.
    create_new = """
        CREATE TABLE open_threads__C1_5_migration (
            id TEXT PRIMARY KEY,
            owner_user_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'resolved', 'dismissed', 'logged')),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            topic_summary TEXT NOT NULL,
            why_it_matters TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('work', 'personal', 'health', 'curiosity', 'followup', 'other')),
            urgency TEXT NOT NULL,
            last_surfaced_at REAL,
            surface_count INTEGER NOT NULL DEFAULT 0,
            resolved_at REAL,
            resolution_reason TEXT,
            source_kind TEXT NOT NULL DEFAULT 'topic'
                CHECK(source_kind IN ('topic', 'recurring', 'followthrough', 'proactive_record')),
            source_id TEXT,
            source_payload BLOB
        )
    """
    conn.execute(create_new)

    if has_source_cols:
        # Modern shape — straight column-for-column copy.
        conn.execute(
            """
            INSERT INTO open_threads__C1_5_migration (
                id, owner_user_id, status, created_at, updated_at,
                topic_summary, why_it_matters, kind, urgency,
                last_surfaced_at, surface_count, resolved_at, resolution_reason,
                source_kind, source_id, source_payload
            )
            SELECT
                id, owner_user_id, status, created_at, updated_at,
                topic_summary, why_it_matters, kind, urgency,
                last_surfaced_at, surface_count, resolved_at, resolution_reason,
                COALESCE(source_kind, 'topic'), source_id, source_payload
            FROM open_threads
            """
        )
    else:
        # Pre-C1.5 shape — backfill source_kind='topic', NULL source_id /
        # source_payload. (Tests under tempdir hit this path.)
        conn.execute(
            """
            INSERT INTO open_threads__C1_5_migration (
                id, owner_user_id, status, created_at, updated_at,
                topic_summary, why_it_matters, kind, urgency,
                last_surfaced_at, surface_count, resolved_at, resolution_reason,
                source_kind, source_id, source_payload
            )
            SELECT
                id, owner_user_id, status, created_at, updated_at,
                topic_summary, why_it_matters, kind, urgency,
                last_surfaced_at, surface_count, resolved_at, resolution_reason,
                'topic', NULL, NULL
            FROM open_threads
            """
        )

    conn.execute("DROP TABLE open_threads")
    conn.execute("ALTER TABLE open_threads__C1_5_migration RENAME TO open_threads")

    # Re-create the indexes.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_open_threads_owner_status "
        "ON open_threads(owner_user_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_open_threads_owner_last_surfaced "
        "ON open_threads(owner_user_id, last_surfaced_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_open_threads_source_kind "
        "ON open_threads(owner_user_id, source_kind, status)"
    )


def _normalize_trigger_reason(raw: Any) -> Any:
    """Try to JSON-decode trigger_reason; return decoded dict or original str.

    The legacy column is JSON-in-TEXT; the rumination guard parsed it on
    read. Storing the decoded dict in source_payload['original_trigger_reason']
    is more SQL-friendly (json_extract works), but if the value isn't
    valid JSON we keep the raw string so nothing is lost.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    text = str(raw).strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            pass
    return text


def _build_source_payload(row: sqlite3.Row) -> dict[str, Any]:
    """Construct the source_payload dict for one proactive_actions row.

    All keys are nullable per the legacy schema. Reader paths
    (rumination guard, daily-cap, bounded-reflection) read documented
    keys and tolerate missing values.
    """
    d = dict(row)
    raw_success = d.get("success")
    payload: dict[str, Any] = {
        "runtime_job_id": d.get("runtime_job_id"),
        "runtime_session_key": d.get("runtime_session_key"),
        "action_type": d.get("action_type"),
        "original_trigger_reason": _normalize_trigger_reason(d.get("trigger_reason")),
        "success": (None if raw_success is None else bool(raw_success)),
        "user_response": d.get("user_response"),
        "effectiveness_score": d.get("effectiveness_score"),
        # Reserved keys for parallelism with future RECURRING /
        # FOLLOWTHROUGH absorbs (B5 Q3). proactive_actions has no
        # 'instruction' or 'why_now' fields; leave them None.
        "original_instruction": None,
        "original_why_now": None,
    }
    return payload


def _build_open_threads_row(row: sqlite3.Row) -> dict[str, Any]:
    """Map one proactive_actions row to its open_threads INSERT shape."""
    d = dict(row)
    scheduled = d.get("scheduled_time")
    executed = d.get("executed_time")
    created_at = float(scheduled) if scheduled is not None else (
        float(executed) if executed is not None else time.time()
    )
    updated_at = float(executed) if executed is not None else created_at
    last_surfaced_at = float(executed) if executed is not None else None
    resolved_at = float(executed) if executed is not None else None
    # SQLite stores BOOLEAN as 0/1 ints (or NULL); normalize.
    raw_success = d.get("success")
    if raw_success is None:
        success_bool: bool | None = None
    else:
        success_bool = bool(raw_success)
    if success_bool is True:
        resolution_reason = "executed"
    elif success_bool is False:
        resolution_reason = "failed"
    else:
        resolution_reason = None
    payload = _build_source_payload(row)
    content = d.get("content") or ""
    topic_summary = str(content).strip() or "(no content)"
    return {
        "id": uuid.uuid4().hex,
        "owner_user_id": int(d["user_id"]),
        "status": LOGGED_STATUS,
        "created_at": created_at,
        "updated_at": updated_at,
        "topic_summary": topic_summary,
        "why_it_matters": WHY_IT_MATTERS,
        "kind": DEFAULT_THREAD_KIND,
        "urgency": DEFAULT_URGENCY,
        "last_surfaced_at": last_surfaced_at,
        "surface_count": 1,
        "resolved_at": resolved_at,
        "resolution_reason": resolution_reason,
        "source_kind": PROACTIVE_RECORD_KIND,
        "source_id": str(int(d["id"])),
        "source_payload": json.dumps(payload),
    }


def _select_proactive_actions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(conn, "proactive_actions"):
        return []
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(
        """
        SELECT id, user_id, runtime_job_id, runtime_session_key,
               action_type, trigger_reason, content, scheduled_time,
               executed_time, success, user_response, effectiveness_score
        FROM proactive_actions
        ORDER BY id ASC
        """
    )
    return list(cur.fetchall())


def _existing_source_ids(conn: sqlite3.Connection) -> set[str]:
    """Already-migrated source_ids — for idempotency on rerun.

    Returns empty when the source_* columns don't exist yet (pre-C1.5
    install in dry-run before --apply has rebuilt the table). Once
    --apply runs, _ensure_status_check_widened adds the columns and
    subsequent runs see real data.
    """
    if not _table_exists(conn, "open_threads"):
        return set()
    if not _open_threads_has_source_columns(conn):
        return set()
    cur = conn.execute(
        "SELECT source_id FROM open_threads "
        "WHERE source_kind = ? AND source_id IS NOT NULL",
        (PROACTIVE_RECORD_KIND,),
    )
    return {str(r[0]) for r in cur.fetchall()}


def _insert_open_thread(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO open_threads (
            id, owner_user_id, status, created_at, updated_at,
            topic_summary, why_it_matters, kind, urgency,
            last_surfaced_at, surface_count, resolved_at, resolution_reason,
            source_kind, source_id, source_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["id"],
            payload["owner_user_id"],
            payload["status"],
            payload["created_at"],
            payload["updated_at"],
            payload["topic_summary"],
            payload["why_it_matters"],
            payload["kind"],
            payload["urgency"],
            payload["last_surfaced_at"],
            payload["surface_count"],
            payload["resolved_at"],
            payload["resolution_reason"],
            payload["source_kind"],
            payload["source_id"],
            payload["source_payload"],
        ),
    )


def run_migration(conn: sqlite3.Connection) -> dict[str, int]:
    """Execute the migration against an open connection (no commit).

    Returns a dict with ``scanned``, ``migrated``, ``skipped`` counts.
    Caller is responsible for ``conn.commit()`` (the script does this on
    --apply; tests may call run_migration directly and assert on counts).
    """
    _ensure_status_check_widened(conn)
    rows = _select_proactive_actions(conn)
    already = _existing_source_ids(conn)
    migrated = 0
    skipped = 0
    for row in rows:
        source_id = str(int(row["id"]))
        if source_id in already:
            skipped += 1
            continue
        _insert_open_thread(conn, _build_open_threads_row(row))
        migrated += 1
    return {
        "scanned": len(rows),
        "migrated": migrated,
        "skipped": skipped,
    }


def _show_status(conn: sqlite3.Connection) -> None:
    has_table = _table_exists(conn, "proactive_actions")
    if not has_table:
        print("proactive_actions table: does not exist (already dropped)")
    else:
        count_row = conn.execute("SELECT COUNT(*) FROM proactive_actions").fetchone()
        live_count = int(count_row[0]) if count_row else 0
        print(f"proactive_actions table: exists; {live_count} rows live")
    if _table_exists(conn, "open_threads"):
        wide = _open_threads_has_logged_status(conn)
        has_source = _open_threads_has_source_columns(conn)
        print(f"open_threads.status accepts 'logged': {wide}")
        print(f"open_threads has source_* columns: {has_source}")
        if has_source:
            absorbed = conn.execute(
                "SELECT COUNT(*) FROM open_threads WHERE source_kind = ?",
                (PROACTIVE_RECORD_KIND,),
            ).fetchone()
            print(f"open_threads with source_kind='{PROACTIVE_RECORD_KIND}': {int(absorbed[0])} rows")
    else:
        print("open_threads table: does not exist")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show migration state and exit (does not write).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the migration. Default is dry-run.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: sqlite database not found at {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    try:
        if args.status:
            _show_status(conn)
            return 0

        rows_to_migrate = _select_proactive_actions(conn)
        already = _existing_source_ids(conn)
        new_rows = [r for r in rows_to_migrate if str(int(r["id"])) not in already]
        print(f"Will migrate {len(new_rows)} new proactive_actions row(s) "
              f"(of {len(rows_to_migrate)} total; {len(rows_to_migrate) - len(new_rows)} already migrated).")

        if not args.apply:
            print("\n(dry run — re-run with --apply to actually write)")
            return 0

        backup = db_path.with_suffix(db_path.suffix + ".pre-C1.5-migrate.bak")
        shutil.copy2(db_path, backup)
        print(f"\nbacked up sqlite to {backup}")

        result = run_migration(conn)
        conn.commit()

        print(
            f"\nMigration complete: scanned={result['scanned']} "
            f"migrated={result['migrated']} skipped={result['skipped']}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
