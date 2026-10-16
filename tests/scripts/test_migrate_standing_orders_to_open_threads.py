"""Tests for scripts/migrate_standing_orders_to_open_threads.py.

C8 step 2 of the unified completion refactor. Validates the
``standing_orders`` -> ``open_threads`` migration end-to-end against a
synthetic temp-dir DB:

- mapping per the B5 design doc (column-by-column shape)
- idempotency (rerun is safe; rows already migrated are skipped)
- 0-row case (the live DB on this install has 1 row but the script
  must also handle the post-drop / fresh-bootstrap zero-row case)
- status translation (active/paused/disabled per B5 Q5)
- program_kind -> kind derivation (per B5 Q6)
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest


def _load_migration_script():
    """Import the script from disk by path (it lives outside any package)."""
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "migrate_standing_orders_to_open_threads.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migrate_standing_orders_to_open_threads", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_standing_orders_to_open_threads"] = module
    spec.loader.exec_module(module)
    return module


migrate = _load_migration_script()


def _create_post_c1_5_open_threads(conn: sqlite3.Connection) -> None:
    """Create open_threads in its post-C1.5 shape (source_* columns + 'logged' status)."""
    conn.execute(
        """
        CREATE TABLE open_threads (
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
    )


def _create_standing_orders_table(conn: sqlite3.Connection) -> None:
    """The legacy table. Matches sqlite_schema_catalog.py:293-313."""
    conn.execute(
        """
        CREATE TABLE standing_orders (
            standing_order_id TEXT PRIMARY KEY,
            owner_user_id BIGINT NOT NULL,
            title TEXT NOT NULL,
            instruction TEXT NOT NULL,
            program_kind TEXT NOT NULL,
            trigger_kind TEXT NOT NULL,
            trigger_spec TEXT,
            status TEXT NOT NULL,
            capability_status TEXT NOT NULL,
            delivery_policy TEXT,
            escalation_policy TEXT,
            workspace_root TEXT,
            next_run_at REAL,
            last_run_at REAL,
            last_result_summary TEXT,
            metadata TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        c = sqlite3.connect(str(db_path))
        try:
            yield c
        finally:
            c.close()


def test_migration_handles_zero_rows_gracefully(conn):
    """Post-drop / fresh-bootstrap: no rows to migrate."""
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 0, "migrated": 0, "skipped": 0}


def test_migration_no_op_when_standing_orders_already_dropped(conn):
    """Once C8 step 3 drops the table, rerun must succeed with 0 rows."""
    _create_post_c1_5_open_threads(conn)
    # No standing_orders table at all.
    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 0, "migrated": 0, "skipped": 0}


def test_migration_maps_columns_per_b5_design(conn):
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    trigger_blob = json.dumps(
        {"kind": "weekly", "weekdays": [0], "time": "09:00"}
    )
    metadata_blob = json.dumps(
        {
            "created_from_conversation": True,
            "thread_id": "owner:7:thread:main",
            "thread_kind": "owner_main",
            "continuity_refs": ["thread:owner:7:thread:main"],
        }
    )
    conn.execute(
        """INSERT INTO standing_orders
           (standing_order_id, owner_user_id, title, instruction, program_kind,
            trigger_kind, trigger_spec, status, capability_status, delivery_policy,
            escalation_policy, workspace_root, next_run_at, last_run_at,
            last_result_summary, metadata, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "so_test_aaa111",
            7,
            "Companion check-in: Monday call lead",
            "Lead the weekly Monday call about the Apple proposal status. "
            "User has had two prior calls; this is their first time leading.",
            "companion_checkin",
            "schedule",
            trigger_blob,
            "active",
            "executable",
            "quiet",
            "policy_guarded",
            None,
            1777294800.0,
            None,
            None,
            metadata_blob,
            1777291377.3,
            1777291377.3,
        ),
    )

    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 1, "migrated": 1, "skipped": 0}

    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute(
        "SELECT * FROM open_threads WHERE source_kind = 'recurring'"
    ).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["owner_user_id"] == 7
    assert row["status"] == "active"  # 'active' translates verbatim
    assert row["topic_summary"] == "Companion check-in: Monday call lead"
    # why_it_matters is the (possibly truncated) instruction body
    assert row["why_it_matters"].startswith("Lead the weekly Monday call")
    # kind derived per B5 Q6: companion_checkin -> personal
    assert row["kind"] == "personal"
    assert row["urgency"] == "recurring"
    assert row["created_at"] == 1777291377.3
    assert row["updated_at"] == 1777291377.3
    assert row["last_surfaced_at"] is None  # never run yet
    assert row["resolved_at"] is None
    assert row["resolution_reason"] is None
    assert row["surface_count"] == 0
    assert row["source_kind"] == "recurring"
    assert row["source_id"] == "so_test_aaa111"
    payload = json.loads(row["source_payload"])
    assert payload["program_kind"] == "companion_checkin"
    assert payload["trigger_kind"] == "schedule"
    # trigger_spec JSON-decoded into nested dict (not a string blob)
    assert payload["trigger_spec"]["kind"] == "weekly"
    assert payload["trigger_spec"]["weekdays"] == [0]
    assert payload["trigger_spec"]["time"] == "09:00"
    assert payload["original_status"] == "active"
    assert payload["capability_status"] == "executable"
    assert payload["delivery_policy"] == "quiet"
    assert payload["escalation_policy"] == "policy_guarded"
    assert payload["workspace_root"] is None
    assert payload["next_run_at"] == 1777294800.0
    assert payload["last_result_summary"] is None
    # metadata also JSON-decoded into nested dict
    assert payload["metadata"]["thread_kind"] == "owner_main"
    assert payload["metadata"]["continuity_refs"] == [
        "thread:owner:7:thread:main"
    ]
    # original_instruction preserves the full body (vs truncated why_it_matters)
    assert payload["original_instruction"].startswith(
        "Lead the weekly Monday call"
    )
    # Reserved keys for parallelism (B5 Q3).
    assert payload["original_why_now"] is None


def test_migration_translates_paused_to_dismissed(conn):
    """B5 Q5: paused -> dismissed; original_status preserves round-trip."""
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    conn.execute(
        """INSERT INTO standing_orders
           (standing_order_id, owner_user_id, title, instruction, program_kind,
            trigger_kind, status, capability_status,
            last_run_at, last_result_summary, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "so_paused_001",
            1,
            "Quarterly review",
            "Review quarterly metrics",
            "operator_program",
            "schedule",
            "paused",
            "executable",
            2000.0,
            "previous-run-summary",
            1000.0,
            2500.0,
        ),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT status, resolution_reason, resolved_at, source_payload "
        "FROM open_threads WHERE source_kind='recurring'"
    ).fetchone()
    assert row[0] == "dismissed"
    assert row[1] == "previous-run-summary"
    assert row[2] == 2000.0  # last_run_at as resolved_at
    payload = json.loads(row[3])
    assert payload["original_status"] == "paused"


def test_migration_translates_disabled_to_dismissed_no_run_history(conn):
    """disabled with no runs: resolved_at falls back to updated_at."""
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    conn.execute(
        """INSERT INTO standing_orders
           (standing_order_id, owner_user_id, title, instruction, program_kind,
            trigger_kind, status, capability_status,
            last_run_at, last_result_summary, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "so_disabled_001",
            1,
            "Old program",
            "Defunct",
            "research_watch",
            "schedule",
            "disabled",
            "executable",
            None,
            None,
            1000.0,
            3000.0,
        ),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT status, resolution_reason, resolved_at, source_payload "
        "FROM open_threads WHERE source_kind='recurring'"
    ).fetchone()
    assert row[0] == "dismissed"
    assert row[1] is None
    assert row[2] == 3000.0  # updated_at fallback
    payload = json.loads(row[3])
    assert payload["original_status"] == "disabled"


def test_migration_derives_kind_from_program_kind(conn):
    """B5 Q6 program_kind -> kind heuristic."""
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    expectations = [
        ("companion_checkin", "personal"),
        ("followthrough_review", "followup"),
        ("operator_program", "work"),
        ("research_watch", "work"),
        ("alert_watch", "work"),
        ("email_triage", "work"),
        ("custom_unknown_kind", "other"),  # fallback
    ]
    for idx, (program_kind, _) in enumerate(expectations):
        conn.execute(
            """INSERT INTO standing_orders
               (standing_order_id, owner_user_id, title, instruction, program_kind,
                trigger_kind, status, capability_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"so_kind_{idx}",
                1,
                f"order {idx}",
                "instr",
                program_kind,
                "schedule",
                "active",
                "executable",
                float(1000 + idx),
                float(1000 + idx),
            ),
        )
    migrate.run_migration(conn)
    conn.commit()
    rows = conn.execute(
        "SELECT source_id, kind FROM open_threads "
        "WHERE source_kind='recurring' ORDER BY created_at ASC"
    ).fetchall()
    assert len(rows) == len(expectations)
    for (source_id, derived_kind), (program_kind, expected_kind) in zip(
        rows, expectations
    ):
        assert derived_kind == expected_kind, (
            f"program_kind={program_kind} expected kind={expected_kind} "
            f"got kind={derived_kind} (source_id={source_id})"
        )


def test_migration_truncates_long_instruction_in_why_it_matters(conn):
    """The original_instruction is preserved verbatim; why_it_matters caps at 240."""
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    long_instr = "x" * 500
    conn.execute(
        """INSERT INTO standing_orders
           (standing_order_id, owner_user_id, title, instruction, program_kind,
            trigger_kind, status, capability_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "so_long_001",
            1,
            "Big brief",
            long_instr,
            "operator_program",
            "schedule",
            "active",
            "executable",
            1000.0,
            1000.0,
        ),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT why_it_matters, source_payload FROM open_threads "
        "WHERE source_kind='recurring'"
    ).fetchone()
    why_it_matters = row[0]
    assert len(why_it_matters) <= 240
    assert why_it_matters.endswith("...")
    payload = json.loads(row[1])
    # Full instruction preserved.
    assert payload["original_instruction"] == long_instr


def test_migration_handles_blank_instruction_with_placeholder(conn):
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    conn.execute(
        """INSERT INTO standing_orders
           (standing_order_id, owner_user_id, title, instruction, program_kind,
            trigger_kind, status, capability_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "so_blank_001",
            1,
            "Blank",
            "",
            "operator_program",
            "schedule",
            "active",
            "executable",
            1000.0,
            1000.0,
        ),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT why_it_matters FROM open_threads WHERE source_kind='recurring'"
    ).fetchone()
    assert row[0] == "Recurring program (instruction blank)"


def test_migration_preserves_non_json_trigger_spec_as_string(conn):
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    conn.execute(
        """INSERT INTO standing_orders
           (standing_order_id, owner_user_id, title, instruction, program_kind,
            trigger_kind, trigger_spec, status, capability_status,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "so_nonjson_001",
            1,
            "raw spec",
            "instr",
            "alert_watch",
            "event",
            "every-10-min",  # not JSON
            "active",
            "executable",
            1000.0,
            1000.0,
        ),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT source_payload FROM open_threads WHERE source_kind='recurring'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["trigger_spec"] == "every-10-min"


def test_migration_is_idempotent_on_rerun(conn):
    _create_post_c1_5_open_threads(conn)
    _create_standing_orders_table(conn)
    conn.execute(
        """INSERT INTO standing_orders
           (standing_order_id, owner_user_id, title, instruction, program_kind,
            trigger_kind, status, capability_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "so_idem_001",
            1,
            "ord1",
            "instr",
            "operator_program",
            "schedule",
            "active",
            "executable",
            1000.0,
            1000.0,
        ),
    )
    conn.execute(
        """INSERT INTO standing_orders
           (standing_order_id, owner_user_id, title, instruction, program_kind,
            trigger_kind, status, capability_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "so_idem_002",
            1,
            "ord2",
            "instr",
            "companion_checkin",
            "schedule",
            "active",
            "executable",
            2000.0,
            2000.0,
        ),
    )
    first = migrate.run_migration(conn)
    conn.commit()
    assert first == {"scanned": 2, "migrated": 2, "skipped": 0}
    second = migrate.run_migration(conn)
    conn.commit()
    assert second == {"scanned": 2, "migrated": 0, "skipped": 2}
    # Total absorbed rows still 2 (no double-insert).
    count = conn.execute(
        "SELECT COUNT(*) FROM open_threads WHERE source_kind='recurring'"
    ).fetchone()[0]
    assert count == 2
