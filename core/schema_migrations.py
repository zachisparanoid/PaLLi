"""The database knows its version (Batch 8, task 4).

``PRAGMA user_version`` records which numbered migrations a database has had.
Until 2026-09-30 every boot re-ran the catalog's 54 best-effort ALTERs (plus
the reminders ``kind`` one) and its 9 backfill UPDATEs. Now each change runs
once.

A boot, on either path (``initialize_sqlite_schema`` in
``core/sqlite_database_bootstrap.py`` for PalliBrain, async; ``ensure_schema_sync``
below for tests and one-off scripts):

* **Version 0** means this code has never stamped the database: a fresh file,
  or one from before this change (hers is one). Today's whole program runs
  once, unchanged and in today's order: the CREATEs, the ALTERs, the backfills,
  the indexes, then the reminders ``kind`` column and its index. Nothing is
  skipped, because an old database may lack columns the indexes name (ruling
  T4-1). Then ``migrate`` stamps it 1, the baseline, and applies the numbered
  migrations after 1.
* **Version 1 or later** runs the CREATEs (``IF NOT EXISTS``, so a table the
  catalog has gained since is made), the migrations it has not had, then the
  indexes. No ALTER.

All of it happens inside the one ``BEGIN IMMEDIATE`` the boot already takes,
and the version is read and written inside it. ``PalliBrain._init_lock`` is
per process, so a second process (``palli_ctl``) waits on the write lock, then
finds the work done. Each numbered migration runs in its own SAVEPOINT together
with its stamp. One that fails is rolled back alone and logged, the ones after
it wait, and the next boot tries again.

Adding a migration: append ``(N, "what it does", [statements])``. It runs on
every database below N, fresh ones included, so a migration owns what it adds.
A column it adds is not also written into the catalog's CREATE (on a fresh
database the ALTER would then fail as a duplicate). An index on that column
goes in the migration, not in ``DEFAULT_SQLITE_INDEX_STATEMENTS``, because on a
version-0 database those run before the numbered migrations. A new table can
simply join the catalog's CREATEs and indexes instead, as ``stakes`` did here.
"""
from __future__ import annotations

import logging
from typing import Any

from core.sqlite_database_bootstrap import (
    SQLiteBootstrapProgram,
    adapt_sqlite_schema_statement,
)
from core.sqlite_schema_catalog import (
    DEFAULT_SQLITE_BOOTSTRAP_PROGRAM,
    DEFAULT_SQLITE_DATA_MIGRATION_STATEMENTS,
)

logger = logging.getLogger(__name__)

BASELINE_VERSION = 1

MIGRATIONS: list[tuple[int, str, list[str]]] = [
    # Today's schema: everything the catalog's CREATEs and ALTERs make. A
    # version-0 database reaches it by running today's whole program once.
    (1, "baseline 2026-09-30", []),
    # The backfills that re-ran on every boot until 2026-09-30, now run once
    # (ruling T4-3): surface_id from channel_id on six tables, and three old
    # runtime_jobs status names. Conversations rows written after this keep a
    # NULL surface_id; readers fall back to channel_id.
    (
        2,
        "one-time backfills: surface_id from channel_id, old runtime_jobs status names",
        list(DEFAULT_SQLITE_DATA_MIGRATION_STATEMENTS),
    ),
]


def _on_apply(version: int) -> None:
    """Called once for each version a database takes. A no-op; tests patch it."""


def current_version(conn: Any) -> int:
    """The database's ``PRAGMA user_version``; 0 means never stamped."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0] or 0) if row else 0


async def current_version_async(conn: Any) -> int:
    """``current_version`` on the boot path's connection."""
    return int(await conn.fetchval("PRAGMA user_version") or 0)


def newest_version() -> int:
    return max(number for number, _name, _statements in MIGRATIONS)


def _pending(version: int) -> list[tuple[int, str, list[str]]]:
    return sorted((m for m in MIGRATIONS if m[0] > version), key=lambda m: m[0])


def _stamp(version: int) -> str:
    return f"PRAGMA user_version = {int(version)}"


def _note_newer(version: int, log: logging.Logger) -> None:
    if version > newest_version():
        log.warning(
            "Database schema version %s is newer than this code knows (%s); "
            "booting without migrations.",
            version,
            newest_version(),
        )


def _note_failure(log: logging.Logger, number: int, name: str, version: int, exc: Exception) -> None:
    log.error(
        "Schema migration %s (%s) failed and was rolled back; the database stays "
        "at version %s and the next boot tries again: %s",
        number,
        name,
        version,
        exc,
    )


def migrate(conn: Any, *, log: logging.Logger | None = None) -> int:
    """Apply the numbered migrations this database has not had; return its version.

    Runs inside the caller's transaction, after the catalog's program has run on
    this connection (``ensure_schema_sync`` does both). Version 0 is stamped the
    baseline, since today's CREATEs and ALTERs have just run. Then each pending
    migration runs in its own SAVEPOINT with its stamp.
    """
    log = log or logger
    version = current_version(conn)
    if version == 0:
        conn.execute(_stamp(BASELINE_VERSION))
        _on_apply(BASELINE_VERSION)
        version = BASELINE_VERSION
    else:
        _note_newer(version, log)
    for number, name, statements in _pending(version):
        savepoint = f"schema_migration_{int(number)}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            for statement in statements:
                conn.execute(adapt_sqlite_schema_statement(statement))
            conn.execute(_stamp(number))
        except Exception as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            _note_failure(log, number, name, version, exc)
            break
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        _on_apply(number)
        version = number
    return version


async def migrate_async(conn: Any, *, log: logging.Logger | None = None) -> int:
    """``migrate`` on the boot path's connection (``initialize_sqlite_schema``).

    ``conn.transaction()`` inside the boot's ``BEGIN IMMEDIATE`` is a SAVEPOINT,
    rolled back on its own when a statement fails.
    """
    log = log or logger
    version = await current_version_async(conn)
    if version == 0:
        await conn.execute(_stamp(BASELINE_VERSION))
        _on_apply(BASELINE_VERSION)
        version = BASELINE_VERSION
    else:
        _note_newer(version, log)
    for number, name, statements in _pending(version):
        try:
            async with conn.transaction():
                for statement in statements:
                    await conn.execute(adapt_sqlite_schema_statement(statement))
                await conn.execute(_stamp(number))
        except Exception as exc:
            _note_failure(log, number, name, version, exc)
            break
        _on_apply(number)
        version = number
    return version


def _run_unversioned_program_sync(conn: Any, program: SQLiteBootstrapProgram) -> None:
    """Today's whole program, unchanged and in today's order (version 0 only)."""
    for statement in program.ddl_statements:
        conn.execute(adapt_sqlite_schema_statement(statement))
    for statement in program.migration_statements:
        try:
            conn.execute(adapt_sqlite_schema_statement(statement))
        except Exception:
            pass
    for statement in program.data_migration_statements:
        try:
            conn.execute(adapt_sqlite_schema_statement(statement))
        except Exception:
            pass
    for statement in program.index_statements:
        conn.execute(adapt_sqlite_schema_statement(statement))
    try:
        conn.execute(adapt_sqlite_schema_statement(program.scheduled_reminder_kind_statement))
    except Exception:
        pass
    conn.execute(adapt_sqlite_schema_statement(program.scheduled_reminder_kind_index_statement))


def ensure_schema_sync(conn: Any, *, program: SQLiteBootstrapProgram | None = None) -> int:
    """The catalog's CREATEs, then ``migrate``, then the indexes, all in one
    ``BEGIN IMMEDIATE``; returns the version. Synchronous, for tests and one-off
    scripts on raw ``sqlite3`` connections (``ensure_catalog_schema_sync`` calls
    it). It mirrors ``initialize_sqlite_schema``; keep the two in step.
    """
    program = program or DEFAULT_SQLITE_BOOTSTRAP_PROGRAM
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN IMMEDIATE")
    try:
        start = current_version(conn)
        if start == 0:
            _run_unversioned_program_sync(conn, program)
        else:
            for statement in program.ddl_statements:
                conn.execute(adapt_sqlite_schema_statement(statement))
        version = migrate(conn)
        if start != 0:
            for statement in (*program.index_statements, program.scheduled_reminder_kind_index_statement):
                conn.execute(adapt_sqlite_schema_statement(statement))
    except BaseException:
        if began and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    if began:
        conn.execute("COMMIT")
    else:
        conn.commit()
    return version


__all__ = [
    "BASELINE_VERSION",
    "MIGRATIONS",
    "current_version",
    "current_version_async",
    "ensure_schema_sync",
    "migrate",
    "migrate_async",
    "newest_version",
]
