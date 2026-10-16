"""Batch 5, task 3 (memory that picks, 2026-09-28): reference stamps for
memory bullets — the table, the brain methods and the in-process snapshot.

``core.memory_refs`` keeps ONE snapshot per owner in the process (R7): recall
loads it once (``ensure_loaded``), the nightly sweep reloads it
(``refresh``), and ``bump`` writes the table without touching it — so the
core's ranking inputs change once a day and the reply prompt's stable
prefix stays cacheable. The sync render reads ``snapshot(owner_id)``, ``{}``
until loaded. The table ``memory_bullet_refs`` (R10) lives in the brain's
canonical DDL and is exercised here against the suite's tmp database.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from core import memory_refs
from core.database import brain
from core.sqlite_schema_catalog import ensure_catalog_schema_sync

# Not the fixtures' owner 1: the context-adapter tests expect owner 1's
# snapshot to be empty, and this module loads snapshots.
OWNER = 4242
A, B, C = "mcb_aaaaaaaaaaaa", "mcb_bbbbbbbbbbbb", "mcb_cccccccccccc"


@pytest.fixture(autouse=True)
def _forget_between_tests():
    memory_refs.forget()
    yield
    memory_refs.forget()


async def _wipe(*owners: int) -> None:
    conn = await brain.get_connection()
    try:
        for owner in owners:
            await conn.execute("DELETE FROM memory_bullet_refs WHERE owner_user_id = $1", int(owner))
    finally:
        await conn.close()


def _by_id(rows):
    return {row["item_id"]: (row["last_referenced_at"], row["reference_count"]) for row in rows}


# ── the table (R10) ─────────────────────────────────────────────────────────


def test_the_table_and_its_index_are_in_the_catalog():
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(str(Path(tmp) / "test.sqlite3"))
        try:
            ensure_catalog_schema_sync(conn)
            cols = {row[1]: (row[2].upper(), row[3], row[5]) for row in conn.execute("PRAGMA table_info(memory_bullet_refs)")}
            indexes = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='memory_bullet_refs'"
            )}
        finally:
            conn.close()
    # (declared type, NOT NULL, position in the primary key)
    assert cols["item_id"] == ("TEXT", 1, 1)
    assert cols["owner_user_id"] == ("BIGINT", 1, 2)
    assert cols["last_referenced_at"] == ("REAL", 1, 0)
    assert cols["reference_count"][0] == "INTEGER" and cols["reference_count"][1] == 1
    assert "idx_memory_bullet_refs_owner_time" in indexes


# ── the brain methods ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bump_inserts_at_one_then_counts_up_and_restamps():
    await _wipe(OWNER)
    assert await brain.load_memory_bullet_refs(owner_user_id=OWNER) == []
    await brain.bump_memory_bullet_refs(owner_user_id=OWNER, item_ids=[A, B], now=100.0)
    assert _by_id(await brain.load_memory_bullet_refs(owner_user_id=OWNER)) == {A: (100.0, 1), B: (100.0, 1)}
    await brain.bump_memory_bullet_refs(owner_user_id=OWNER, item_ids=[A], now=200.0)
    assert _by_id(await brain.load_memory_bullet_refs(owner_user_id=OWNER)) == {A: (200.0, 2), B: (100.0, 1)}
    await _wipe(OWNER)


@pytest.mark.asyncio
async def test_bump_dedups_within_a_call_skips_blanks_and_defaults_now_to_the_clock():
    await _wipe(OWNER)
    await brain.bump_memory_bullet_refs(owner_user_id=OWNER, item_ids=[C, C, "", "  ", None])  # type: ignore[list-item]
    rows = _by_id(await brain.load_memory_bullet_refs(owner_user_id=OWNER))
    assert set(rows) == {C} and rows[C][1] == 1
    assert rows[C][0] > 1_700_000_000.0  # a real clock reading, not zero
    await brain.bump_memory_bullet_refs(owner_user_id=OWNER, item_ids=[])  # a no-op, no error
    assert set(_by_id(await brain.load_memory_bullet_refs(owner_user_id=OWNER))) == {C}
    await _wipe(OWNER)


@pytest.mark.asyncio
async def test_refs_are_scoped_to_their_owner():
    await _wipe(OWNER, OWNER + 1)
    await brain.bump_memory_bullet_refs(owner_user_id=OWNER, item_ids=[A], now=100.0)
    await brain.bump_memory_bullet_refs(owner_user_id=OWNER + 1, item_ids=[A], now=300.0)
    assert _by_id(await brain.load_memory_bullet_refs(owner_user_id=OWNER)) == {A: (100.0, 1)}
    assert _by_id(await brain.load_memory_bullet_refs(owner_user_id=OWNER + 1)) == {A: (300.0, 1)}
    assert await brain.load_memory_bullet_refs(owner_user_id=OWNER + 2) == []
    await _wipe(OWNER, OWNER + 1)


# ── the snapshot (R7) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_is_empty_until_loaded_loads_once_and_bump_leaves_it_alone():
    await _wipe(OWNER)
    assert memory_refs.snapshot(OWNER) == {}
    await memory_refs.bump(OWNER, [A], now=100.0)
    assert memory_refs.snapshot(OWNER) == {}  # written to the table, not to the snapshot
    await memory_refs.ensure_loaded(OWNER)
    loaded = {A: {"last_referenced_at": 100.0, "count": 1}}
    assert memory_refs.snapshot(OWNER) == loaded
    await memory_refs.bump(OWNER, [A, B], now=200.0)
    assert memory_refs.snapshot(OWNER) == loaded  # still yesterday's picture
    await memory_refs.ensure_loaded(OWNER)
    assert memory_refs.snapshot(OWNER) == loaded  # once per process: not reloaded
    # the table has moved on; refresh (the nightly sweep's call) sees it
    assert await memory_refs.load_refs(OWNER) == {
        A: {"last_referenced_at": 200.0, "count": 2},
        B: {"last_referenced_at": 200.0, "count": 1},
    }
    await memory_refs.refresh(OWNER)
    assert memory_refs.snapshot(OWNER) == await memory_refs.load_refs(OWNER)
    await _wipe(OWNER)


@pytest.mark.asyncio
async def test_snapshots_are_per_owner_and_forget_drops_them():
    await _wipe(OWNER, OWNER + 1)
    await memory_refs.bump(OWNER, [A], now=100.0)
    await memory_refs.ensure_loaded(OWNER)
    await memory_refs.ensure_loaded(OWNER + 1)
    assert set(memory_refs.snapshot(OWNER)) == {A}
    assert memory_refs.snapshot(OWNER + 1) == {}
    assert memory_refs.snapshot("4242") == memory_refs.snapshot(OWNER)  # an id as text is the same owner
    memory_refs.forget(OWNER)
    assert memory_refs.snapshot(OWNER) == {}
    await memory_refs.ensure_loaded(OWNER)  # forgotten, so it loads again
    assert set(memory_refs.snapshot(OWNER)) == {A}
    await _wipe(OWNER, OWNER + 1)


@pytest.mark.asyncio
async def test_bump_with_nothing_to_bump_is_a_no_op():
    await _wipe(OWNER)
    await memory_refs.bump(OWNER, [])
    await memory_refs.bump(OWNER, ["", None])  # type: ignore[list-item]
    assert await memory_refs.load_refs(OWNER) == {}


@pytest.mark.asyncio
async def test_a_brain_that_fails_never_breaks_the_reply_path(monkeypatch, caplog):
    """ensure_loaded and refresh log and keep what they had; the render's
    ``snapshot`` read stays a plain dict read."""
    import logging

    await _wipe(OWNER)
    await memory_refs.bump(OWNER, [A], now=100.0)
    await memory_refs.ensure_loaded(OWNER)
    before = memory_refs.snapshot(OWNER)

    async def boom(**_kwargs):
        raise RuntimeError("brain down")

    monkeypatch.setattr(brain, "load_memory_bullet_refs", boom)
    with caplog.at_level(logging.INFO, logger=memory_refs.logger.name):
        await memory_refs.refresh(OWNER)
        memory_refs.forget(OWNER + 1)
        await memory_refs.ensure_loaded(OWNER + 1)
    assert memory_refs.snapshot(OWNER) == before  # the old picture stays
    assert memory_refs.snapshot(OWNER + 1) == {}
    assert any("brain down" in record.getMessage() for record in caplog.records)
    monkeypatch.undo()
    await _wipe(OWNER)


# ── the core's budget (R38) ─────────────────────────────────────────────────


def test_memory_core_budget_reads_the_setting_and_falls_back_to_the_schema_default(monkeypatch):
    """Batch 5, task 4: ONE budget helper for recall's dedup and the
    adapter's render, so both plan the same core. It reads
    ``memory.core_budget_chars`` from the settings snapshot; when settings
    cannot be read, or the value is missing or zero, it is the schema's
    12000 — the reading the adapter's helper had before it moved here."""
    from types import SimpleNamespace

    import core.settings_boot as settings_boot

    def snapshot_with(**memory):
        return lambda: SimpleNamespace(memory=SimpleNamespace(**memory))

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", snapshot_with(core_budget_chars=4321))
    assert memory_refs.memory_core_budget() == 4321

    def boom():
        raise RuntimeError("settings not booted")

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", boom)
    assert memory_refs.memory_core_budget() == 12000
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", snapshot_with(core_budget_chars=0))
    assert memory_refs.memory_core_budget() == 12000
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", lambda: SimpleNamespace())
    assert memory_refs.memory_core_budget() == 12000


def test_corrections_budget_reads_the_setting_falls_back_to_1500_and_keeps_zero(monkeypatch):
    """Batch 5, task 7 fix round 1 (ruling R56): the reply's corrections
    band budget is a setting, ``memory.corrections_budget_chars`` — the
    sibling of memory_core_budget. Unbootable settings, a missing block or
    a missing value read as the schema's 1500; unlike the core budget, 0
    is KEPT: it turns the band off."""
    from types import SimpleNamespace

    import core.settings_boot as settings_boot

    def snapshot_with(**memory):
        return lambda: SimpleNamespace(memory=SimpleNamespace(**memory))

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", snapshot_with(corrections_budget_chars=640))
    assert memory_refs.corrections_budget() == 640
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", snapshot_with(corrections_budget_chars=0))
    assert memory_refs.corrections_budget() == 0

    def boom():
        raise RuntimeError("settings not booted")

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", boom)
    assert memory_refs.corrections_budget() == 1500
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", lambda: SimpleNamespace())
    assert memory_refs.corrections_budget() == 1500
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", snapshot_with(core_budget_chars=12000))
    assert memory_refs.corrections_budget() == 1500
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", snapshot_with(corrections_budget_chars=None))
    assert memory_refs.corrections_budget() == 1500
