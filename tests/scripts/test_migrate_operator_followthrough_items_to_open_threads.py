"""Tests for scripts/migrate_operator_followthrough_items_to_open_threads.py.

C6 step 1 of the unified completion refactor. Validates the
``operator_followthrough_items`` -> ``open_threads`` migration end-to-end
against a synthetic temp-dir DB:

- mapping per the B5 design doc (column-by-column shape)
- idempotency (rerun is safe; rows already migrated are skipped)
- 0-row case (the live DB on this install has 0 rows; the script must
  also handle the post-drop / fresh-bootstrap zero-row case)
- status translation per the C6 handoff Q5 table (6 input statuses
  collapse to {active, resolved, dismissed} with documented
  resolution_reason and metadata side effects)
- salience -> urgency derivation
- kind hard-coded to 'followup'
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
        / "migrate_operator_followthrough_items_to_open_threads.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migrate_operator_followthrough_items_to_open_threads", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_operator_followthrough_items_to_open_threads"] = module
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


def _create_followthrough_table(conn: sqlite3.Connection) -> None:
    """The legacy table. Matches sqlite_schema_catalog.py:1064-1081."""
    conn.execute(
        """
        CREATE TABLE operator_followthrough_items (
            followthrough_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            outcome_id TEXT NOT NULL,
            owner_user_id BIGINT NOT NULL,
            status TEXT NOT NULL,
            salience TEXT NOT NULL,
            summary TEXT NOT NULL,
            why_now TEXT,
            recommended_next_action TEXT NOT NULL,
            next_review_at REAL,
            last_surfaced_at REAL,
            last_surface_kind TEXT,
            linked_reminder_id BIGINT,
            metadata TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )


def _insert_followthrough(
    conn: sqlite3.Connection,
    *,
    followthrough_id: str = "ft_test_001",
    workflow_id: str = "wf_test_001",
    outcome_id: str = "out_test_001",
    owner_user_id: int = 7,
    status: str = "open",
    salience: str = "medium",
    summary: str = "Followthrough summary",
    why_now: str = "Why now rationale",
    recommended_next_action: str = "answer_question",
    next_review_at: float | None = None,
    last_surfaced_at: float | None = None,
    last_surface_kind: str | None = None,
    linked_reminder_id: int | None = None,
    metadata: str | None = None,
    created_at: float = 1000.0,
    updated_at: float = 2000.0,
) -> None:
    conn.execute(
        """INSERT INTO operator_followthrough_items
           (followthrough_id, workflow_id, outcome_id, owner_user_id, status, salience,
            summary, why_now, recommended_next_action, next_review_at, last_surfaced_at,
            last_surface_kind, linked_reminder_id, metadata, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            followthrough_id,
            workflow_id,
            outcome_id,
            owner_user_id,
            status,
            salience,
            summary,
            why_now,
            recommended_next_action,
            next_review_at,
            last_surfaced_at,
            last_surface_kind,
            linked_reminder_id,
            metadata,
            created_at,
            updated_at,
        ),
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
    """Live install case: operator_followthrough_items table exists, 0 rows."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 0, "migrated": 0, "skipped": 0}


def test_migration_no_op_when_followthrough_already_dropped(conn):
    """Once C6 step 3 drops the table, rerun must succeed with 0 rows."""
    _create_post_c1_5_open_threads(conn)
    # No operator_followthrough_items table at all.
    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 0, "migrated": 0, "skipped": 0}


def test_migration_maps_columns_per_b5_design(conn):
    """Full mapping check on a richly-populated 'open' row."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    metadata_blob = json.dumps(
        {"surface": "mission_control", "extra": "ctx"}
    )
    _insert_followthrough(
        conn,
        followthrough_id="ft_main_001",
        workflow_id="wf_main_001",
        outcome_id="out_main_001",
        owner_user_id=7,
        status="open",
        salience="high",
        summary="Still waiting on the owner reply.",
        why_now="The repo can resume immediately after an answer.",
        recommended_next_action="answer_question",
        next_review_at=2400.0,
        last_surfaced_at=2350.0,
        last_surface_kind="review",
        linked_reminder_id=17,
        metadata=metadata_blob,
        created_at=2250.0,
        updated_at=2500.0,
    )

    result = migrate.run_migration(conn)
    conn.commit()
    assert result == {"scanned": 1, "migrated": 1, "skipped": 0}

    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute(
        "SELECT * FROM open_threads WHERE source_kind = 'followthrough'"
    ).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["owner_user_id"] == 7
    # 'open' translates to 'active' (Q5).
    assert row["status"] == "active"
    assert row["topic_summary"] == "Still waiting on the owner reply."
    # why_it_matters carries the (possibly truncated) why_now text.
    assert row["why_it_matters"].startswith("The repo can resume")
    # kind is hard-coded 'followup' (B5 Q6).
    assert row["kind"] == "followup"
    # salience='high' -> urgency='high' (B5 Q6 free-text passthrough).
    assert row["urgency"] == "high"
    assert row["created_at"] == 2250.0
    assert row["updated_at"] == 2500.0
    assert row["last_surfaced_at"] == 2350.0
    # 'open' is non-terminal — no resolved_at / resolution_reason yet.
    assert row["resolved_at"] is None
    assert row["resolution_reason"] is None
    assert row["surface_count"] == 0
    assert row["source_kind"] == "followthrough"
    assert row["source_id"] == "ft_main_001"
    payload = json.loads(row["source_payload"])
    assert payload["workflow_id"] == "wf_main_001"
    assert payload["outcome_id"] == "out_main_001"
    assert payload["recommended_next_action"] == "answer_question"
    assert payload["next_action_at"] == 2400.0
    assert payload["last_surface_kind"] == "review"
    assert payload["linked_reminder_id"] == 17
    assert payload["original_status"] == "open"
    assert payload["metadata"]["surface"] == "mission_control"
    assert payload["metadata"]["extra"] == "ctx"
    # original_instruction None on FOLLOWTHROUGH; original_why_now preserved.
    assert payload["original_instruction"] is None
    assert payload["original_why_now"] == "The repo can resume immediately after an answer."


def test_migration_translates_snoozed_to_active_with_metadata_flag(conn):
    """B5 Q5: snoozed -> active with metadata.followthrough_snoozed=True."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    _insert_followthrough(
        conn,
        followthrough_id="ft_snoozed_001",
        status="snoozed",
        next_review_at=8888.0,  # snooze-until time
        metadata=json.dumps({"already": "set"}),
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT status, source_payload FROM open_threads WHERE source_kind='followthrough'"
    ).fetchone()
    assert row[0] == "active"
    payload = json.loads(row[1])
    assert payload["original_status"] == "snoozed"
    assert payload["next_action_at"] == 8888.0
    assert payload["metadata"]["followthrough_snoozed"] is True
    # Pre-existing metadata keys preserved.
    assert payload["metadata"]["already"] == "set"


def test_migration_translates_surfaced_to_active_records_last_surfaced(conn):
    """B5 Q5: surfaced -> active with metadata.last_surfaced_at."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    _insert_followthrough(
        conn,
        followthrough_id="ft_surfaced_001",
        status="surfaced",
        last_surfaced_at=7777.5,
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT status, last_surfaced_at, source_payload "
        "FROM open_threads WHERE source_kind='followthrough'"
    ).fetchone()
    assert row[0] == "active"
    # The top-level open_threads column also carries last_surfaced_at
    # (verbatim copy from the legacy column).
    assert row[1] == 7777.5
    payload = json.loads(row[2])
    assert payload["original_status"] == "surfaced"
    assert payload["metadata"]["last_surfaced_at"] == 7777.5


def test_migration_translates_resolved_with_correct_resolution_reason(conn):
    """B5 Q5: resolved -> 'resolved' (resolution_reason='followthrough_resolved')."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    _insert_followthrough(
        conn,
        followthrough_id="ft_resolved_001",
        status="resolved",
        updated_at=9000.0,
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT status, resolution_reason, resolved_at, source_payload "
        "FROM open_threads WHERE source_kind='followthrough'"
    ).fetchone()
    assert row[0] == "resolved"
    assert row[1] == "followthrough_resolved"
    assert row[2] == 9000.0  # updated_at as resolved_at
    payload = json.loads(row[3])
    assert payload["original_status"] == "resolved"


def test_migration_translates_dismissed_with_correct_resolution_reason(conn):
    """B5 Q5: dismissed -> 'dismissed' (resolution_reason='followthrough_dismissed')."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    _insert_followthrough(
        conn,
        followthrough_id="ft_dismissed_001",
        status="dismissed",
        updated_at=11111.0,
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT status, resolution_reason, resolved_at FROM open_threads "
        "WHERE source_kind='followthrough'"
    ).fetchone()
    assert row[0] == "dismissed"
    assert row[1] == "followthrough_dismissed"
    assert row[2] == 11111.0


def test_migration_translates_converted_to_reminder_preserves_link(conn):
    """B5 Q5: converted_to_reminder -> 'resolved'
    (resolution_reason='converted_to_reminder';
    metadata.linked_reminder_id preserved)."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    _insert_followthrough(
        conn,
        followthrough_id="ft_conv_001",
        status="converted_to_reminder",
        linked_reminder_id=42,
        updated_at=12345.0,
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT status, resolution_reason, resolved_at, source_payload "
        "FROM open_threads WHERE source_kind='followthrough'"
    ).fetchone()
    assert row[0] == "resolved"
    assert row[1] == "converted_to_reminder"
    assert row[2] == 12345.0
    payload = json.loads(row[3])
    # Q5 specifically calls out preserving metadata.linked_reminder_id;
    # the migration writes it as a top-level source_payload key (matching
    # the recommended payload shape) and the legacy linked_reminder_id
    # column also lives there for round-trip.
    assert payload["linked_reminder_id"] == 42
    assert payload["original_status"] == "converted_to_reminder"


def test_migration_derives_urgency_from_salience(conn):
    """B5 Q6: salience {low, medium, high} -> urgency one-to-one; fallback medium."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    expectations = [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("urgent", "medium"),  # unknown -> default
        ("", "medium"),
    ]
    for idx, (salience, _) in enumerate(expectations):
        _insert_followthrough(
            conn,
            followthrough_id=f"ft_sal_{idx}",
            salience=salience,
            created_at=float(1000 + idx),
            updated_at=float(1000 + idx),
        )
    migrate.run_migration(conn)
    conn.commit()
    rows = conn.execute(
        "SELECT source_id, urgency FROM open_threads "
        "WHERE source_kind='followthrough' ORDER BY created_at ASC"
    ).fetchall()
    assert len(rows) == len(expectations)
    for (source_id, urgency), (salience, expected) in zip(rows, expectations):
        assert urgency == expected, (
            f"salience={salience!r} expected urgency={expected!r} "
            f"got urgency={urgency!r} (source_id={source_id})"
        )


def test_migration_truncates_long_why_now_in_why_it_matters(conn):
    """The original_why_now is preserved verbatim; why_it_matters caps at 240."""
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    long_why = "y" * 500
    _insert_followthrough(
        conn,
        followthrough_id="ft_long_001",
        why_now=long_why,
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT why_it_matters, source_payload FROM open_threads "
        "WHERE source_kind='followthrough'"
    ).fetchone()
    why_it_matters = row[0]
    assert len(why_it_matters) <= 240
    assert why_it_matters.endswith("...")
    payload = json.loads(row[1])
    # Full why_now preserved.
    assert payload["original_why_now"] == long_why


def test_migration_handles_blank_why_now_with_placeholder(conn):
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    _insert_followthrough(
        conn,
        followthrough_id="ft_blank_001",
        why_now="",
    )
    migrate.run_migration(conn)
    conn.commit()
    row = conn.execute(
        "SELECT why_it_matters FROM open_threads WHERE source_kind='followthrough'"
    ).fetchone()
    assert row[0] == "Followthrough item (rationale unavailable)"


def test_migration_is_idempotent_on_rerun(conn):
    _create_post_c1_5_open_threads(conn)
    _create_followthrough_table(conn)
    _insert_followthrough(
        conn,
        followthrough_id="ft_idem_001",
        created_at=1000.0,
        updated_at=1000.0,
    )
    _insert_followthrough(
        conn,
        followthrough_id="ft_idem_002",
        status="resolved",
        created_at=2000.0,
        updated_at=2500.0,
    )
    first = migrate.run_migration(conn)
    conn.commit()
    assert first == {"scanned": 2, "migrated": 2, "skipped": 0}
    second = migrate.run_migration(conn)
    conn.commit()
    assert second == {"scanned": 2, "migrated": 0, "skipped": 2}
    # Total absorbed rows still 2 (no double-insert).
    count = conn.execute(
        "SELECT COUNT(*) FROM open_threads WHERE source_kind='followthrough'"
    ).fetchone()[0]
    assert count == 2
