"""Wave 1a readers: day-window + content search over conversations (READ-only),
monologue by specific day, night reflection by specific date."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest

from core.database import PalliBrain


def _db(tmp_path: Path) -> Path:
    return tmp_path / f"convread_{uuid.uuid4().hex}.sqlite3"


async def _seed(brain, *, content, ts, platform="dashboard", role="user"):
    cid = await brain.store_conversation(
        user_id=1, platform=platform, channel_id=1, thread_id=None,
        role=role, content=content, username="Sam" if role == "user" else "Me",
    )
    conn = await brain.get_connection()
    try:
        await conn.execute("UPDATE conversations SET timestamp = $1 WHERE id = $2", ts, cid)
    finally:
        await conn.close()
    return cid


@pytest.mark.asyncio
async def test_day_window_ascending_and_reminder_fire_excluded(tmp_path):
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        day0 = datetime(2026, 5, 2, 0, 0, 0).timestamp()
        await _seed(brain, content="evening line", ts=day0 + 20 * 3600)
        await _seed(brain, content="morning line", ts=day0 + 9 * 3600)
        await _seed(brain, content="machinery", ts=day0 + 10 * 3600, platform="reminder_fire", role="assistant")
        await _seed(brain, content="next day", ts=day0 + 86400 + 60)
        rows = await brain.get_conversations_for_day(day_start_ts=day0, day_end_ts=day0 + 86400)
        assert [r["content"] for r in rows] == ["morning line", "evening line"]
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_content_search_and_semantics_window_and_cap(tmp_path):
    import time as _t
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        now = _t.time()
        await _seed(brain, content="the pergola estimate came back high", ts=now - 40 * 86400)
        await _seed(brain, content="pergola paint colors", ts=now - 10 * 86400)
        await _seed(brain, content="estimate for the patio", ts=now - 5 * 86400)
        await _seed(brain, content="ancient pergola estimate", ts=now - 400 * 86400)
        both = await brain.search_conversation_content(terms=["pergola", "estimate"], days_back=365)
        assert [r["content"] for r in both] == ["the pergola estimate came back high"]
        any_of = await brain.search_conversation_content(
            terms=["pergola", "estimate"], days_back=365, match_all=False, limit=2)
        assert len(any_of) == 2  # capped
        # review fix: OR mode ranks by match count, then recency — the old
        # 2-term match beats the newer 1-term match
        assert any_of[0]["content"] == "the pergola estimate came back high"
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_content_search_escapes_like_wildcards(tmp_path):
    """Review fix: '_' and '%' in terms must match literally, not as wildcards
    — exact mode presents results as verbatim record hits."""
    import time as _t
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        now = _t.time()
        await _seed(brain, content="the day_gist store landed", ts=now - 2 * 86400)
        await _seed(brain, content="the dayXgist store landed", ts=now - 1 * 86400)
        rows = await brain.search_conversation_content(terms=["day_gist"], days_back=30)
        assert [r["content"] for r in rows] == ["the day_gist store landed"]
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_content_search_or_mode_ranks_by_match_count_not_recency(tmp_path):
    """Review fix: the OR fallback must return the BEST match, not the newest
    row containing any filler word."""
    import time as _t
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        now = _t.time()
        await _seed(brain, content="shelf brackets are original oak", ts=now - 40 * 86400)  # 2 terms, old
        await _seed(brain, content="new brackets for the pantry", ts=now - 1 * 86400)          # 1 term, new
        rows = await brain.search_conversation_content(
            terms=["shelf", "brackets"], days_back=365, match_all=False)
        assert rows[0]["content"] == "shelf brackets are original oak"
    finally:
        await brain.close()


def test_monologue_read_entries_for_day(tmp_path, monkeypatch):
    import json as _json

    import ai.inner_monologue_store as mono
    monkeypatch.setattr(mono, "MONOLOGUE_DIR", tmp_path)
    ts = datetime(2026, 5, 2, 14, 0, 0).timestamp()
    e1 = mono.MonologueEntry(timestamp=ts, thought="afternoon thought", situation_snapshot={})
    e2 = mono.MonologueEntry(timestamp=ts - 4 * 3600, thought="morning thought", situation_snapshot={})
    e3 = mono.MonologueEntry(timestamp=ts + 86400, thought="next-day thought", situation_snapshot={})
    for e in (e1, e2, e3):
        path = tmp_path / (e.datetime_local.strftime("%Y-%m-%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(e.to_dict()) + "\n")
    got = mono.read_entries_for_day(day="2026-05-02")
    assert [g.thought for g in got] == ["morning thought", "afternoon thought"]


def test_night_reflection_read_for_specific_date(tmp_path):
    from ai import night_cycle_store as ncs
    ncs.write_daily_meaning(date="2026-07-13", text="the day the cycle first ran", root=tmp_path)
    got = ncs.read_daily_meaning_for(date="2026-07-13", root=tmp_path)
    assert got is not None and "first ran" in got.text
    assert ncs.read_daily_meaning_for(date="2026-07-12", root=tmp_path) is None


@pytest.mark.asyncio
async def test_day_reader_keeps_the_evening_when_capped(tmp_path):
    """Review fix: a >limit day used to silently drop the END of the day —
    the exact part the peak-and-ending law needs. At the cap, the reader now
    merges in the day's tail."""
    brain = PalliBrain(str(_db(tmp_path)))
    await brain.initialize()
    try:
        day0 = datetime(2026, 5, 2, 0, 0, 0).timestamp()
        for i in range(15):
            await _seed(brain, content=f"line {i}", ts=day0 + 3600 + i * 60)
        rows = await brain.get_conversations_for_day(
            day_start_ts=day0, day_end_ts=day0 + 86400, limit=10)
        contents = [r["content"] for r in rows]
        assert "line 0" in contents          # the morning survives
        assert "line 14" in contents         # the evening survives
        assert contents == sorted(contents, key=lambda c: int(c.split()[1]))  # chronological
    finally:
        await brain.close()
