"""Tests for scripts/migrate_proactive_actions_to_open_threads.py.

C1.5 step 2 of the unified completion refactor. Validates the
``proactive_actions`` -> ``open_threads`` migration end-to-end against a
synthetic temp-dir DB:

- mapping per the B5 design doc (column-by-column shape)
- idempotency (rerun is safe; rows already migrated are skipped)
- CHECK-constraint widening on pre-C1.5 installs (the SQLite
  table-rebuild path; status='logged' is rejected by old CHECK)
- 0-row case (the live DB on this install has 0 proactive_actions)
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest


def _load_migration_script():
    """Import the script from disk by path (it lives outside any package)."""
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "migrate_proactive_actions_to_open_threads.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migrate_proactive_actions_to_open_threads", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_proactive_actions_to_open_threads"] = module
    spec.loader.exec_module(module)
    return module


migrate = _load_migration_script()


def _create_pre_c1_5_open_threads(conn: sqlite3.Connection) -> None:
    """Create the open_threads table in its pre-C1.5 shape.

    No source_* columns; CHECK constraint excludes 'logged'. Mirrors
    what the live DB looked like before C1.5 step 1 ran on a fresh
    bootstrap.
    """
    conn.execute(
        """
        CREATE TABLE open_threads (
            id TEXT PRIMARY KEY,
            owner_user_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'resolved', 'dismissed')),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            topic_summary TEXT NOT NULL,
            why_it_matters TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('work', 'personal', 'health', 'curiosity', 'followup', 'other')),
            urgency TEXT NOT NULL,
            last_surfaced_at REAL,
            surface_count INTEGER NOT NULL DEFAULT 0,
            resolved_at REAL,
            resolution_reason TEXT
        )
        """
    )


def _create_proactive_actions_table(conn: sqlite3.Connection) -> None:
    """The legacy table. Matches sqlite_schema_catalog.py:246-259 + ALTERs."""
    conn.execute(
        """
        CREATE TABLE proactive_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id BIGINT NOT NULL,
            runtime_job_id TEXT,
            runtime_session_key TEXT,
            action_type TEXT NOT NULL,
            trigger_reason TEXT,
            content TEXT,
            scheduled_time REAL,
            executed_time REAL,
            success BOOLEAN,
            user_response TEXT,
            effectiveness_score REAL
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
    """Live DB on this install has 0 rows; the script must not error."""
    _create_pre_c1_5_open_threads(conn)
    _create_proactive_actions_table(conn)
    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 0, "migrated": 0, "skipped": 0}
    # Still widens the CHECK constraint as a side-effect (fresh bootstraps
    # need this to be idempotent with the no-row case).
    assert migrate._open_threads_has_logged_status(conn)


def test_migration_widens_status_check_for_pre_c1_5_installs(conn):
    """Pre-C1.5 CHECK rejects 'logged'; migration must rebuild the table."""
    _create_pre_c1_5_open_threads(conn)
    _create_proactive_actions_table(conn)
    # Insert a pre-existing topic row to make sure the table-rebuild
    # preserves data.
    conn.execute(
        """INSERT INTO open_threads
           (id, owner_user_id, status, created_at, updated_at,
            topic_summary, why_it_matters, kind, urgency,
            last_surfaced_at, surface_count, resolved_at, resolution_reason)
           VALUES ('topic_1', 1, 'active', 1000.0, 1000.0, 'pre-existing',
                   'reason', 'other', 'low', NULL, 0, NULL, NULL)""",
    )
    assert not migrate._open_threads_has_logged_status(conn)
    migrate._ensure_status_check_widened(conn)
    assert migrate._open_threads_has_logged_status(conn)
    # The pre-existing row survived.
    rows = conn.execute("SELECT id, topic_summary FROM open_threads").fetchall()
    assert rows == [("topic_1", "pre-existing")]
    # New 'logged' row now accepted.
    conn.execute(
        """INSERT INTO open_threads
           (id, owner_user_id, status, created_at, updated_at,
            topic_summary, why_it_matters, kind, urgency,
            last_surfaced_at, surface_count, resolved_at, resolution_reason,
            source_kind, source_id, source_payload)
           VALUES ('logged_1', 1, 'logged', 1000.0, 1000.0, 'history',
                   'reason', 'other', 'historical', NULL, 1, NULL, NULL,
                   'proactive_record', '42', '{}')""",
    )
    # Indexes also restored.
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='open_threads'"
    ).fetchall()
    idx_names = {r[0] for r in idx}
    assert "idx_open_threads_owner_status" in idx_names
    assert "idx_open_threads_owner_last_surfaced" in idx_names
    assert "idx_open_threads_source_kind" in idx_names


def test_migration_maps_columns_per_b5_design(conn):
    _create_pre_c1_5_open_threads(conn)
    _create_proactive_actions_table(conn)
    # Insert a complete proactive_actions row with realistic JSON
    # trigger_reason carrying a candidate_tick_timestamp (the field the
    # rumination guard reads).
    trigger_blob = json.dumps(
        {"candidate_tick_timestamp": "1810.123456", "candidate_text": "gentle check-in"}
    )
    conn.execute(
        """INSERT INTO proactive_actions
           (user_id, runtime_job_id, runtime_session_key, action_type,
            trigger_reason, content, scheduled_time, executed_time, success,
            user_response, effectiveness_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            7,
            "job_42",
            "owner:7:surface:abc",
            "proactive_message",
            trigger_blob,
            "Hey, want to talk about that thing?",
            1800.0,
            1810.0,
            True,
            "thanks",
            0.85,
        ),
    )

    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 1, "migrated": 1, "skipped": 0}

    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute(
        "SELECT * FROM open_threads WHERE source_kind = 'proactive_record'"
    ).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["owner_user_id"] == 7
    assert row["status"] == "logged"
    assert row["topic_summary"] == "Hey, want to talk about that thing?"
    assert row["why_it_matters"] == migrate.WHY_IT_MATTERS
    assert row["kind"] == "other"
    assert row["urgency"] == "historical"
    assert row["created_at"] == 1800.0
    assert row["updated_at"] == 1810.0
    assert row["last_surfaced_at"] == 1810.0
    assert row["resolved_at"] == 1810.0
    assert row["resolution_reason"] == "executed"
    assert row["surface_count"] == 1
    assert row["source_kind"] == "proactive_record"
    # Integer PK text-rendered (B5 Q1).
    assert row["source_id"] == "1"
    payload = json.loads(row["source_payload"])
    assert payload["runtime_job_id"] == "job_42"
    assert payload["runtime_session_key"] == "owner:7:surface:abc"
    assert payload["action_type"] == "proactive_message"
    # trigger_reason JSON-decoded (rumination guard now walks the dict).
    assert payload["original_trigger_reason"]["candidate_tick_timestamp"] == "1810.123456"
    assert payload["original_trigger_reason"]["candidate_text"] == "gentle check-in"
    assert payload["success"] is True
    assert payload["user_response"] == "thanks"
    assert payload["effectiveness_score"] == 0.85
    # Reserved keys for parallelism (B5 Q3).
    assert payload["original_instruction"] is None
    assert payload["original_why_now"] is None


def test_migration_marks_failed_with_correct_resolution_reason(conn):
    _create_pre_c1_5_open_threads(conn)
    _create_proactive_actions_table(conn)
    conn.execute(
        """INSERT INTO proactive_actions
           (user_id, action_type, content, scheduled_time, executed_time, success)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (1, "proactive_message", "msg", 1000.0, 1010.0, False),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT resolution_reason FROM open_threads WHERE source_kind='proactive_record'"
    ).fetchone()
    assert row[0] == "failed"


def test_migration_handles_unexecuted_action(conn):
    """scheduled but not yet executed → resolved_at IS NULL, no resolution_reason."""
    _create_pre_c1_5_open_threads(conn)
    _create_proactive_actions_table(conn)
    conn.execute(
        """INSERT INTO proactive_actions
           (user_id, action_type, content, scheduled_time, executed_time, success)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (1, "proactive_message", "msg", 1000.0, None, None),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        """SELECT created_at, updated_at, last_surfaced_at, resolved_at, resolution_reason
           FROM open_threads WHERE source_kind='proactive_record'"""
    ).fetchone()
    assert row[0] == 1000.0  # created_at = scheduled_time
    assert row[1] == 1000.0  # updated_at falls back to created_at
    assert row[2] is None    # last_surfaced_at unset (not executed)
    assert row[3] is None    # resolved_at unset (not executed)
    assert row[4] is None    # resolution_reason unset (success=NULL)


def test_migration_preserves_non_json_trigger_reason_as_string(conn):
    _create_pre_c1_5_open_threads(conn)
    _create_proactive_actions_table(conn)
    conn.execute(
        """INSERT INTO proactive_actions
           (user_id, action_type, content, scheduled_time, executed_time, trigger_reason)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (1, "proactive_message", "msg", 1000.0, 1010.0, "user_idle_for_3h"),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT source_payload FROM open_threads WHERE source_kind='proactive_record'"
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["original_trigger_reason"] == "user_idle_for_3h"


def test_migration_is_idempotent_on_rerun(conn):
    _create_pre_c1_5_open_threads(conn)
    _create_proactive_actions_table(conn)
    conn.execute(
        """INSERT INTO proactive_actions
           (user_id, action_type, content, scheduled_time, executed_time)
           VALUES (?, ?, ?, ?, ?)""",
        (1, "proactive_message", "msg1", 1000.0, 1010.0),
    )
    conn.execute(
        """INSERT INTO proactive_actions
           (user_id, action_type, content, scheduled_time, executed_time)
           VALUES (?, ?, ?, ?, ?)""",
        (1, "proactive_message", "msg2", 2000.0, 2010.0),
    )
    first = migrate.run_migration(conn)
    conn.commit()
    assert first == {"scanned": 2, "migrated": 2, "skipped": 0}
    second = migrate.run_migration(conn)
    conn.commit()
    assert second == {"scanned": 2, "migrated": 0, "skipped": 2}
    # Total absorbed rows still 2 (no double-insert).
    count = conn.execute(
        "SELECT COUNT(*) FROM open_threads WHERE source_kind='proactive_record'"
    ).fetchone()[0]
    assert count == 2


def test_migration_no_op_when_proactive_actions_already_dropped(conn):
    """Once C1.5 step 5 drops the table, rerun must succeed with 0 rows."""
    _create_pre_c1_5_open_threads(conn)
    # No proactive_actions table at all.
    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 0, "migrated": 0, "skipped": 0}
    assert migrate._open_threads_has_logged_status(conn)


def test_migration_handles_post_c1_5_open_threads_shape_directly(conn):
    """If open_threads already has source_* columns, no rebuild needed."""
    # Create the table in its post-C1.5 shape directly.
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
    _create_proactive_actions_table(conn)
    conn.execute(
        """INSERT INTO proactive_actions
           (user_id, action_type, content, scheduled_time, executed_time)
           VALUES (?, ?, ?, ?, ?)""",
        (1, "proactive_message", "msg", 1000.0, 1010.0),
    )
    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 1, "migrated": 1, "skipped": 0}
    row = conn.execute(
        "SELECT source_kind, source_id FROM open_threads"
    ).fetchone()
    assert row == ("proactive_record", "1")
