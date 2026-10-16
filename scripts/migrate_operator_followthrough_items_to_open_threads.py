#!/usr/bin/env python3
"""One-shot migration: absorb operator_followthrough_items into open_threads.

Phase C, Cluster C6 of the 2026-05-08 unified completion refactor.
Implements the FOLLOWTHROUGH absorb of the B5 design doc (queue-collapse
schema) — the **third and final third** of B5 Decision 5, after the
PROACTIVE_RECORD absorb (C1.5, ``proactive_actions``) and the RECURRING
absorb (C8, ``standing_orders``):

    docs/superpowers/plans/2026-05-08-decision-5-queue-collapse-schema.md

The legacy ``operator_followthrough_items`` table is collapsed into
``open_threads`` with ``source_kind='followthrough'``. Each row's full
typed payload is preserved in ``open_threads.source_payload`` JSON under
documented keys so post-migration writers/readers can round-trip the
typed fields that have no existing column on open_threads (workflow_id,
outcome_id, recommended_next_action, last_surface_kind, etc.).

The mirror columns on ``operator_workflows`` (``followthrough_status``,
``next_followthrough_review_at``, ``last_followthrough_surface_at``,
``latest_followthrough_id``) are intentionally PRESERVED per the C6 audit
and B5 keep-decision: ``latest_followthrough_id`` continues to point at
the legacy text PK value, which now also lives as
``open_threads.source_id`` — so the join keeps working unchanged.

Column mapping (per B5 doc, "source_kind = 'followthrough'" section,
plus the C6 controller-resolved Q5 status table):

    operator_followthrough_items   -> open_threads
    -----------------------------  -- ------------
    followthrough_id               -> source_id (TEXT, verbatim)
    workflow_id                    -> source_payload.workflow_id
    outcome_id                     -> source_payload.outcome_id
    owner_user_id                  -> owner_user_id
    status                         -> status (translated per B5 Q5):
                                       'open'                  -> 'active'
                                       'snoozed'               -> 'active' (with
                                          metadata.followthrough_snoozed=True
                                          and source_payload.next_action_at set
                                          to the snooze-until time)
                                       'surfaced'              -> 'active' (with
                                          metadata.last_surfaced_at updated)
                                       'resolved'              -> 'resolved'
                                          (resolution_reason='followthrough_resolved')
                                       'dismissed'             -> 'dismissed'
                                          (resolution_reason='followthrough_dismissed')
                                       'converted_to_reminder' -> 'resolved'
                                          (resolution_reason='converted_to_reminder';
                                           metadata.linked_reminder_id preserved)
                                     source_payload.original_status preserves
                                     the round-trip distinction.
    salience                       -> urgency (B5 Q6: salience column already
                                     uses {low, medium, high} which matches the
                                     free-text urgency field one-to-one)
    summary                        -> topic_summary
    why_now                        -> why_it_matters (B5 doc: closest semantic
                                     match — slightly lossy)
    recommended_next_action        -> source_payload.recommended_next_action
    next_review_at                 -> source_payload.next_action_at
                                     (B5 doc earmarked next_action_at as a
                                      top-level open_threads column but
                                      Step 0 was deferred — same as C8.
                                      Stash here until Phase E adds the column.)
    last_surfaced_at               -> last_surfaced_at
    last_surface_kind              -> source_payload.last_surface_kind
    linked_reminder_id             -> source_payload.linked_reminder_id
    metadata                       -> source_payload.metadata (verbatim)
    created_at                     -> created_at
    updated_at                     -> updated_at
    (computed)                     -> kind = 'followup' (B5 Q6: matches the
                                     existing ThreadKind.FOLLOWUP value)
    (computed)                     -> resolution_reason = derived from status
                                     (see Q5 table above) when the row is
                                     terminal; else NULL
    (computed)                     -> resolved_at = updated_at iff terminal
    (computed)                     -> surface_count = 0 (last_surface_kind is
                                     binary "did we surface yet?", not a count)

Plus reserved keys preserved for parallelism with the previous absorbs:
``original_instruction`` is None on FOLLOWTHROUGH (followthrough rows have
no instruction blob — the rationale lives in summary + why_now);
``original_why_now`` carries the legacy ``why_now`` value verbatim so
downstream readers retaining the legacy semantics get the un-mapped text.

Usage:
    python scripts/migrate_operator_followthrough_items_to_open_threads.py --status
    python scripts/migrate_operator_followthrough_items_to_open_threads.py
    python scripts/migrate_operator_followthrough_items_to_open_threads.py --apply

The default is dry-run. Pass ``--apply`` to write. Backs up the sqlite
to ``.pre-C6-followthrough.bak`` before any write so a rollback is
possible. Idempotent: re-running ``--apply`` is safe; rows already
migrated (matched by ``source_id`` + ``source_kind='followthrough'``)
are skipped.

Note (versus C1.5 / C8 precedent): this script does NOT widen the
open_threads CHECK(status) constraint — C1.5 already widened it to
include 'logged'. The FOLLOWTHROUGH absorb only writes status='active',
'resolved', or 'dismissed', all of which the post-C1.5 CHECK constraint
already permits. The schema is therefore a pure data-move + caller rewire.

Live DB note: this install has 0 rows in operator_followthrough_items at
the time C6 lands, so the data migration is ceremonial. The code rewire
(in C6 step 2) is the real work; this script exists for completeness and
for any install that does have rows.
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

FOLLOWTHROUGH_KIND = "followthrough"
THREAD_KIND_FOLLOWUP = "followup"
DEFAULT_URGENCY = "medium"

# Per B5 Q5 (controller-resolved table from the C6 handoff). Keys are the
# legacy operator_followthrough_items.status values; the value is the
# mapped open_threads.status. Resolution-reason and snooze/surface side
# effects are applied separately (see _build_open_threads_row).
_STATUS_TRANSLATION = {
    "open": "active",
    "snoozed": "active",
    "surfaced": "active",
    "resolved": "resolved",
    "dismissed": "dismissed",
    "converted_to_reminder": "resolved",
}

# Statuses that are considered terminal (write resolved_at + resolution_reason).
_TERMINAL_LEGACY_STATUSES = {"resolved", "dismissed", "converted_to_reminder"}

# Map terminal legacy statuses to the resolution_reason literal stored on
# the open_threads row (per the C6 handoff's Q5 table).
_RESOLUTION_REASON_FOR_TERMINAL = {
    "resolved": "followthrough_resolved",
    "dismissed": "followthrough_dismissed",
    "converted_to_reminder": "converted_to_reminder",
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


def _normalize_metadata(raw: Any) -> Any:
    """JSON-decode metadata if it's a TEXT blob; else return verbatim.

    operator_followthrough_items.metadata is JSONB on the schema but
    SQLite stores it as TEXT. Storing a decoded dict in
    source_payload['metadata'] keeps json_extract paths working on the
    unified store.
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


def _truncate_for_why_it_matters(why_now: Any) -> str:
    """Compress the why_now blob for the why_it_matters column.

    The full text is preserved verbatim in source_payload.original_why_now;
    why_it_matters is a NOT-NULL TEXT for display/index. Cap at 240 chars.
    """
    text = str(why_now or "").strip()
    if not text:
        return "Followthrough item (rationale unavailable)"
    if len(text) <= 240:
        return text
    return text[:237].rstrip() + "..."


def _derive_urgency(salience: Any) -> str:
    """Map followthrough salience to open_threads urgency.

    salience is one of {low, medium, high} per
    WORKFLOW_CONTINUITY_SALIENCE in core/operator_service.py:122. urgency
    on open_threads is free-text TEXT, so direct copy is safe. Any
    unrecognized value falls back to DEFAULT_URGENCY ('medium') per the
    C6 handoff's Q6.
    """
    text = str(salience or "").strip().lower()
    if text in {"low", "medium", "high"}:
        return text
    return DEFAULT_URGENCY


def _build_source_payload(row: sqlite3.Row) -> dict[str, Any]:
    """Construct the source_payload dict for one operator_followthrough_items row.

    All keys are nullable per the legacy schema. Reader paths
    (heartbeat follow-through dispatch, operator review snapshots, the
    reminders/scheduler conversion path) fetch documented keys and
    tolerate missing values.
    """
    d = dict(row)
    metadata_dict = _normalize_metadata(d.get("metadata"))
    if metadata_dict is None or not isinstance(metadata_dict, dict):
        metadata_dict = {} if metadata_dict is None else {"raw": metadata_dict}
    raw_status = str(d.get("status") or "").strip().lower()

    # Augment metadata with status-specific side-effect keys per Q5:
    # - 'snoozed' rows tag metadata.followthrough_snoozed=True so readers
    #   that want the round-trip snoozed/active distinction can detect it
    #   without consulting source_payload.original_status.
    # - 'surfaced' rows record metadata.last_surfaced_at (which is also
    #   on the open_threads top-level column for the surface predicate,
    #   but having it in metadata mirrors the legacy update path that
    #   bumped metadata on every surface event).
    if raw_status == "snoozed":
        metadata_dict.setdefault("followthrough_snoozed", True)
    if raw_status == "surfaced":
        last_surfaced = d.get("last_surfaced_at")
        if last_surfaced is not None:
            metadata_dict.setdefault("last_surfaced_at", float(last_surfaced))

    payload: dict[str, Any] = {
        "workflow_id": d.get("workflow_id"),
        "outcome_id": d.get("outcome_id"),
        "recommended_next_action": d.get("recommended_next_action"),
        # B5 doc earmarked next_action_at as a top-level column but
        # Step 0 was deferred — see migration script docstring. For
        # snoozed rows this also encodes the snooze-until time per Q5.
        "next_action_at": (
            float(d["next_review_at"]) if d.get("next_review_at") is not None else None
        ),
        "last_surface_kind": d.get("last_surface_kind"),
        "linked_reminder_id": (
            int(d["linked_reminder_id"])
            if d.get("linked_reminder_id") is not None
            else None
        ),
        # Round-trip preservation per B5 Q5 — readers that care about the
        # original 6-value distinction read this key, not the unified
        # 'status' column (which collapses the 6 into {active, resolved,
        # dismissed}).
        "original_status": raw_status or None,
        "metadata": metadata_dict,
        # Reserved keys for parallelism with the standing_orders absorb
        # (C8) and proactive_actions absorb (C1.5). FOLLOWTHROUGH has no
        # instruction-shaped field, so original_instruction is None;
        # original_why_now preserves the unmapped why_now text so
        # downstream readers retaining the legacy semantics see it.
        "original_instruction": None,
        "original_why_now": d.get("why_now"),
    }
    return payload


def _build_open_threads_row(row: sqlite3.Row) -> dict[str, Any]:
    """Map one operator_followthrough_items row to its open_threads INSERT shape."""
    d = dict(row)
    raw_status = str(d.get("status") or "").strip().lower()
    new_status = _STATUS_TRANSLATION.get(raw_status, "active")
    last_surfaced_at = (
        float(d["last_surfaced_at"]) if d.get("last_surfaced_at") is not None else None
    )
    # resolution_reason + resolved_at only apply to terminal rows.
    resolution_reason: str | None = None
    resolved_at: float | None = None
    if raw_status in _TERMINAL_LEGACY_STATUSES:
        resolution_reason = _RESOLUTION_REASON_FOR_TERMINAL[raw_status]
        # updated_at is the canonical "row entered terminal state" timestamp
        # for these rows (the legacy update path bumps updated_at on the
        # transition).
        resolved_at = float(d.get("updated_at") or time.time())
    payload = _build_source_payload(row)
    summary = str(d.get("summary") or "").strip() or "(untitled followthrough item)"
    return {
        "id": uuid.uuid4().hex,
        "owner_user_id": int(d["owner_user_id"]),
        "status": new_status,
        "created_at": float(d.get("created_at") or time.time()),
        "updated_at": float(d.get("updated_at") or time.time()),
        "topic_summary": summary,
        "why_it_matters": _truncate_for_why_it_matters(d.get("why_now")),
        "kind": THREAD_KIND_FOLLOWUP,
        "urgency": _derive_urgency(d.get("salience")),
        "last_surfaced_at": last_surfaced_at,
        "surface_count": 0,
        "resolved_at": resolved_at,
        "resolution_reason": resolution_reason,
        "source_kind": FOLLOWTHROUGH_KIND,
        "source_id": str(d["followthrough_id"]),
        "source_payload": json.dumps(payload),
    }


def _select_followthrough_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(conn, "operator_followthrough_items"):
        return []
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    cur.execute(
        """
        SELECT followthrough_id, workflow_id, outcome_id, owner_user_id, status, salience,
               summary, why_now, recommended_next_action, next_review_at, last_surfaced_at,
               last_surface_kind, linked_reminder_id, metadata, created_at, updated_at
        FROM operator_followthrough_items
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
        (FOLLOWTHROUGH_KIND,),
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
    rows = _select_followthrough_items(conn)
    already = _existing_source_ids(conn)
    migrated = 0
    skipped = 0
    for row in rows:
        source_id = str(row["followthrough_id"])
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
    has_table = _table_exists(conn, "operator_followthrough_items")
    if not has_table:
        print("operator_followthrough_items table: does not exist (already dropped)")
    else:
        count_row = conn.execute(
            "SELECT COUNT(*) FROM operator_followthrough_items"
        ).fetchone()
        live_count = int(count_row[0]) if count_row else 0
        print(
            f"operator_followthrough_items table: exists; {live_count} rows live"
        )
    if _table_exists(conn, "open_threads"):
        has_source = _open_threads_has_source_columns(conn)
        print(f"open_threads has source_* columns: {has_source}")
        if has_source:
            absorbed = conn.execute(
                "SELECT COUNT(*) FROM open_threads WHERE source_kind = ?",
                (FOLLOWTHROUGH_KIND,),
            ).fetchone()
            print(
                f"open_threads with source_kind='{FOLLOWTHROUGH_KIND}': "
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

        rows_to_migrate = _select_followthrough_items(conn)
        already = _existing_source_ids(conn)
        new_rows = [
            r
            for r in rows_to_migrate
            if str(r["followthrough_id"]) not in already
        ]
        print(
            f"Will migrate {len(new_rows)} new operator_followthrough_items "
            f"row(s) (of {len(rows_to_migrate)} total; "
            f"{len(rows_to_migrate) - len(new_rows)} already migrated)."
        )

        if not args.apply:
            print("\n(dry run — re-run with --apply to actually write)")
            return 0

        backup = db_path.with_suffix(
            db_path.suffix + ".pre-C6-followthrough.bak"
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
