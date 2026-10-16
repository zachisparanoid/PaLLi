"""Schema-level test: user_sentiment_state DDL is present in the catalog.

Phase 3 Task 0.2 adds the table. This test is the lightweight guard that
someone doesn't accidentally delete the DDL statement. Full end-to-end
table-creation tests live in the bootstrap suite.
"""
from __future__ import annotations

from core.sqlite_schema_catalog import DEFAULT_SQLITE_BOOTSTRAP_PROGRAM


def _all_ddl_joined() -> str:
    return "\n".join(DEFAULT_SQLITE_BOOTSTRAP_PROGRAM.ddl_statements)


def test_user_sentiment_state_ddl_is_in_catalog():
    joined = _all_ddl_joined()
    assert "CREATE TABLE IF NOT EXISTS user_sentiment_state" in joined


def test_user_sentiment_state_has_expected_columns():
    joined = _all_ddl_joined()
    # Locate the user_sentiment_state block and check each required column
    # appears in it. Not a strict order check — catalog edits shouldn't
    # break this for cosmetic reordering.
    assert "user_sentiment_state" in joined
    start = joined.find("CREATE TABLE IF NOT EXISTS user_sentiment_state")
    end = joined.find(")", start)
    ddl = joined[start:end]
    for col in ("user_id", "tag", "read", "confidence", "source", "updated_at"):
        assert col in ddl, f"column {col!r} missing from user_sentiment_state DDL"


def test_user_sentiment_state_has_primary_key():
    joined = _all_ddl_joined()
    start = joined.find("CREATE TABLE IF NOT EXISTS user_sentiment_state")
    end = joined.find(")", start)
    ddl = joined[start:end]
    # user_id should be the PRIMARY KEY (single row per user)
    assert "PRIMARY KEY" in ddl
    assert "user_id" in ddl.split("PRIMARY KEY")[0]
