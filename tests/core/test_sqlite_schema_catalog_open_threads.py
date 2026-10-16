"""open_threads schema definition is in the catalog with the correct columns."""
import sqlite3
import tempfile
from pathlib import Path

from core.sqlite_schema_catalog import ensure_catalog_schema_sync


def test_open_threads_table_created_with_expected_columns():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_catalog_schema_sync(conn)
            cur = conn.execute("PRAGMA table_info(open_threads)")
            cols = {row[1]: row[2] for row in cur.fetchall()}
        finally:
            conn.close()

    expected = {
        "id": "TEXT",
        "owner_user_id": "INTEGER",
        "status": "TEXT",
        "created_at": "REAL",
        "updated_at": "REAL",
        "topic_summary": "TEXT",
        "why_it_matters": "TEXT",
        "kind": "TEXT",
        "urgency": "TEXT",
        "last_surfaced_at": "REAL",
        "surface_count": "INTEGER",
        "resolved_at": "REAL",
        "resolution_reason": "TEXT",
    }
    for name, declared_type in expected.items():
        assert name in cols, f"missing column {name}"
        assert cols[name].upper().startswith(declared_type), (
            f"{name}: expected {declared_type}, got {cols[name]}"
        )


def test_open_threads_indexes_created():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_catalog_schema_sync(conn)
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='open_threads'"
            )
            idx_names = {row[0] for row in cur.fetchall()}
        finally:
            conn.close()

    assert "idx_open_threads_owner_status" in idx_names
    assert "idx_open_threads_owner_last_surfaced" in idx_names
