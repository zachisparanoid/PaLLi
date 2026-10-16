"""Day-gist FTS reach (Wave 1a): indexed, re-indexable, rebuild-covered."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest

from core.database import PalliBrain


def _db(tmp_path: Path) -> Path:
    return tmp_path / f"daygist_{uuid.uuid4().hex}.sqlite3"


async def _rows(brain, entry_id):
    conn = await brain.get_connection()
    try:
        return await conn.fetch(
            "SELECT source_kind, timestamp, text_content FROM monologue_memory_fts WHERE entry_id = $1",
            entry_id,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_index_day_gist_upserts_with_day_noon_timestamp(tmp_path):
    from ai.inner_memory_index import index_day_gist
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        await index_day_gist(day="2026-05-02", text="the reminder-storm week", brain_client=brain)
        await index_day_gist(day="2026-05-02", text="the reminder-storm week, revised", brain_client=brain)
        rows = await _rows(brain, "day_gist:2026-05-02")
        assert len(rows) == 1  # delete-then-insert upsert, no duplicates
        assert str(rows[0][0]) == "day_gist"
        assert "revised" in str(rows[0][2])
        expected = datetime(2026, 5, 2, 12, 0, 0).timestamp()
        assert abs(float(rows[0][1]) - expected) < 1.0
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_query_for_introspection_reaches_day_gists(tmp_path):
    from ai.inner_memory_index import index_day_gist, query_for_introspection
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        import datetime as dt
        recent = (dt.date.today() - dt.timedelta(days=3)).isoformat()
        await index_day_gist(day=recent, text="the pergola estimate finally landed", brain_client=brain)
        hits = await query_for_introspection("pergola estimate", days_back=30, brain_client=brain)
        assert any(h.source_kind == "day_gist" and "pergola" in h.text_content for h in hits)
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_passive_query_never_serves_day_gists(tmp_path):
    """Review fix (2026-07-15): day-gists are for DELIBERATE introspection —
    the spec scoped ambient-recall integration OUT of v1, but the passive
    reply-path query() reads the same FTS table. It must exclude the kind
    (undated whole-day summaries would color every reply and survive a
    consolidation-flag rollback)."""
    import time as _t

    from ai.inner_memory_index import index_day_gist, query, query_for_introspection
    from ai.inner_monologue_store import MonologueEntry
    from ai.inner_memory_index import index_entry
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        import datetime as dt
        recent = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        await index_day_gist(day=recent, text="the pergola question dominated that day", brain_client=brain)
        entry = MonologueEntry(timestamp=_t.time() - 3600, thought="pergola math again tonight",
                               situation_snapshot={}, tick_metadata={"substantive": True})
        await index_entry(entry, brain_client=brain)

        passive = await query(text="pergola", max_results=3, brain_client=brain)
        assert passive and all(h.source_kind != "day_gist" for h in passive)

        deliberate = await query_for_introspection("pergola", days_back=30, brain_client=brain)
        assert any(h.source_kind == "day_gist" for h in deliberate)  # introspect keeps reach
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_rebuild_from_archive_includes_day_gists(tmp_path, monkeypatch):
    import ai.day_gist_store as dgs
    import ai.inner_monologue_store as mono
    from ai.inner_memory_index import rebuild_from_archive
    gist_dir = tmp_path / "day_gists"
    mono_dir = tmp_path / "inner_monologue"
    mono_dir.mkdir()
    monkeypatch.setattr(dgs, "DAY_GIST_DIR", gist_dir)
    monkeypatch.setattr(mono, "MONOLOGUE_DIR", mono_dir)
    dgs.write_day_gist(day="2026-05-02", text="storm week", root=gist_dir)
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        counts = await rebuild_from_archive(brain_client=brain)
        assert counts["day_gists"] == 1
        rows = await _rows(brain, "day_gist:2026-05-02")
        assert len(rows) == 1
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_rebuild_skips_poison_gist_file_instead_of_aborting(tmp_path, monkeypatch):
    """Review fix: one malformed day field used to abort the ENTIRE rebuild
    transaction (monologue + digests + all gists rolled back). Bad files are
    skipped like every sibling walker does; the validated filename stem is
    the indexing key."""
    import json as _json

    import ai.day_gist_store as dgs
    import ai.inner_monologue_store as mono
    from ai.inner_memory_index import rebuild_from_archive
    gist_dir = tmp_path / "day_gists"
    gist_dir.mkdir()
    mono_dir = tmp_path / "inner_monologue"
    mono_dir.mkdir()
    monkeypatch.setattr(dgs, "DAY_GIST_DIR", gist_dir)
    monkeypatch.setattr(mono, "MONOLOGUE_DIR", mono_dir)
    dgs.write_day_gist(day="2026-05-02", text="a good gist", root=gist_dir)
    # poison: valid JSON, content-side day is garbage (hand-edit scenario)
    (gist_dir / "2026-05-03.jsonl").write_text(
        _json.dumps({"day": "May 3rd", "text": "poison", "created_at": 1.0, "meta": {}}) + "\n",
        encoding="utf-8")
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        counts = await rebuild_from_archive(brain_client=brain)
        assert counts["day_gists"] == 2  # poison file indexed under its VALID stem
        rows = await _rows(brain, "day_gist:2026-05-03")
        assert len(rows) == 1            # keyed by the filename's day, not the bad field
    finally:
        await brain.close()
