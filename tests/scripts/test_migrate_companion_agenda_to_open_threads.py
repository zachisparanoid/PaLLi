"""Migration: backup companion_agenda_items → drop → confirm open_threads usable."""
import sqlite3
import tempfile
from pathlib import Path

from scripts.migrate_companion_agenda_to_open_threads import run_migration
from core.sqlite_schema_catalog import ensure_catalog_schema_sync


def test_migration_creates_backup_table():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_catalog_schema_sync(conn)
            # The schema catalog no longer ships the legacy table (it was
            # stripped post-cutover), so simulate a pre-migration database
            # by creating it here before seeding.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS companion_agenda_items ("
                " item_id TEXT PRIMARY KEY,"
                " owner_user_id INTEGER,"
                " subject_user_id INTEGER,"
                " platform TEXT,"
                " kind TEXT,"
                " topic_key TEXT,"
                " title TEXT,"
                " summary TEXT,"
                " follow_up_seed TEXT,"
                " status TEXT,"
                " salience REAL,"
                " next_eligible_at REAL,"
                " last_captured_at REAL,"
                " created_at REAL,"
                " updated_at REAL"
                ")"
            )
            # Seed the legacy table with one row
            conn.execute(
                "INSERT INTO companion_agenda_items "
                "(item_id, owner_user_id, subject_user_id, platform, kind, "
                " topic_key, title, summary, follow_up_seed, status, salience, "
                " next_eligible_at, last_captured_at, created_at, updated_at) "
                "VALUES ('id1', 1, 1, 'local', 'follow_up', 'tk', 'title', "
                "'summary', 'follow', 'active', 0.5, 0, 0, 0, 0)"
            )
            conn.commit()

            run_migration(conn)

            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'companion_agenda_items%'"
            )
            tables = {row[0] for row in cur.fetchall()}
        finally:
            conn.close()

    assert "companion_agenda_items_pre_rebuild_backup" in tables


def test_migration_drops_original_table():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_catalog_schema_sync(conn)
            run_migration(conn)
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='companion_agenda_items'"
            )
            still_present = cur.fetchall()
        finally:
            conn.close()

    assert still_present == []


def test_migration_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.sqlite3"
        conn = sqlite3.connect(str(db_path))
        try:
            ensure_catalog_schema_sync(conn)
            run_migration(conn)
            run_migration(conn)  # second call must not raise
        finally:
            conn.close()
