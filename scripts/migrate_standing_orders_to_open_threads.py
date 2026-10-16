#!/usr/bin/env python3
"""One-shot migration: absorb standing_orders into open_threads.

Phase C, Cluster C8 of the 2026-05-08 unified completion refactor.
Implements the "RECURRING absorb" of the B5 design doc (queue-collapse
schema):

    docs/superpowers/plans/2026-05-08-decision-5-queue-collapse-schema.md

The legacy ``standing_orders`` table is collapsed into ``open_threads``
with ``source_kind='recurring'`` and ``status='active'`` (or 'dismissed'
for paused/disabled rows). Each row's full original payload is preserved
in ``open_threads.source_payload`` JSON under documented keys so post-
migration writers/readers can round-trip the typed fields that have no
existing column on open_threads (trigger_spec, capability_status, etc.).

Column mapping (per B5 doc, "source_kind = 'recurring'" section):

    standing_orders            -> open_threads
    -----------------          -- ------------
    standing_order_id          -> source_id (TEXT, verbatim)
    owner_user_id              -> owner_user_id
    title                      -> topic_summary
    instruction                -> source_payload.original_instruction
                                  AND why_it_matters (truncated label)
    program_kind               -> source_payload.program_kind
                                  AND kind (heuristic per B5 Q6 — see below)
    trigger_kind               -> source_payload.trigger_kind
    trigger_spec               -> source_payload.trigger_spec (JSON-in-JSON)
    status                     -> status (translated per B5 Q5):
                                    'active'   -> 'active'
                                    'paused'   -> 'dismissed'
                                    'disabled' -> 'dismissed'
                                  source_payload.original_status preserves
                                  the round-trip distinction.
    capability_status          -> source_payload.capability_status
    delivery_policy            -> source_payload.delivery_policy
    escalation_policy          -> source_payload.escalation_policy
    workspace_root             -> source_payload.workspace_root
    next_run_at                -> source_payload.next_run_at
                                  (B5 doc earmarked next_action_at as the
                                   unified scheduling clock, but Step 0
                                   never landed — the column does not yet
                                   exist on open_threads. Stash next_run_at
                                   in source_payload until C1.4.5/Phase E
                                   adds the column.)
    last_run_at                -> last_surfaced_at
    last_result_summary        -> source_payload.last_result_summary
    metadata                   -> source_payload.metadata (verbatim)
    created_at                 -> created_at
    updated_at                 -> updated_at
    (computed)                 -> kind = derived from program_kind:
                                    companion_checkin    -> 'personal'
                                    followthrough_review -> 'followup'
                                    operator_program     -> 'work'
                                    research_watch       -> 'work'
                                    alert_watch          -> 'work'
                                    email_triage         -> 'work'
                                    else                 -> 'other'
    (computed)                 -> urgency = 'recurring' (B5 doc default;
                                  payload preserves real cadence)
    (computed)                 -> resolution_reason = last_result_summary
                                  iff status->dismissed (final-run record);
                                  else NULL
    (computed)                 -> surface_count = 0 (audit history is in
                                  standing_order_runs, not migrated here)

Plus reserved keys preserved for parallelism with the future
FOLLOWTHROUGH absorb (B5 Q3): ``original_why_now`` is None on RECURRING
since standing_orders has no such field.

Usage:
    python scripts/migrate_standing_orders_to_open_threads.py --status
    python scripts/migrate_standing_orders_to_open_threads.py
    python scripts/migrate_standing_orders_to_open_threads.py --apply

The default is dry-run. Pass ``--apply`` to write. Backs up the sqlite
to ``.pre-C8-standing-orders-migrate.bak`` before any write so a
rollback is possible. Idempotent: re-running ``--apply`` is safe; rows
already migrated (matched by ``source_id`` + ``source_kind='recurring'``)
are skipped.

Note (versus C1.5 precedent): this script does NOT widen the
open_threads CHECK(status) constraint — C1.5 already widened it to
include 'logged'. The RECURRING absorb only writes status='active' or
'dismissed', both of which the post-C1.5 CHECK constraint already
permits. The schema is therefore a pure data-move + caller rewire.
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

RECURRING_KIND = "recurring"
DEFAULT_URGENCY = "recurring"

# Per B5 Q6 "Derived" mapping. companion_checkin is the most common
# program_kind; the others are operator-tooling oriented.
_PROGRAM_KIND_TO_THREAD_KIND = {
    "companion_checkin": "personal",
    "followthrough_review": "followup",
    "operator_program": "work",
    "research_watch": "work",
    "alert_watch": "work",
    "email_triage": "work",
}

# Per B5 Q5 "status translation". 'paused' is reversible in
# standing_orders semantics but 'dismissed' is not — readers consulting
# source_payload.original_status get the round-trip distinction.
_STATUS_TRANSLATION = {
    "active": "active",
    "paused": "dismissed",
    "disabled": "dismissed",
}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _open_threads_columns(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("PRAGMA table_info(open_threads)").fetchall()
    return [str(r[1]) for r in rows]


def _open_threads_has_source_columns(conn: sqlite3.Connection) -> bool:
    cols = set(_open_threads_columns(conn))
    return {"source_kind", "source_id", "source_payload"}.issubset(cols)


def _normalize_trigger_spec(raw: Any) -> Any:
    """JSON-decode trigger_spec if it's a TEXT blob; else return verbatim.

    standing_orders.trigger_spec is JSONB on the schema but SQLite
    stores it as TEXT. Storing a decoded dict in
    source_payload['trigger_spec'] keeps json_extract paths working
    on the unified store.
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


def _normalize_metadata(raw: Any) -> Any:
    """Same pattern as _normalize_trigger_spec but for the metadata column."""
    return _normalize_trigger_spec(raw)


def _build_source_payload(row: sqlite3.Row) -> dict[str, Any]:
    """Construct the source_payload dict for one standing_orders row.

    All keys are nullable per the legacy schema. Reader paths
    (heartbeat due-poll, autonomy-control reads) fetch documented keys
    and tolerate missing values.
    """
    d = dict(row)
    payload: dict[str, Any] = {
        "program_kind": d.get("program_kind"),
        "trigger_kind": d.get("trigger_kind"),
        "trigger_spec": _normalize_trigger_spec(d.get("trigger_spec")),
        # Round-trip preservation per B5 Q5 — readers that care about the
        # paused-vs-disabled distinction read this key, not the unified
        # 'status' column (which collapses both to 'dismissed').
        "original_status": d.get("status"),
        "capability_status": d.get("capability_status"),
        "delivery_policy": d.get("delivery_policy"),
        "escalation_policy": d.get("escalation_policy"),
        "workspace_root": d.get("workspace_root"),
        # B5 doc earmarked next_action_at as a top-level column (Step 0
        # of the migration order); that column never landed (only the
        # source_kind/source_id/source_payload columns shipped in C1.5).
        # Stash here until Phase E adds the column. The heartbeat
        # due-poll reads source_payload.next_run_at after the rewire.
        "next_run_at": (
            float(d["next_run_at"]) if d.get("next_run_at") is not None else None
        ),
        "last_result_summary": d.get("last_result_summary"),
        "metadata": _normalize_metadata(d.get("metadata")),
        # Reserved keys for parallelism with future absorbs (B5 Q3).
        # standing_orders has its full instruction body migrated to its
        # own key (more durable than a truncated why_it_matters); other
        # absorbed kinds may not have an instruction-shaped field.
        "original_instruction": d.get("instruction"),
        "original_why_now": None,
    }
    return payload


def _derive_thread_kind(program_kind: str | None) -> str:
    """Map standing_orders.program_kind to open_threads.kind per B5 Q6."""
    if not program_kind:
        return "other"
    return _PROGRAM_KIND_TO_THREAD_KIND.get(str(program_kind).strip().lower(), "other")


def _truncate_for_why_it_matters(instruction: str | None) -> str:
    """Compress the instruction blob to a short rationale for why_it_matters.

    The full text is preserved verbatim in source_payload.original_instruction;
    why_it_matters is a NOT-NULL TEXT for display/index. Cap at 240 chars
    so dashboards / reflective surfaces don't choke on multi-page briefs.
    """
    text = str(instruction or "").strip()
    if not text:
        return "Recurring program (instruction blank)"
    if len(text) <= 240:
        return text
    return text[:237].rstrip() + "..."


def _build_open_threads_row(row: sqlite3.Row) -> dict[str, Any]:
    """Map one standing_orders row to its open_threads INSERT shape."""
    d = dict(row)
    raw_status = str(d.get("status") or "").strip().lower()
    new_status = _STATUS_TRANSLATION.get(raw_status, "active")
    last_run = d.get("last_run_at")
    last_surfaced_at = float(last_run) if last_run is not None else None
    # last_result_summary is only the resolution_reason if the order is
    # terminal (paused / disabled). For active orders the run history
    # lives in standing_order_runs; resolving it on the open_threads row
    # would imply a final state that doesn't exist.
    resolution_reason: str | None = None
    resolved_at: float | None = None
    if new_status == "dismissed":
        summary = d.get("last_result_summary")
        if summary:
            resolution_reason = str(summary)
        # Use last_run_at as the resolution timestamp when available;
        # else fall back to updated_at (the time the row entered its
        # paused/disabled state).
        resolved_at = last_surfaced_at if last_surfaced_at is not None else float(
            d.get("updated_at") or time.time()
        )
    payload = _build_source_payload(row)
    title = str(d.get("title") or "").strip() or "(untitled standing order)"
    return {
        "id": uuid.uuid4().hex,
        "owner_user_id": int(d["owner_user_id"]),
        "status": new_status,
        "created_at": float(d.get("created_at") or time.time()),
        "updated_at": float(d.get("updated_at") or time.time()),
        "topic_summary": title,
        "why_it_matters": _truncate_for_why_it_matters(d.get("instruction")),
        "kind": _derive_thread_kind(d.get("program_kind")),
        "urgency": DEFAULT_URGENCY,
        "last_surfaced_at": last_surfaced_at,
        "surface_count": 0,
        "resolved_at": resolved_at,
        "resolution_reason": resolution_reason,
        "source_kind": RECURRING_KIND,
        "source_id": str(d["standing_order_id"]),
        "source_payload": json.dumps(payload),
    }


def _select_standing_orders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(conn, "standing_orders"):
        return []
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(
        """
        SELECT standing_order_id, owner_user_id, title, instruction, program_kind,
               trigger_kind, trigger_spec, status, capability_status, delivery_policy,
               escalation_policy, workspace_root, next_run_at, last_run_at,
               last_result_summary, metadata, created_at, updated_at
        FROM standing_orders
        ORDER BY created_at ASC
        """
    )
    return list(cur.fetchall())


def _existing_source_ids(conn: sqlite3.Connection) -> set[str]:
    """Already-migrated source_ids — for idempotency on rerun."""
    if not _table_exists(conn, "open_threads"):
        return set()
    if not _open_threads_has_source_columns(conn):
        return set()
    cur = conn.execute(
        "SELECT source_id FROM open_threads "
        "WHERE source_kind = ? AND source_id IS NOT NULL",
        (RECURRING_KIND,),
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
    rows = _select_standing_orders(conn)
    already = _existing_source_ids(conn)
    migrated = 0
    skipped = 0
    for row in rows:
        source_id = str(row["standing_order_id"])
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
    has_table = _table_exists(conn, "standing_orders")
    if not has_table:
        print("standing_orders table: does not exist (already dropped)")
    else:
        count_row = conn.execute("SELECT COUNT(*) FROM standing_orders").fetchone()
        live_count = int(count_row[0]) if count_row else 0
        print(f"standing_orders table: exists; {live_count} rows live")
    if _table_exists(conn, "open_threads"):
        has_source = _open_threads_has_source_columns(conn)
        print(f"open_threads has source_* columns: {has_source}")
        if has_source:
            absorbed = conn.execute(
                "SELECT COUNT(*) FROM open_threads WHERE source_kind = ?",
                (RECURRING_KIND,),
            ).fetchone()
            print(
                f"open_threads with source_kind='{RECURRING_KIND}': "
                f"{int(absorbed[0])} rows"
            )
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

        rows_to_migrate = _select_standing_orders(conn)
        already = _existing_source_ids(conn)
        new_rows = [
            r
            for r in rows_to_migrate
            if str(r["standing_order_id"]) not in already
        ]
        print(
            f"Will migrate {len(new_rows)} new standing_orders row(s) "
            f"(of {len(rows_to_migrate)} total; "
            f"{len(rows_to_migrate) - len(new_rows)} already migrated)."
        )

        if not args.apply:
            print("\n(dry run — re-run with --apply to actually write)")
            return 0

        backup = db_path.with_suffix(
            db_path.suffix + ".pre-C8-standing-orders-migrate.bak"
        )
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
