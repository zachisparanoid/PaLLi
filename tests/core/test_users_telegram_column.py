"""``users.telegram_user_id`` — the column the Telegram door resolves him by
(telegram door, task 1).

``users`` has carried a legacy ``telegram_username`` since the hosted era;
a username is not an identity (it can be changed by its owner in seconds).
The numeric ``telegram_user_id`` is, so it gets its own column, its own
unique index (the same partial shape ``discord_user_id`` has, so blanks and
NULLs never collide), a migration for databases that already exist, and a
lookup beside ``get_app_user_by_discord_id``.

Real sqlite throughout — the DDL is the thing under test, not a mock of it.
"""
from __future__ import annotations

import sqlite3

import pytest

from core.sqlite_schema_catalog import (
    DEFAULT_SQLITE_MIGRATION_STATEMENTS,
    ensure_catalog_schema_sync,
)


def _fresh_db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "catalog.sqlite3"))
    ensure_catalog_schema_sync(conn)
    return conn


# ── the catalog ─────────────────────────────────────────────────────────

def test_the_users_ddl_carries_both_telegram_columns(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        cols = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(users)")}
    finally:
        conn.close()

    assert "discord_user_id" in cols                      # the pin
    assert "telegram_username" in cols                    # the legacy column stays
    assert "telegram_user_id" in cols
    assert cols["telegram_user_id"].upper().startswith("TEXT")


def test_the_unique_index_exists_beside_the_discord_one(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='users'"
            )
        }
    finally:
        conn.close()

    assert "idx_users_discord_user_id_unique" in names    # the pin
    assert "idx_users_telegram_user_id_unique" in names


def test_two_owners_cannot_claim_one_telegram_id(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        conn.execute(
            "INSERT INTO users (email, telegram_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("a@palli.local", "777", 0.0, 0.0),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (email, telegram_user_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("b@palli.local", "777", 0.0, 0.0),
            )
    finally:
        conn.close()


def test_the_index_is_partial_so_blanks_and_nulls_never_collide(tmp_path):
    """Every user without a Telegram id would otherwise collide on NULL/''
    — the same partial shape the Discord index has."""
    conn = _fresh_db(tmp_path)
    try:
        for i, value in enumerate([None, None, "", ""]):
            conn.execute(
                "INSERT INTO users (email, telegram_user_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (f"u{i}@palli.local", value, 0.0, 0.0),
            )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()

    assert count == 4


def test_an_existing_database_gets_the_column_by_migration():
    assert (
        "ALTER TABLE users ADD COLUMN telegram_user_id TEXT"
        in DEFAULT_SQLITE_MIGRATION_STATEMENTS
    )


# ── the store ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_store_writes_and_finds_a_telegram_id(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "store.sqlite3"))
    await brain.initialize()

    user_id = await brain.create_app_user(
        email="owner@palli.local", password_hash=None, google_sub=None, role="admin",
    )
    assert await brain.update_app_user(user_id, {"telegram_user_id": "777"}) is True

    found = await brain.get_app_user_by_telegram_id("777")
    assert found is not None and int(found["id"]) == int(user_id)
    # The account record carries it beside discord_user_id.
    assert str(found["telegram_user_id"]) == "777"
    assert found["discord_user_id"] is None


@pytest.mark.asyncio
async def test_an_unknown_telegram_id_finds_nobody(tmp_path):
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "store.sqlite3"))
    await brain.initialize()
    await brain.create_app_user(
        email="owner@palli.local", password_hash=None, google_sub=None, role="admin",
    )

    assert await brain.get_app_user_by_telegram_id("888") is None


@pytest.mark.asyncio
async def test_the_discord_lookup_is_unchanged(tmp_path):
    """The pin: the door this task must not move."""
    from core.database import PalliBrain

    brain = PalliBrain(db_path=str(tmp_path / "store.sqlite3"))
    await brain.initialize()
    user_id = await brain.create_app_user(
        email="owner@palli.local", password_hash=None, google_sub=None, role="admin",
    )
    await brain.update_app_user(user_id, {"discord_user_id": "424242"})

    found = await brain.get_app_user_by_discord_id("424242")
    assert found is not None and int(found["id"]) == int(user_id)
