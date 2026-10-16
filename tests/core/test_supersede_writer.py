"""Wave 1a supersede mechanics: one transaction, original NEVER altered."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from core.database import PalliBrain


def _db(tmp_path: Path) -> Path:
    return tmp_path / f"supersede_{uuid.uuid4().hex}.sqlite3"


async def _fetch(brain, fact_id):
    from ai.fact_retrieval import get_user_facts_raw
    rows = await get_user_facts_raw(user_id=7, brain_client=brain)
    return next(r for r in rows if r["id"] == fact_id)


@pytest.mark.asyncio
async def test_supersede_inserts_gist_and_marks_original(tmp_path):
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        old = await brain.store_user_memory(
            user_id=7, memory_type="moment",
            content="2026-05-02: compared three pergola quotes line by line",
            importance=0.8, tags=["yard"], salience_weight=1.2)
        new_id = await brain.supersede_user_memory_with_gist(
            user_id=7, memory_id=old,
            gist_content="early May: he was deep in pergola quotes, I think",
            source_detail={"consolidated_from": old, "pass_day": "2026-07-15"})
        assert isinstance(new_id, int) and new_id != old
        original = await _fetch(brain, old)
        assert original["content"] == "2026-05-02: compared three pergola quotes line by line"  # byte-identical
        assert original["status"] == "superseded"
        assert original["superseded_by_fact_id"] == str(new_id)
        assert original["salience_weight"] == pytest.approx(0.6)  # 1.2 * 0.5
        gist = await _fetch(brain, new_id)
        assert gist["status"] == "active"
        assert gist["memory_type"] == "moment"
        assert gist["importance_score"] == pytest.approx(0.8)     # inherited
        assert gist["tags"] == ["yard"]                            # inherited
        assert gist["source_detail"]["consolidated_from"] == old
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_supersede_successor_inherits_timestamps_not_now(tmp_path):
    """Review fix (2026-07-15): aging must FADE, not rejuvenate. The successor
    inherits the original's created_at/last_referenced_at so decay continuity
    holds — a 60d-old memory's gist scores like a 60d-old memory, not like
    news from today."""
    import time as _t
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        old = await brain.store_user_memory(
            user_id=7, memory_type="moment", content="2026-05-02: pergola quotes", importance=0.8)
        backdate = _t.time() - 60 * 86400
        conn = await brain.get_connection()
        try:
            await conn.execute(
                "UPDATE user_memory SET created_at=$1, last_referenced_at=$1 WHERE id=$2",
                backdate, old)
        finally:
            await conn.close()
        new_id = await brain.supersede_user_memory_with_gist(
            user_id=7, memory_id=old, gist_content="early May: pergola quotes, I think")
        gist = await _fetch(brain, new_id)
        assert abs(gist["created_at"] - backdate) < 1.0          # inherited
        assert abs(gist["last_referenced_at"] - backdate) < 1.0  # inherited
        # decay continuity: the gist must NOT outrank a genuinely fresh fact
        from ai.fact_retrieval import get_decay_weighted_facts
        fresh = await brain.store_user_memory(
            user_id=7, memory_type="moment", content="this morning: check valve arrived", importance=0.8)
        weighted = await get_decay_weighted_facts(user_id=7, max_results=8, brain_client=brain)
        ids = [w.fact["id"] for w in weighted]
        assert fresh in ids
        assert ids.index(fresh) < ids.index(new_id) if new_id in ids else True
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_supersede_fresh_timestamps_opt_in_for_corrections(tmp_path):
    """Corrections are NEW information — reconcile opts into fresh timestamps."""
    import time as _t
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        old = await brain.store_user_memory(
            user_id=7, memory_type="moment", content="insulation delivery in August", importance=0.7)
        backdate = _t.time() - 20 * 86400
        conn = await brain.get_connection()
        try:
            await conn.execute("UPDATE user_memory SET created_at=$1 WHERE id=$2", backdate, old)
        finally:
            await conn.close()
        new_id = await brain.supersede_user_memory_with_gist(
            user_id=7, memory_id=old, gist_content="insulation delivery moved to September",
            inherit_timestamps=False, new_salience_weight=1.0)
        gist = await _fetch(brain, new_id)
        assert gist["created_at"] > _t.time() - 60  # fresh, not inherited
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_supersede_is_noop_on_already_superseded_and_missing(tmp_path):
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        old = await brain.store_user_memory(user_id=7, memory_type="moment", content="x", importance=0.5)
        first = await brain.supersede_user_memory_with_gist(user_id=7, memory_id=old, gist_content="gist of x")
        again = await brain.supersede_user_memory_with_gist(user_id=7, memory_id=old, gist_content="second gist")
        assert first is not None and again is None  # idempotent no-op
        missing = await brain.supersede_user_memory_with_gist(user_id=7, memory_id=999999, gist_content="g")
        assert missing is None
        from ai.fact_retrieval import get_user_facts_raw
        rows = await get_user_facts_raw(user_id=7, brain_client=brain)
        assert len(rows) == 2  # exactly one successor was ever created
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_get_user_memories_hides_superseded_by_default(tmp_path):
    """Review fix: the shared reader now defaults to active-only — Discord
    /insights stops listing stale originals beside their corrections; the
    web account view opts into raw explicitly (it is the owner's only
    view/delete surface for superseded rows)."""
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        old = await brain.store_user_memory(
            user_id=7, memory_type="moment", content="original note", importance=0.8)
        new_id = await brain.supersede_user_memory_with_gist(
            user_id=7, memory_id=old, gist_content="the gist of it")
        default = await brain.get_user_memories(user_id=7, min_importance=0.1)
        assert [m["id"] for m in default] == [new_id]
        raw = await brain.get_user_memories(user_id=7, min_importance=0.1, include_superseded=True)
        assert {m["id"] for m in raw} == {old, new_id}
    finally:
        await brain.close()
