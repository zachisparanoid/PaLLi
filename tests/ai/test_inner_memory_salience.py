# tests/ai/test_inner_memory_salience.py
"""Salience (spec §4): entries written inside a surprising window index
heavier (×boost, applied AFTER recency, never resurrects the expired); the
schema self-migrates (an old table without the column is copy-swapped in one
transaction, rows preserved, once); NULL salience scores as 1.0."""
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

import ai.inner_memory_index as imi
import ai.surprise_state as ss
from ai.inner_monologue_store import MonologueEntry
from core.database import PalliBrain


@pytest_asyncio.fixture
async def brain(tmp_path):
    db = PalliBrain(str(tmp_path / "idx.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _entry(thought, ts):
    return MonologueEntry(timestamp=ts, thought=thought, situation_snapshot={},
                          tick_metadata={"substantive": True})


@pytest.mark.asyncio
async def test_window_entries_index_heavier_and_outrank(brain, monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(ss, "_companion", lambda: SimpleNamespace(surprise_salience_minutes=60))
    monkeypatch.setattr(imi, "_surprise_boost", lambda: 1.5)
    now = time.time()
    await imi.index_entry(_entry("the lake page ordinary note", now - 120), brain_client=brain)
    ss.mark(about="him", weight="real", at=now - 60)
    await imi.index_entry(_entry("the lake page surprising note", now - 30), brain_client=brain)
    hits = await imi.query(text="lake page", max_results=2, brain_client=brain)
    assert len(hits) == 2
    assert "surprising" in hits[0].text_content
    assert hits[0].relevance_score > hits[1].relevance_score


@pytest.mark.asyncio
async def test_old_schema_self_migrates_once(brain, monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "STATE_PATH", tmp_path / "s.json")
    # build the OLD table by hand (no salience column) — the bootstrap
    # catalog creates the NEW shape at initialize(), so drop that first
    # to simulate a genuinely legacy database.
    conn = await brain.get_connection()
    try:
        await conn.execute("DROP TABLE IF EXISTS monologue_memory_fts")
        await conn.execute("""
            CREATE VIRTUAL TABLE monologue_memory_fts USING fts5(
                entry_id UNINDEXED, source_kind, source_path UNINDEXED,
                timestamp UNINDEXED, text_content, sentiment_tag, surfaced_candidate
            )""")
        await conn.execute(
            "INSERT INTO monologue_memory_fts VALUES ($1,$2,$3,$4,$5,$6,$7)",
            "old1", "monologue", "x", time.time(), "an old row about the claw", "", "")
    finally:
        await conn.close()
    imi._reset_schema_cache_for_tests()
    await imi.ensure_schema(brain_client=brain)
    # the migrated table must answer queries without error AND keep her
    # old rows (the copy-swap is atomic — migration never costs memory)
    hits = await imi.query(text="claw", max_results=2, brain_client=brain)
    assert any("old row" in h.text_content for h in hits)
    await imi.index_entry(_entry("the claw new row", time.time()), brain_client=brain)
    hits = await imi.query(text="claw", max_results=2, brain_client=brain)
    assert any("new row" in h.text_content for h in hits)
    assert any("old row" in h.text_content for h in hits)


@pytest.mark.asyncio
async def test_null_salience_scores_as_one(brain):
    conn = await brain.get_connection()
    try:
        await imi.ensure_schema(brain_client=brain)
        await conn.execute(
            "INSERT INTO monologue_memory_fts (entry_id, source_kind, source_path, "
            "timestamp, text_content, sentiment_tag, surfaced_candidate) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            "n1", "monologue", "x", time.time(), "null salience row", "", "")
    finally:
        await conn.close()
    hits = await imi.query(text="salience row", max_results=1, brain_client=brain)
    assert hits and 0 < hits[0].relevance_score <= 1.0
