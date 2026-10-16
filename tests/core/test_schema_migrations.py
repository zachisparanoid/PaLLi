"""The database knows its version (Batch 8, task 4).

``PRAGMA user_version`` plus a numbered, one-time migration list, in place of
the 54 best-effort ALTERs (and the reminders ``kind`` one) and the 9 backfill
UPDATEs that used to re-run on every boot.

Ruling T4-1: a version-0 database (fresh, or one from before this change, as
hers is) runs today's whole program once, in today's order, because an old
database may lack columns the indexes need. Then it is stamped 1 and takes the
numbered migrations after it. A stamped database runs the CREATEs, the
migrations it has not had yet, and the indexes, and never an ALTER. Ruling T4-2:
"never an ALTER" is proved by counting the ALTER statements actually executed;
a log line proves nothing, because a failing ALTER was always swallowed
silently.

Real SQLite under tmp_path throughout.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core import schema_migrations as sm
from core.sqlite_database_bootstrap import adapt_sqlite_schema_statement
from core.sqlite_schema_catalog import (
    DEFAULT_SQLITE_BOOTSTRAP_PROGRAM,
    DEFAULT_SQLITE_DATA_MIGRATION_STATEMENTS,
    DEFAULT_SQLITE_MIGRATION_STATEMENTS,
    ensure_catalog_schema_sync,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NEWEST = sm.MIGRATIONS[-1][0]
_INDEX_NAME = re.compile(r"INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE)
# The 54 catalog ALTERs plus the reminders ``kind`` one.
LEGACY_ALTER_COUNT = len(DEFAULT_SQLITE_MIGRATION_STATEMENTS) + 1


def _is_alter(statement: str) -> bool:
    return str(statement).lstrip().upper().startswith("ALTER")


class _CountingConnection:
    """A sqlite3 connection that writes down every statement it is given."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.statements: list[str] = []

    def execute(self, sql, *args):
        self.statements.append(str(sql))
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    @property
    def alters(self) -> list[str]:
        return [s for s in self.statements if _is_alter(s)]


def _catalog_index_names() -> set[str]:
    program = DEFAULT_SQLITE_BOOTSTRAP_PROGRAM
    statements = (*program.index_statements, program.scheduled_reminder_kind_index_statement)
    return {m.group(1) for s in statements for m in [_INDEX_NAME.search(s)] if m}


def _names(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _version_of(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _build_todays_database(path: Path) -> None:
    """Her database as it is before this change: every catalog table and
    column, built the way every boot built it until now, never stamped."""
    program = DEFAULT_SQLITE_BOOTSTRAP_PROGRAM
    conn = sqlite3.connect(path)
    try:
        for statement in program.ddl_statements:
            conn.execute(adapt_sqlite_schema_statement(statement))
        for statement in (*program.migration_statements, *program.data_migration_statements):
            try:
                conn.execute(adapt_sqlite_schema_statement(statement))
            except sqlite3.OperationalError:
                pass
        for statement in program.index_statements:
            conn.execute(adapt_sqlite_schema_statement(statement))
        try:
            conn.execute(adapt_sqlite_schema_statement(program.scheduled_reminder_kind_statement))
        except sqlite3.OperationalError:
            pass
        conn.execute(adapt_sqlite_schema_statement(program.scheduled_reminder_kind_index_statement))
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        conn.close()


def _spy_on_boot_statements(monkeypatch) -> list[str]:
    """Every statement the boot path's connection executes, in order."""
    import core.database as db_mod

    seen: list[str] = []
    real_execute = db_mod._SQLiteConnection.execute

    async def counting_execute(self, query, *args):
        seen.append(str(query))
        return await real_execute(self, query, *args)

    monkeypatch.setattr(db_mod._SQLiteConnection, "execute", counting_execute)
    return seen


async def _boot(path: Path) -> None:
    from core.database import PalliBrain

    brain = PalliBrain(str(path))
    try:
        await brain.initialize()
    finally:
        await brain.close()


# ── the list itself ─────────────────────────────────────────────────────


def test_the_list_starts_at_the_baseline_and_counts_up_by_one():
    assert sm.MIGRATIONS[0] == (1, "baseline 2026-09-30", [])
    numbers = [number for number, _name, _statements in sm.MIGRATIONS]
    assert numbers == list(range(1, len(numbers) + 1))
    assert all(name.strip() for _number, name, _statements in sm.MIGRATIONS)


def test_migration_2_is_the_nine_backfills():
    number, _name, statements = sm.MIGRATIONS[1]
    assert number == 2
    assert statements == list(DEFAULT_SQLITE_DATA_MIGRATION_STATEMENTS)
    assert len(statements) == 9


# ── the synchronous path (tests and one-off scripts) ────────────────────


def test_a_fresh_database_is_stamped_at_the_newest_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "fresh.db")
    try:
        sm.ensure_schema_sync(conn)  # catalog CREATEs + migrate
        assert sm.current_version(conn) == sm.MIGRATIONS[-1][0]
        assert {"users", "conversations", "stakes"} <= _names(conn, "table")
        assert _catalog_index_names() <= _names(conn, "index")
    finally:
        conn.close()


def test_an_unversioned_database_runs_todays_program_once_then_never_an_alter(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    try:
        sm.ensure_schema_sync(conn)
        conn.execute("PRAGMA user_version = 0")  # today's live state

        first = _CountingConnection(conn)
        sm.ensure_schema_sync(first)
        assert sm.current_version(conn) == NEWEST
        # The version-0 pass is today's program, unchanged: every ALTER is tried.
        assert len(first.alters) == LEGACY_ALTER_COUNT

        second = _CountingConnection(conn)
        sm.ensure_schema_sync(second)
        assert second.alters == []
        assert sm.current_version(conn) == NEWEST
    finally:
        conn.close()


def test_a_numbered_migration_runs_once(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "m.db")
    try:
        sm.ensure_schema_sync(conn)
        ran = []
        monkeypatch.setattr(sm, "MIGRATIONS", sm.MIGRATIONS + [(99, "test", ["CREATE TABLE t99 (x)"])])
        monkeypatch.setattr(sm, "_on_apply", lambda v: ran.append(v))
        sm.ensure_schema_sync(conn)
        sm.ensure_schema_sync(conn)
        assert ran == [99] and sm.current_version(conn) == 99
        assert "t99" in _names(conn, "table")
    finally:
        conn.close()


def test_the_catalog_entry_point_is_the_same_boot(tmp_path):
    conn = sqlite3.connect(tmp_path / "catalog.db")
    try:
        ensure_catalog_schema_sync(conn)
        assert sm.current_version(conn) == NEWEST
        counting = _CountingConnection(conn)
        ensure_catalog_schema_sync(counting)
        assert counting.alters == []
        assert not conn.in_transaction  # committed, as before
    finally:
        conn.close()


def test_a_stamped_database_still_gains_a_table_or_index_the_catalog_has(tmp_path):
    """The CREATEs (IF NOT EXISTS) and the indexes run on every boot."""
    conn = sqlite3.connect(tmp_path / "stamped.db")
    try:
        sm.ensure_schema_sync(conn)
        conn.execute("DROP TABLE stakes")
        conn.execute("DROP INDEX idx_conversations_platform_surface_time")
        conn.commit()

        counting = _CountingConnection(conn)
        sm.ensure_schema_sync(counting)
        assert "stakes" in _names(conn, "table")
        assert "idx_conversations_platform_surface_time" in _names(conn, "index")
        assert counting.alters == []
    finally:
        conn.close()


def test_a_failing_migration_is_rolled_back_alone_and_tried_again(tmp_path, monkeypatch, caplog):
    conn = sqlite3.connect(tmp_path / "fail.db")
    try:
        sm.ensure_schema_sync(conn)
        base = NEWEST
        broken = [
            (base + 1, "fine", ["CREATE TABLE t_fine (x)"]),
            (base + 2, "broken", ["CREATE TABLE t_half (x)", "INSERT INTO no_such_table VALUES (1)"]),
            (base + 3, "after", ["CREATE TABLE t_after (x)"]),
        ]
        monkeypatch.setattr(sm, "MIGRATIONS", sm.MIGRATIONS + broken)
        with caplog.at_level(logging.ERROR):
            sm.ensure_schema_sync(conn)  # the boot goes on
        assert sm.current_version(conn) == base + 1
        tables = _names(conn, "table")
        assert "t_fine" in tables
        assert "t_half" not in tables  # its SAVEPOINT rolled back
        assert "t_after" not in tables  # later migrations wait
        assert any(str(base + 2) in r.getMessage() and "broken" in r.getMessage() for r in caplog.records)

        fixed = broken[:1] + [(base + 2, "broken", ["CREATE TABLE t_half (x)"])] + broken[2:]
        monkeypatch.setattr(sm, "MIGRATIONS", sm.MIGRATIONS[: len(sm.MIGRATIONS) - 3] + fixed)
        sm.ensure_schema_sync(conn)
        assert sm.current_version(conn) == base + 3
        assert {"t_half", "t_after"} <= _names(conn, "table")
    finally:
        conn.close()


def test_a_database_newer_than_this_code_boots_and_says_so(tmp_path, caplog):
    conn = sqlite3.connect(tmp_path / "newer.db")
    try:
        sm.ensure_schema_sync(conn)
        conn.execute(f"PRAGMA user_version = {NEWEST + 40}")
        counting = _CountingConnection(conn)
        with caplog.at_level(logging.WARNING):
            sm.ensure_schema_sync(counting)
        assert sm.current_version(conn) == NEWEST + 40  # never moved backwards
        assert counting.alters == []
        assert any("newer" in r.getMessage() for r in caplog.records)
    finally:
        conn.close()


# ── the boot path (PalliBrain.initialize, async) ────────────────────────


@pytest.mark.asyncio
async def test_her_database_as_it_is_today_boots_once_into_the_newest_version(tmp_path, monkeypatch):
    """The controller's old live-shape check: today's catalog at version 0."""
    path = tmp_path / "today.sqlite3"
    _build_todays_database(path)
    seen = _spy_on_boot_statements(monkeypatch)

    await _boot(path)
    first_boot_alters = [s for s in seen if _is_alter(s)]
    assert len(first_boot_alters) == LEGACY_ALTER_COUNT
    assert _version_of(path) == NEWEST

    conn = sqlite3.connect(path)
    try:
        assert _catalog_index_names() <= _names(conn, "index")
        assert "stakes" in _names(conn, "table")
    finally:
        conn.close()

    seen.clear()
    await _boot(path)
    assert [s for s in seen if _is_alter(s)] == []
    assert _version_of(path) == NEWEST


@pytest.mark.asyncio
async def test_an_old_shape_database_gains_its_columns_indexes_and_backfill(tmp_path):
    """Grounding C#9: an index the boot requires names a column only an ALTER
    adds, so a version-0 database must run the ALTERs before the indexes."""
    path = tmp_path / "old-shape.sqlite3"
    raw = sqlite3.connect(path)
    try:
        raw.execute(
            "CREATE TABLE conversations ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,"
            " username TEXT, channel_id INTEGER NOT NULL, platform TEXT,"
            " message TEXT, response TEXT, timestamp REAL)"
        )
        raw.execute(
            "INSERT INTO conversations (user_id, username, channel_id, platform, message, response, timestamp)"
            " VALUES (1, 'Robin', 4242, 'discord', 'hi', 'hello', 1.0)"
        )
        raw.commit()
        assert "surface_id" not in _columns(raw, "conversations")
    finally:
        raw.close()

    await _boot(path)

    conn = sqlite3.connect(path)
    try:
        assert {"thread_id", "surface_id"} <= _columns(conn, "conversations")
        assert "idx_conversations_platform_surface_time" in _names(conn, "index")
        assert conn.execute("SELECT surface_id FROM conversations").fetchone()[0] == "4242"
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == NEWEST
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_a_numbered_migration_runs_once_on_the_boot_path(tmp_path, monkeypatch):
    path = tmp_path / "boot.sqlite3"
    ran: list[int] = []
    monkeypatch.setattr(sm, "_on_apply", lambda v: ran.append(v))

    await _boot(path)
    assert ran == [number for number, _n, _s in sm.MIGRATIONS]  # baseline, then each

    ran.clear()
    monkeypatch.setattr(sm, "MIGRATIONS", sm.MIGRATIONS + [(99, "test", ["CREATE TABLE t99 (x)"])])
    await _boot(path)
    await _boot(path)
    assert ran == [99]
    assert _version_of(path) == 99


@pytest.mark.asyncio
async def test_a_failing_migration_on_the_boot_path_is_rolled_back_alone(tmp_path, monkeypatch, caplog):
    path = tmp_path / "boot-fail.sqlite3"
    await _boot(path)
    broken = [
        (NEWEST + 1, "fine", ["CREATE TABLE t_fine (x)"]),
        (NEWEST + 2, "broken", ["CREATE TABLE t_half (x)", "INSERT INTO no_such_table VALUES (1)"]),
        (NEWEST + 3, "after", ["CREATE TABLE t_after (x)"]),
    ]
    monkeypatch.setattr(sm, "MIGRATIONS", sm.MIGRATIONS + broken)
    with caplog.at_level(logging.ERROR):
        await _boot(path)  # she still boots

    assert _version_of(path) == NEWEST + 1
    conn = sqlite3.connect(path)
    try:
        tables = _names(conn, "table")
    finally:
        conn.close()
    assert "t_fine" in tables and "t_half" not in tables and "t_after" not in tables
    assert any(str(NEWEST + 2) in r.getMessage() and "broken" in r.getMessage() for r in caplog.records)


# ── stakes joins the catalog ────────────────────────────────────────────


def test_the_catalog_creates_stakes_and_its_index():
    program = DEFAULT_SQLITE_BOOTSTRAP_PROGRAM
    ddl = next(s for s in program.ddl_statements if "CREATE TABLE IF NOT EXISTS stakes" in s)
    for column in ("voiced_at", "evidence", "muted_at"):
        assert column in ddl
    assert "idx_stakes_status" in _catalog_index_names()


@pytest.mark.asyncio
async def test_a_fresh_boot_has_the_whole_ledger_before_the_ledger_is_touched(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    await _boot(path)
    conn = sqlite3.connect(path)
    try:
        assert {"voiced_at", "evidence", "muted_at"} <= _columns(conn, "stakes")
        assert "idx_stakes_status" in _names(conn, "index")
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_the_ledgers_own_schema_step_runs_no_alter(tmp_path, monkeypatch):
    from ai import stake_ledger
    from core.database import PalliBrain

    brain = PalliBrain(str(tmp_path / "ledger-own.sqlite3"))
    await brain.initialize()
    seen = _spy_on_boot_statements(monkeypatch)
    try:
        await stake_ledger.ensure_schema(brain_client=brain)
    finally:
        await brain.close()
    assert any("CREATE TABLE IF NOT EXISTS stakes" in s for s in seen)  # it still covers fakes
    assert [s for s in seen if _is_alter(s)] == []


# ── no import cycle, and two processes racing one file ──────────────────


@pytest.mark.parametrize(
    "module",
    ["core.schema_migrations", "core.sqlite_schema_catalog", "core.sqlite_database_bootstrap"],
)
def test_each_module_imports_first_in_a_fresh_interpreter(module):
    """catalog -> schema_migrations is a lazy import: whichever comes first loads."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


_RACER = r"""
import json, sqlite3, sys, time
import core.schema_migrations as sm
from core.sqlite_schema_catalog import ensure_catalog_schema_sync
ran = []
sm._on_apply = ran.append
start_at = float(sys.argv[2])
while time.time() < start_at:
    time.sleep(0.001)
conn = sqlite3.connect(sys.argv[1], timeout=30)
ensure_catalog_schema_sync(conn)
print(json.dumps({"ran": ran, "version": sm.current_version(conn)}))
conn.close()
"""


def test_two_processes_racing_a_fresh_file_apply_each_version_once(tmp_path):
    """The version is read inside BEGIN IMMEDIATE: the second process waits,
    then finds the work done (``_init_lock`` is per-process; this is not)."""
    path = tmp_path / "race.sqlite3"
    start_at = f"{time.time() + 1.5:.3f}"
    racers = [
        subprocess.Popen(
            [sys.executable, "-c", _RACER, str(path), start_at],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(2)
    ]
    outputs = []
    for racer in racers:
        out, err = racer.communicate(timeout=90)
        assert racer.returncode == 0, err
        outputs.append(json.loads(out.strip().splitlines()[-1]))

    assert [o["version"] for o in outputs] == [NEWEST, NEWEST]
    applied = sorted(v for o in outputs for v in o["ran"])
    assert applied == [number for number, _n, _s in sm.MIGRATIONS]
