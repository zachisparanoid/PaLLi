#!/usr/bin/env python
"""Great Excision stage 7 — drop dead tables (2026-07-09/10).

Drops the tables whose writers were deleted in excision stages 2-5, plus
two stale artifacts. Every dropped table was verified BOTH empty (except
the two stale ones, noted) AND writer-free in surviving production code
at the time of the migration; the full verdict table lives in the stage-7
commit message and docs/plans/2026-07-09-operator-salvage-map.md points
back to the pre-excision tag for recovery.

Safety:
- REFUSES to run unless a data/palli-local.sqlite3.pre-excision-*.bak
  snapshot exists next to the target DB (house convention).
- Re-runnable: DROP TABLE IF EXISTS / DELETE with IF EXISTS semantics.
- Prints per-table row counts before dropping; aborts if a to-drop table
  unexpectedly has rows (unless listed in EXPECTED_NONEMPTY).

Usage:
    venv/bin/python scripts/migrate_2026_07_10_excision_drop_dead_tables.py           # dry run
    venv/bin/python scripts/migrate_2026_07_10_excision_drop_dead_tables.py --apply
"""
from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DROP_TABLES = [
    # SaaS shell (stage 5)
    "account_deletion_tokens",
    "beta_seat_reservations",
    "beta_waitlist",
    "email_events",
    "email_verification_tokens",
    "password_reset_tokens",
    "sessions",
    "stripe_webhook_events",
    # operator / agentic / device / heartbeat (stage 4)
    "agent_pending_confirmations",
    "agent_session_deliveries",
    "agent_session_entries",
    "cross_device_approvals",
    "device_node_artifacts",
    "device_node_invocations",
    "device_node_pairings",
    "operator_workflow_checkpoints",
    "operator_workflow_findings",
    "operator_workflow_memories",
    "operator_workflow_outcomes",
    "operator_workflow_questions",
    "operator_workflow_steps",
    "operator_workflows",
    # bounded reflection + contradiction auditor (stage 2)
    "reflection_runs",
    "user_fact_contradictions",
    # long-dead analytics
    "learning_analytics",
    # stale artifacts (rows expected; content obsolete)
    "heartbeat_runs",
    "companion_agenda_items_pre_rebuild_backup",
]

EXPECTED_NONEMPTY = {"heartbeat_runs", "companion_agenda_items_pre_rebuild_backup"}

# Stale plugin-registry rows for builtin plugins whose specs were removed;
# catalog sync ignores them, this is cosmetic cleanup.
STALE_PLUGIN_ROWS = [
    "builtin.service.bounded_reflection",
    "builtin.service.device_nodes",
    "builtin.agent.tools.core",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually run the drops (default: dry run)")
    parser.add_argument("--database", default=os.environ.get("DATABASE_PATH", "data/palli-local.sqlite3"))
    args = parser.parse_args()

    db_path = args.database
    if not os.path.isabs(db_path):
        db_path = os.path.join(REPO_ROOT, db_path)
    if not os.path.exists(db_path):
        print(f"ERROR: database not found: {db_path}")
        return 2

    snapshots = glob.glob(os.path.join(os.path.dirname(db_path), "*.pre-excision-*.bak"))
    if not snapshots:
        print("ERROR: refusing to run — no *.pre-excision-*.bak snapshot found next to the DB.")
        print("Take one first:  cp data/palli-local.sqlite3 data/palli-local.sqlite3.pre-excision-<date>.bak")
        return 2

    con = sqlite3.connect(db_path)
    try:
        existing = {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        plan: list[tuple[str, int]] = []
        for table in DROP_TABLES:
            if table not in existing:
                print(f"  skip   {table} (already gone)")
                continue
            count = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count and table not in EXPECTED_NONEMPTY:
                print(f"ABORT: {table} has {count} rows but was verified empty — investigate before dropping.")
                return 3
            plan.append((table, count))
            print(f"  drop   {table} ({count} rows)")

        stale_rows = 0
        if "plugin_runtime_plugins" in existing:
            placeholders = ",".join("?" for _ in STALE_PLUGIN_ROWS)
            stale_rows = con.execute(
                f"SELECT COUNT(*) FROM plugin_runtime_plugins WHERE plugin_id IN ({placeholders})",
                STALE_PLUGIN_ROWS,
            ).fetchone()[0]
            print(f"  clean  plugin_runtime_plugins stale rows: {stale_rows}")

        if not args.apply:
            print("\nDRY RUN — re-run with --apply to execute.")
            return 0

        for table, _count in plan:
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
        if "plugin_runtime_plugins" in existing and stale_rows:
            placeholders = ",".join("?" for _ in STALE_PLUGIN_ROWS)
            con.execute(
                f"DELETE FROM plugin_runtime_plugins WHERE plugin_id IN ({placeholders})",
                STALE_PLUGIN_ROWS,
            )
        con.commit()
        con.execute("VACUUM")
        print(f"\nDropped {len(plan)} tables, cleaned {stale_rows} stale plugin rows, VACUUMed.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
