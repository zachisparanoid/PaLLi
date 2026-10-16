"""Tests for the migrate_last_session_closed_at one-shot migration.

Phase B / B4 step 1 of the 2026-05-08 completion refactor. The
migration moves `last_session_closed_at` rows out of `user_memory`
(where they pollute decay-ranked recall) into a new column on
`users`. See IM-HIGH-2.

Tests load the script as a module via importlib (the same pattern
test_wipe_palli_memory.py uses) and exercise the helpers directly,
avoiding the live CLI invocation that has historically hung in the
test runner.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "migrate_last_session_closed_at.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migrate_last_session_closed_at_test_module", module_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_db(tmp_path: Path) -> Path:
    """Build a sqlite fixture mirroring the relevant subset of the
    PaLLi schema after the migration's schema-change applies (i.e.
    `users.last_session_closed_at` already exists).
    """
    db_path = tmp_path / "palli.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                last_session_closed_at REAL
            );
            CREATE TABLE user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                importance_score REAL DEFAULT 0.5,
                created_at REAL NOT NULL,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                tags TEXT,
                last_referenced_at REAL DEFAULT 0,
                reference_count INTEGER DEFAULT 0,
                salience_weight REAL DEFAULT 1.0,
                status TEXT DEFAULT 'active',
                superseded_by_fact_id TEXT,
                source_detail TEXT
            );
            INSERT INTO users (id, email, last_session_closed_at)
              VALUES (1, 'local@palli.local', NULL);
            """
        )
    return db_path


def _insert_session_close(conn: sqlite3.Connection, user_id: int, ts: float, reason: str = "unspecified") -> int:
    cur = conn.execute(
        """
        INSERT INTO user_memory
          (user_id, memory_type, content, importance_score, created_at, tags, source_detail)
        VALUES (?, 'user_fact', ?, 1.0, ?,
                '["last_session_closed_at", "reflect.session_close"]',
                ?)
        """,
        (
            user_id,
            f"last_session_closed_at: {ts:.0f}|{reason}",
            ts,
            f'{{"source": "reflect.session_close", "fact_key": "last_session_closed_at", "fact_value": "{ts:.0f}|{reason}", "confidence": 1.0}}',
        ),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _insert_other_user_fact(conn: sqlite3.Connection, user_id: int, key: str, value: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO user_memory
          (user_id, memory_type, content, importance_score, created_at, tags, source_detail)
        VALUES (?, 'user_fact', ?, 0.7, 0.0,
                ?,
                ?)
        """,
        (
            user_id,
            f"{key}: {value}",
            f'["{key}", "reflect"]',
            f'{{"source": "reflect", "fact_key": "{key}", "fact_value": "{value}", "confidence": 0.7}}',
        ),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


# =============================================================================
# Pure helpers
# =============================================================================


def test_extract_timestamp_parses_canonical_content():
    module = _load_module()
    assert module._extract_timestamp("last_session_closed_at: 1778197025|unspecified") == 1778197025.0
    assert module._extract_timestamp("last_session_closed_at: 1778197025|user-said-goodnight") == 1778197025.0


def test_extract_timestamp_returns_none_for_unparseable():
    module = _load_module()
    assert module._extract_timestamp("last_session_closed_at: not-a-number|x") is None
    assert module._extract_timestamp("totally unrelated content") is None
    assert module._extract_timestamp("") is None


# =============================================================================
# Plan: read-only inspection of the DB
# =============================================================================


def test_plan_migration_finds_no_rows_when_table_is_empty(tmp_path):
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        plan = module._plan_migration(conn)
    assert plan == {}


def test_plan_migration_groups_rows_by_user_and_picks_latest(tmp_path):
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        # Three rows for user 1, increasing timestamps
        rid_a = _insert_session_close(conn, user_id=1, ts=1000.0)
        rid_b = _insert_session_close(conn, user_id=1, ts=2500.0)
        rid_c = _insert_session_close(conn, user_id=1, ts=2000.0)
        # An unrelated user_fact for user 1 — must NOT be touched
        _insert_other_user_fact(conn, user_id=1, key="favorite_color", value="blue")
        conn.commit()

        plan = module._plan_migration(conn)

    assert set(plan.keys()) == {1}
    entry = plan[1]
    assert set(entry["row_ids"]) == {rid_a, rid_b, rid_c}
    assert entry["latest_ts"] == 2500.0
    assert entry["current_value"] is None


def test_plan_migration_includes_current_users_value(tmp_path):
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE users SET last_session_closed_at = ? WHERE id = ?", (5000.0, 1))
        _insert_session_close(conn, user_id=1, ts=2500.0)
        conn.commit()

        plan = module._plan_migration(conn)

    assert plan[1]["current_value"] == 5000.0
    assert plan[1]["latest_ts"] == 2500.0


# =============================================================================
# Apply: actual migration + idempotence
# =============================================================================


def test_apply_migration_writes_latest_to_users_and_deletes_user_memory_rows(tmp_path):
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        _insert_session_close(conn, user_id=1, ts=1000.0)
        _insert_session_close(conn, user_id=1, ts=2500.0)
        _insert_session_close(conn, user_id=1, ts=2000.0)
        # Unrelated user_fact must survive
        keep_id = _insert_other_user_fact(conn, user_id=1, key="favorite_color", value="blue")
        conn.commit()

        plan = module._plan_migration(conn)
        result = module._apply_migration(conn, plan)
        conn.commit()

        # users column updated to LATEST timestamp
        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?", (1,)
        ).fetchone()
        assert row[0] == 2500.0

        # All session-close rows deleted
        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM user_memory
            WHERE memory_type = 'user_fact'
              AND content LIKE 'last_session_closed_at:%'
            """
        ).fetchone()[0]
        assert remaining == 0

        # Unrelated row preserved
        keep_row = conn.execute(
            "SELECT id FROM user_memory WHERE id = ?", (keep_id,)
        ).fetchone()
        assert keep_row is not None

    assert result["users_updated"] == 1
    assert result["rows_deleted"] == 3


def test_apply_migration_takes_max_when_users_already_has_newer_value(tmp_path):
    """If users.last_session_closed_at is already newer than any
    user_memory row (e.g. the new writer landed first), the migration
    must NOT move backwards; it should keep the newer value but still
    delete the stale rows."""
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE users SET last_session_closed_at = ? WHERE id = ?", (9999.0, 1))
        _insert_session_close(conn, user_id=1, ts=1000.0)
        _insert_session_close(conn, user_id=1, ts=2500.0)
        conn.commit()

        plan = module._plan_migration(conn)
        result = module._apply_migration(conn, plan)
        conn.commit()

        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?", (1,)
        ).fetchone()
        # users still has the newer value
        assert row[0] == 9999.0

        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM user_memory
            WHERE memory_type = 'user_fact'
              AND content LIKE 'last_session_closed_at:%'
            """
        ).fetchone()[0]
        assert remaining == 0

    # users.last_session_closed_at unchanged (no UPDATE issued because
    # the existing value is already the max), but stale rows were still
    # deleted.
    assert result["users_updated"] == 0
    assert result["rows_deleted"] == 2


def test_apply_migration_is_idempotent(tmp_path):
    """Re-running --apply on an already-migrated DB is a no-op."""
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        _insert_session_close(conn, user_id=1, ts=2500.0)
        conn.commit()

        # First run
        plan1 = module._plan_migration(conn)
        result1 = module._apply_migration(conn, plan1)
        conn.commit()
        assert result1["users_updated"] == 1
        assert result1["rows_deleted"] == 1

        # Second run: nothing to do
        plan2 = module._plan_migration(conn)
        result2 = module._apply_migration(conn, plan2)
        conn.commit()
        assert plan2 == {}
        assert result2["users_updated"] == 0
        assert result2["rows_deleted"] == 0

        # users column still holds the migrated value
        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?", (1,)
        ).fetchone()
        assert row[0] == 2500.0


def test_apply_migration_no_op_when_user_has_no_session_close_rows(tmp_path):
    """A user with NO last_session_closed_at rows is not touched."""
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        # Only an unrelated fact
        _insert_other_user_fact(conn, user_id=1, key="favorite_color", value="blue")
        conn.commit()

        plan = module._plan_migration(conn)
        result = module._apply_migration(conn, plan)
        conn.commit()

        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?", (1,)
        ).fetchone()
        assert row[0] is None

    assert plan == {}
    assert result["users_updated"] == 0
    assert result["rows_deleted"] == 0


def test_apply_migration_skips_rows_with_unparseable_timestamp(tmp_path):
    """If a session-close row has an unparseable timestamp, it is
    skipped — the row is NOT deleted and does NOT contribute to the
    chosen max. This protects against accidental data loss if the
    content format ever drifts."""
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        good = _insert_session_close(conn, user_id=1, ts=2500.0)
        # Manually insert a malformed row
        conn.execute(
            """
            INSERT INTO user_memory
              (user_id, memory_type, content, importance_score, created_at, tags, source_detail)
            VALUES (?, 'user_fact', ?, 1.0, 0.0,
                    '["last_session_closed_at"]',
                    '{}')
            """,
            (1, "last_session_closed_at: not-a-number|x"),
        )
        conn.commit()

        plan = module._plan_migration(conn)
        result = module._apply_migration(conn, plan)
        conn.commit()

        # The good row was deleted; the malformed row remains for human review
        rows = conn.execute(
            """
            SELECT content FROM user_memory
            WHERE memory_type = 'user_fact' AND content LIKE 'last_session_closed_at:%'
            """
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "last_session_closed_at: not-a-number|x"

        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?", (1,)
        ).fetchone()
        assert row[0] == 2500.0

    assert result["users_updated"] == 1
    assert result["rows_deleted"] == 1
    # The malformed row was not counted in either users_updated or rows_deleted


# =============================================================================
# Self-prepare: the migration script can run against a DB whose schema
# bootstrap hasn't yet added the new column.
# =============================================================================


def _make_db_without_new_column(tmp_path: Path) -> Path:
    """Mirror an existing install whose schema bootstrap hasn't yet
    added users.last_session_closed_at. The migration script must
    self-add the column before reading or writing it."""
    db_path = tmp_path / "palli-no-col.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL
            );
            CREATE TABLE user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                importance_score REAL DEFAULT 0.5,
                created_at REAL NOT NULL,
                last_accessed REAL,
                access_count INTEGER DEFAULT 0,
                tags TEXT,
                last_referenced_at REAL DEFAULT 0,
                reference_count INTEGER DEFAULT 0,
                salience_weight REAL DEFAULT 1.0,
                status TEXT DEFAULT 'active',
                superseded_by_fact_id TEXT,
                source_detail TEXT
            );
            INSERT INTO users (id, email) VALUES (1, 'local@palli.local');
            """
        )
    return db_path


def test_users_has_last_session_closed_at_returns_false_when_missing(tmp_path):
    module = _load_module()
    db_path = _make_db_without_new_column(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert module._users_has_last_session_closed_at(conn) is False


def test_users_has_last_session_closed_at_returns_true_when_present(tmp_path):
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert module._users_has_last_session_closed_at(conn) is True


def test_ensure_column_exists_adds_missing_column(tmp_path):
    module = _load_module()
    db_path = _make_db_without_new_column(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        added = module._ensure_column_exists(conn)
        assert added is True
        assert module._users_has_last_session_closed_at(conn) is True


def test_ensure_column_exists_is_idempotent(tmp_path):
    module = _load_module()
    db_path = _make_db_without_new_column(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        added_first = module._ensure_column_exists(conn)
        added_second = module._ensure_column_exists(conn)
        assert added_first is True
        assert added_second is False
        # Column still present
        assert module._users_has_last_session_closed_at(conn) is True


def test_full_run_against_db_without_new_column(tmp_path):
    """End-to-end: migrate against a DB that doesn't have the new
    column yet. The script self-adds the column, then moves the rows."""
    module = _load_module()
    db_path = _make_db_without_new_column(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        _insert_session_close(conn, user_id=1, ts=2500.0)
        _insert_session_close(conn, user_id=1, ts=1000.0)
        conn.commit()

        module._ensure_column_exists(conn)
        plan = module._plan_migration(conn)
        result = module._apply_migration(conn, plan)
        conn.commit()

        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?", (1,)
        ).fetchone()
        assert row[0] == 2500.0

        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM user_memory
            WHERE memory_type = 'user_fact' AND content LIKE 'last_session_closed_at:%'
            """
        ).fetchone()[0]
        assert remaining == 0

    assert result["users_updated"] == 1
    assert result["rows_deleted"] == 2


# =============================================================================
# Schema catalog: the new column is in the canonical schema definition
# =============================================================================


def test_schema_catalog_users_includes_last_session_closed_at():
    """The canonical schema (used by initialize_sqlite_schema and by
    fresh installs that bypass the ALTER path) MUST include the new
    column in the CREATE TABLE for `users`."""
    from core.sqlite_schema_catalog import DEFAULT_SQLITE_BOOTSTRAP_PROGRAM as program

    users_ddl = next(
        ddl for ddl in program.ddl_statements if "CREATE TABLE IF NOT EXISTS users " in ddl
    )
    assert "last_session_closed_at" in users_ddl


def test_schema_catalog_includes_users_last_session_closed_at_alter():
    """Existing installs without the column rely on the ALTER TABLE
    migration entry to add it on the next bootstrap."""
    from core.sqlite_schema_catalog import DEFAULT_SQLITE_BOOTSTRAP_PROGRAM as program

    matches = [
        stmt
        for stmt in program.migration_statements
        if "ALTER TABLE users" in stmt and "last_session_closed_at" in stmt
    ]
    assert len(matches) == 1, matches


# =============================================================================
# Code-review fix tests (post-3e754fc)
# =============================================================================


def test_apply_migration_handles_corrupted_current_value(tmp_path, caplog):
    """Issue #1: if users.last_session_closed_at holds a non-numeric
    string (corrupted DB / format drift / manual SQL edit), the
    migration must NOT crash with ValueError mid-loop. It should
    treat the corrupt value as missing, log a warning, and overwrite
    with the migration's latest_ts.

    SQLite's REAL affinity coerces on insert in MOST cases but not
    when the column is added via ALTER TABLE without a typed value
    flowing through it. To force a TEXT value into the column, we
    PRAGMA writable_schema and bypass the affinity check, OR we
    simply use Python's sqlite3 binding with a string — sqlite3
    stores it as TEXT regardless of column affinity for sufficiently
    non-numeric inputs.
    """
    import logging

    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        # Inject a non-numeric string. sqlite3 has a "type affinity
        # but no strict type enforcement" model — REAL affinity will
        # try to coerce, and a fully non-numeric string falls back to
        # TEXT storage.
        conn.execute(
            "UPDATE users SET last_session_closed_at = 'garbage' WHERE id = ?",
            (1,),
        )
        conn.commit()
        # Sanity: verify the storage class really is TEXT.
        type_row = conn.execute(
            "SELECT typeof(last_session_closed_at) FROM users WHERE id = ?",
            (1,),
        ).fetchone()
        assert type_row[0] == "text", (
            f"Expected TEXT storage to exercise the bug, got {type_row[0]!r}"
        )

        _insert_session_close(conn, user_id=1, ts=2500.0)
        conn.commit()

        plan = module._plan_migration(conn)
        # Plan should still see the 'garbage' string as current_value
        # (typed parsing is _apply_migration's job, not plan's).
        assert plan[1]["current_value"] == "garbage"

        with caplog.at_level(logging.WARNING):
            result = module._apply_migration(conn, plan)
        conn.commit()

        # The migration's latest_ts overwrote the garbage value.
        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?", (1,)
        ).fetchone()
        assert row[0] == 2500.0

    assert result["users_updated"] == 1
    assert result["rows_deleted"] == 1
    # A WARNING was logged about the unparseable existing value.
    assert any(
        "unparseable" in str(rec.msg).lower() or "garbage" in (str(rec.msg) + str(rec.args)).lower()
        for rec in caplog.records
    ), [(r.levelname, r.getMessage()) for r in caplog.records]


def test_apply_migration_handles_multiple_users(tmp_path):
    """Issue #5: per-user loops were untested with 2+ users. Each
    user's latest_ts must land in THEIR own users.last_session_closed_at,
    not cross-contaminate."""
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        # Add a second user
        conn.execute(
            "INSERT INTO users (id, email, last_session_closed_at) VALUES (?, ?, ?)",
            (2, "second@palli.local", None),
        )
        # User 1: rows topping out at 2500
        _insert_session_close(conn, user_id=1, ts=1000.0)
        _insert_session_close(conn, user_id=1, ts=2500.0)
        # User 2: rows topping out at 7777
        _insert_session_close(conn, user_id=2, ts=5000.0)
        _insert_session_close(conn, user_id=2, ts=7777.0)
        _insert_session_close(conn, user_id=2, ts=6666.0)
        conn.commit()

        plan = module._plan_migration(conn)
        assert set(plan.keys()) == {1, 2}
        assert plan[1]["latest_ts"] == 2500.0
        assert plan[2]["latest_ts"] == 7777.0

        result = module._apply_migration(conn, plan)
        conn.commit()

        rows = dict(
            conn.execute(
                "SELECT id, last_session_closed_at FROM users ORDER BY id"
            ).fetchall()
        )
        assert rows[1] == 2500.0
        assert rows[2] == 7777.0

        # All session-close rows deleted across both users
        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM user_memory
            WHERE memory_type = 'user_fact' AND content LIKE 'last_session_closed_at:%'
            """
        ).fetchone()[0]
        assert remaining == 0

    assert result["users_updated"] == 2
    assert result["rows_deleted"] == 5


def test_show_status_flags_unparseable_only_rows(tmp_path, capsys):
    """Issue #2 (status branch): when ALL matching rows are
    unparseable, the plan is empty but rows DO exist. The status
    output must NOT claim "Nothing to migrate" without flagging the
    unparseable rows separately."""
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        # Insert two malformed rows: prefix matches but timestamp
        # cannot be extracted.
        conn.execute(
            """
            INSERT INTO user_memory
              (user_id, memory_type, content, importance_score, created_at, tags, source_detail)
            VALUES (?, 'user_fact', ?, 1.0, 0.0,
                    '["last_session_closed_at"]',
                    '{}')
            """,
            (1, "last_session_closed_at: not-a-number|x"),
        )
        conn.execute(
            """
            INSERT INTO user_memory
              (user_id, memory_type, content, importance_score, created_at, tags, source_detail)
            VALUES (?, 'user_fact', ?, 1.0, 0.0,
                    '["last_session_closed_at"]',
                    '{}')
            """,
            (1, "last_session_closed_at: also-busted"),
        )
        conn.commit()

        module._show_status(conn)

    captured = capsys.readouterr()
    out = captured.out
    # Must mention the unparseable count somewhere visibly.
    assert "unparseable" in out.lower(), out
    # Must include the actual count of unparseable rows.
    assert "2" in out, out


def test_dry_run_exits_nonzero_when_only_unparseable_rows_match(tmp_path, capsys):
    """Issue #2 (apply branch): if the LIKE filter matches rows but
    NONE are parseable, the dry-run must NOT print "Nothing to
    migrate — no rows found" (a lie) and must NOT exit 0 (because
    the operator should manually inspect the rows before --apply).

    We exercise the dry-run code path by calling main() with no args
    and asserting non-zero exit + a message that mentions
    'unparseable'.
    """
    module = _load_module()
    db_path = _make_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO user_memory
              (user_id, memory_type, content, importance_score, created_at, tags, source_detail)
            VALUES (?, 'user_fact', ?, 1.0, 0.0,
                    '["last_session_closed_at"]',
                    '{}')
            """,
            (1, "last_session_closed_at: cannot-parse"),
        )
        conn.commit()

    monkey_argv = ["migrate_last_session_closed_at.py"]
    real_argv = sys.argv
    real_db_path = module.DB_PATH
    sys.argv = monkey_argv
    module.DB_PATH = db_path
    try:
        rc = module.main()
    finally:
        sys.argv = real_argv
        module.DB_PATH = real_db_path

    captured = capsys.readouterr()
    assert rc != 0, (rc, captured.out, captured.err)
    out_lower = (captured.out + captured.err).lower()
    assert "unparseable" in out_lower, out_lower


def test_status_and_apply_are_mutually_exclusive(tmp_path):
    """Issue #6: passing both --status and --apply is a usage error.
    Currently --apply was silently dropped, which is confusing.
    """
    module = _load_module()
    db_path = _make_db(tmp_path)

    monkey_argv = ["migrate_last_session_closed_at.py", "--status", "--apply"]
    real_argv = sys.argv
    real_db_path = module.DB_PATH
    sys.argv = monkey_argv
    module.DB_PATH = db_path
    try:
        # argparse.parser.error raises SystemExit(2).
        import pytest as _pytest

        with _pytest.raises(SystemExit) as exc_info:
            module.main()
    finally:
        sys.argv = real_argv
        module.DB_PATH = real_db_path
    assert exc_info.value.code == 2


def test_resolve_local_owner_id_helper_was_removed():
    """Issue #3: the dead helper was removed entirely."""
    module = _load_module()
    assert not hasattr(module, "_resolve_local_owner_id"), (
        "Dead helper _resolve_local_owner_id should have been deleted "
        "(never called by anything in this script)."
    )


def test_apply_backup_captures_pre_schema_state(tmp_path, monkeypatch):
    """Issue #4: when running --apply against a DB that lacks the
    new column, the .pre-migration.bak must capture the truly
    pre-everything state — i.e. the .bak should NOT have the new
    column. If the operator wants a clean rollback via
    `cp .bak palli-local.sqlite3`, they should land back where they
    started."""
    module = _load_module()
    db_path = _make_db_without_new_column(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        _insert_session_close(conn, user_id=1, ts=2500.0)
        conn.commit()

    monkey_argv = ["migrate_last_session_closed_at.py", "--apply"]
    real_argv = sys.argv
    real_db_path = module.DB_PATH
    sys.argv = monkey_argv
    module.DB_PATH = db_path
    try:
        rc = module.main()
    finally:
        sys.argv = real_argv
        module.DB_PATH = real_db_path

    assert rc == 0, rc

    # The migrated DB has the column populated.
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT last_session_closed_at FROM users WHERE id = ?", (1,)
        ).fetchone()
        assert row[0] == 2500.0

    # The .bak should be the pre-everything snapshot: no new column,
    # original session-close rows still present.
    bak = db_path.with_suffix(db_path.suffix + ".pre-migration.bak")
    assert bak.exists(), "backup file was not created"
    with sqlite3.connect(str(bak)) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        assert "last_session_closed_at" not in cols, (
            "backup captured post-ALTER state; should be pre-everything"
        )
        n_rows = conn.execute(
            """
            SELECT COUNT(*) FROM user_memory
            WHERE memory_type = 'user_fact' AND content LIKE 'last_session_closed_at:%'
            """
        ).fetchone()[0]
        assert n_rows == 1
