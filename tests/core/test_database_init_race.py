"""The 2026-07-28 15:17 boot race: two components (backend bootstrap + the
Discord client) both saw _schema_initialized False and BOTH ran schema init
on separate connections — BEGIN IMMEDIATE vs BEGIN IMMEDIATE, 'database is
locked', component death, launcher shutdown. initialize() must serialize
concurrent callers behind a lock: schema init runs exactly once."""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_concurrent_initialize_runs_schema_init_exactly_once(monkeypatch, tmp_path):
    import core.database as db_mod

    brain = db_mod.PalliBrain(db_path=str(tmp_path / "race.sqlite3"))
    calls = []

    async def fake_schema_init(conn, *, program, logger):
        calls.append(1)
        await asyncio.sleep(0.05)   # hold the door open so the race is real

    monkeypatch.setattr(db_mod, "initialize_sqlite_schema", fake_schema_init)

    await asyncio.gather(brain.initialize(), brain.initialize(), brain.initialize())

    assert len(calls) == 1
    assert brain._schema_initialized is True


@pytest.mark.asyncio
async def test_two_brains_racing_one_file_end_at_the_same_version(monkeypatch, tmp_path):
    """Batch 8, task 4: _init_lock is per brain (per process), so two brains on
    one file are the cross-connection case. The schema version is read and
    written inside the init's BEGIN IMMEDIATE: the second waits, then finds
    every migration applied, and nothing runs twice."""
    import sqlite3

    import core.database as db_mod
    from core import schema_migrations as sm

    path = tmp_path / "race-two.sqlite3"
    ran = []
    monkeypatch.setattr(sm, "_on_apply", lambda v: ran.append(v))
    first = db_mod.PalliBrain(db_path=str(path))
    second = db_mod.PalliBrain(db_path=str(path))
    try:
        await asyncio.gather(first.initialize(), second.initialize())
    finally:
        await first.close()
        await second.close()

    assert first._schema_initialized and second._schema_initialized
    assert sorted(ran) == [number for number, _name, _statements in sm.MIGRATIONS]
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == sm.MIGRATIONS[-1][0]
    finally:
        conn.close()
