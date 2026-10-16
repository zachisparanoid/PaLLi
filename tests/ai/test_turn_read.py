# tests/ai/test_turn_read.py
"""Caring memory — the unified per-turn read (ai/turn_read.py).

Covers the parse/fallback contract (the read must NEVER break a turn) and the
system-prompt rendering rules (read replaces the canned care note; high
needs_support escalates framing; arc renders as background continuity).
"""
from __future__ import annotations

from ai.turn_read import TurnRead, _clamp01, _parse


def test_parse_valid_json():
    raw = (
        '{"read": "He is quietly worn down under the jokes.", "weight": 0.7,'
        ' "needs_support": 0.55, "carries_forward": "waiting on the permit decision",'
        ' "memory_worthy": "His permit decision lands this week."}'
    )
    r = _parse(raw)
    assert r is not None
    assert "worn down" in r.read
    assert r.weight == 0.7
    assert r.needs_support == 0.55
    assert r.carries_forward.startswith("waiting")
    assert r.memory_worthy.startswith("His permit")


def test_parse_tolerates_fencing_and_prose():
    raw = 'Sure! Here is the JSON:\n```json\n{"read": "ordinary check-in", "weight": 0.05}\n```'
    r = _parse(raw)
    assert r is not None
    assert r.read == "ordinary check-in"
    assert r.weight == 0.05
    assert r.carries_forward == ""  # missing fields default empty/zero


def test_parse_garbage_returns_none():
    assert _parse("") is None
    assert _parse("no json here at all") is None
    assert _parse('{"weight": 0.9}') is None  # no read text -> useless, skip


def test_clamping_out_of_range_values():
    raw = '{"read": "x", "weight": 7, "needs_support": -3}'
    r = _parse(raw)
    assert r is not None
    assert r.weight == 1.0
    assert r.needs_support == 0.0
    assert _clamp01("not a number") == 0.0


class _Ctx:
    identity_core_text = "# Identity\nYou are PaLLi."
    skills_text = ""
    runtime_text = ""
    memory_text = ""
    facts_text = ""
    register_directive = "(Care, from your own voice.) canned-note"
    situational_text = ""
    relationship_text = ""
    turn_read_text = ""
    emotional_arc_text = ""
    needs_support = 0.0


def _build(ctx):
    from core.agent.system_prompt import build_system_prompt
    return build_system_prompt(ctx, "cadence: x")


def test_read_replaces_canned_care_note():
    ctx = _Ctx()
    ctx.turn_read_text = "He's carrying something about the permit tonight."
    out = _build(ctx)
    assert "Your read of this moment" in out
    assert "carrying something about the permit" in out
    assert "canned-note" not in out  # one steer, not two


def test_without_read_canned_note_survives():
    ctx = _Ctx()
    out = _build(ctx)
    assert "canned-note" in out
    assert "Your read of this moment" not in out


def test_high_needs_support_escalates_framing():
    ctx = _Ctx()
    ctx.turn_read_text = "He is really struggling tonight."
    ctx.needs_support = 0.8
    out = _build(ctx)
    assert "show up for it" in out
    ctx2 = _Ctx()
    ctx2.turn_read_text = "Ordinary good evening."
    ctx2.needs_support = 0.1
    out2 = _build(ctx2)
    assert "show up for it" not in out2
    assert "Private awareness" in out2


def test_arc_renders_as_background():
    ctx = _Ctx()
    ctx.emotional_arc_text = "- 3 days ago: heavy about the permit decision"
    out = _build(ctx)
    assert "How they've been lately" in out
    assert "heavy about the permit decision" in out


# ── search_timeline (introspect tool read path, 2026-07-13) ───────────────


import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from core.database import PalliBrain  # noqa: E402


@pytest_asyncio.fixture
async def tl_brain(tmp_path):
    db = PalliBrain(str(tmp_path / "timeline.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _seed_read(db, *, owner_id=1, age_days=0.0, text="", weight=0.5):
    import time as _t

    conn = await db.get_connection()
    try:
        await conn.execute(
            "INSERT INTO emotional_timeline "
            "(owner_user_id, created_at, read_text, weight, needs_support, carries_forward) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            int(owner_id),
            _t.time() - age_days * 86400.0,
            text,
            float(weight),
            0.0,
            "",
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_search_timeline_token_match_newest_first(tl_brain):
    from ai.turn_read import search_timeline

    await _seed_read(tl_brain, age_days=5.0, text="he was heavy about the budget math")
    await _seed_read(tl_brain, age_days=1.0, text="lighter tonight; the budget relief showed")
    await _seed_read(tl_brain, age_days=0.5, text="an ordinary check-in, nothing loaded")
    got = await search_timeline(
        owner_id=1, query_tokens=["budget"], days_back=30, brain_client=tl_brain
    )
    assert [("relief" in e["read_text"]) for e in got] == [True, False]
    assert got[0]["created_at"] > got[1]["created_at"]


@pytest.mark.asyncio
async def test_search_timeline_days_back_window(tl_brain):
    from ai.turn_read import search_timeline

    await _seed_read(tl_brain, age_days=45.0, text="the old survey worry")
    got = await search_timeline(
        owner_id=1, query_tokens=["survey"], days_back=30, brain_client=tl_brain
    )
    assert got == []


@pytest.mark.asyncio
async def test_search_timeline_owner_scoped(tl_brain):
    from ai.turn_read import search_timeline

    await _seed_read(tl_brain, owner_id=2, age_days=1.0, text="someone else's workshop day")
    got = await search_timeline(
        owner_id=1, query_tokens=["workshop"], days_back=30, brain_client=tl_brain
    )
    assert got == []


@pytest.mark.asyncio
async def test_search_timeline_limit_and_empty_tokens(tl_brain):
    from ai.turn_read import search_timeline

    for i in range(8):
        await _seed_read(tl_brain, age_days=float(i), text=f"workshop note {i}")
    got = await search_timeline(
        owner_id=1, query_tokens=["workshop"], days_back=30, limit=3, brain_client=tl_brain
    )
    assert len(got) == 3
    assert (
        await search_timeline(
            owner_id=1, query_tokens=[], days_back=30, brain_client=tl_brain
        )
        == []
    )


@pytest.mark.asyncio
async def test_search_timeline_best_effort_on_broken_brain():
    from ai.turn_read import search_timeline

    class _Broken:
        async def get_connection(self):
            raise RuntimeError("db down")

    got = await search_timeline(
        owner_id=1, query_tokens=["anything"], brain_client=_Broken()
    )
    assert got == []
