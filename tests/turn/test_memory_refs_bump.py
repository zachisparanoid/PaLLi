"""Batch 5, task 5 (memory that picks, 2026-09-28): a line she actually used
gets a reference stamp.

After the reply, ``apply_reflection`` records what recall SURFACED for the
turn (ruling R6: "use" = surfaced, including bullets the core already
shows): (a) every ``mcb_`` id on ``ctx.memory_lookup_ids`` through
``memory_refs.bump`` — the ``memory_bullet_refs`` table takes the time and
counts up; (b) the ``user_memory`` row ids of the fact entries the "Things
I Know" band rendered (``ctx.relevant_facts`` entries carrying an int
``id``) through ``ai.fact_retrieval.queue_reference_bump_ids`` (R24/R41: the
old bump finally has a caller) — the task it queues is awaited inside the
helper (fix wave, T5 M19), so the stamp has landed when the helper returns
and nothing here settles stray tasks. One helper, ``_record_memory_use``,
placed after ``_persist_caring_memory`` and before ``_extract_plan``; it
never raises, and it never refreshes the in-process refs snapshot (R7).

The bullet stamps are asserted against the suite's tmp database, the way
Task 3's tests do; the user_memory bump is asserted both against the real
row and through a recorder at the brain seam (the exact ids handed over).
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest

from core import memory_refs
from core.database import PalliBrain, brain
from core.turn import reflect
from core.turn.reflect import apply_reflection
from core.turn.types import ReflectionMemoryUpdates, ReflectionPlan

# Not the fixtures' owner 1 and not Task 3's 4242: this module writes stamps
# and loads snapshots for its owner.
OWNER = 5150
A, B = "mcb_aaaaaaaaaaaa", "mcb_bbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _clean_slate():
    memory_refs.forget()
    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())
    memory_refs.forget()


async def _wipe() -> None:
    conn = await brain.get_connection()
    try:
        await conn.execute("DELETE FROM memory_bullet_refs WHERE owner_user_id = $1", OWNER)
        await conn.execute("DELETE FROM user_memory WHERE user_id = $1", OWNER)
    finally:
        await conn.close()


def _ctx(**fields):
    """A hand-built context the way the reflect tests build one: only
    ``turn.owner_id`` is required on the pre-plan path; Task 4's two fields
    are given when the test wants them (a context WITHOUT them must be
    tolerated — older hand-built contexts have neither)."""
    return SimpleNamespace(
        turn=SimpleNamespace(owner_id=OWNER, surface="local", content="", metadata=SimpleNamespace()),
        **fields,
    )


def _bullet_entry(item_id: str) -> dict:
    """A bullet as ``plan_facts_band`` puts it in the band: a line, an
    ``item_id`` and no ``id``."""
    return {"line": f"- [2026-09-01] a line ({item_id})", "item_id": item_id, "source_kind": "memory_bullet"}


def _fact_entry(row_id, key: str = "editor", value: str = "vim") -> dict:
    """A user_memory fact as ``_w_facts`` shapes it (R39: ``id`` and
    ``memory_type`` ride along)."""
    return {"key": key, "value": value, "confidence": 0.9, "id": row_id, "memory_type": "preference"}


def _empty_plan(**overrides) -> ReflectionPlan:
    base: dict = dict(
        memory_updates=ReflectionMemoryUpdates(),
        personality_observations=[],
        relationship_delta=None,
        user_facts_add=[],
        monologue_note=None,
        first_contact_resolutions={},
        skip_reason=None,
    )
    base.update(overrides)
    return ReflectionPlan(**base)


async def _reflect(ctx, plan=None) -> bool:
    """Drive ``apply_reflection`` with the LLM plan stubbed (no network)."""
    with patch("core.turn.reflect._extract_plan", return_value=plan):
        return await apply_reflection(ctx, response_text="...")


def _stamps(refs: dict) -> dict:
    return {item_id: ref["count"] for item_id, ref in refs.items()}


# ── (a) the bullet stamps, on the real table ────────────────────────────────


@pytest.mark.asyncio
async def test_a_reflected_turn_stamps_every_bullet_recall_surfaced():
    """Two ids on the context leave two stamps; a later turn that surfaced
    only one of them counts that one up and leaves the other alone."""
    assert await memory_refs.load_refs(OWNER) == {}

    applied = await _reflect(_ctx(memory_lookup_ids=(A, B), relevant_facts=()))

    assert applied is False  # no plan: the reflection applied nothing, the stamps still landed
    refs = await memory_refs.load_refs(OWNER)
    assert _stamps(refs) == {A: 1, B: 1}
    assert all(ref["last_referenced_at"] > 1_700_000_000.0 for ref in refs.values())

    await _reflect(_ctx(memory_lookup_ids=(A,), relevant_facts=()))
    assert _stamps(await memory_refs.load_refs(OWNER)) == {A: 2, B: 1}


@pytest.mark.asyncio
async def test_a_turn_that_surfaced_nothing_leaves_no_stamp_and_calls_no_bump(monkeypatch):
    """No ids: nothing is written and neither brain bump is even called —
    whether the context carries empty fields or, like an older hand-built
    context, none at all."""
    calls: list[str] = []

    async def bullets_bump(**_kw):
        calls.append("bullets")

    async def facts_bump(**_kw):
        calls.append("facts")

    monkeypatch.setattr(brain, "bump_memory_bullet_refs", bullets_bump)
    monkeypatch.setattr(brain, "bump_user_memory_references", facts_bump)

    assert await _reflect(_ctx(memory_lookup_ids=(), relevant_facts=())) is False
    assert await _reflect(_ctx()) is False  # no memory_lookup_ids / relevant_facts attribute at all

    assert calls == []
    assert await memory_refs.load_refs(OWNER) == {}


# ── (b) the user_memory facts the band rendered ─────────────────────────────


@pytest.mark.asyncio
async def test_the_rendered_facts_ids_reach_the_user_memory_bump_and_bullet_entries_are_skipped(monkeypatch):
    """Only entries carrying an int ``id`` are handed to the old bump: a
    bullet entry (item_id, no id) and a fact whose id is None are skipped;
    a repeated id is handed once."""
    recorded: list[tuple[int, list[int]]] = []

    async def facts_bump(*, user_id, memory_ids):
        recorded.append((int(user_id), list(memory_ids)))

    monkeypatch.setattr(brain, "bump_user_memory_references", facts_bump)

    ctx = _ctx(
        memory_lookup_ids=(),
        relevant_facts=(
            _bullet_entry(A),
            _fact_entry(7),
            _fact_entry(None, key="unsaved"),
            _fact_entry(9, key="coffee", value="black"),
            _fact_entry(7),
        ),
    )
    await _reflect(ctx)

    assert recorded == [(OWNER, [7, 9])]  # already there: the bump was awaited, not settled after


@pytest.mark.asyncio
async def test_a_rendered_fact_row_is_restamped_in_the_real_table():
    """End to end on the tmp database: the row the band rendered takes
    ``reference_count + 1`` and a fresh ``last_referenced_at``; a sibling
    row the band did not render is untouched."""
    shown = await brain.store_user_memory(user_id=OWNER, memory_type="preference", content="Uses vim.", importance=0.9)
    hidden = await brain.store_user_memory(user_id=OWNER, memory_type="preference", content="Drinks tea.", importance=0.9)
    assert shown is not None and hidden is not None

    await _reflect(_ctx(memory_lookup_ids=(), relevant_facts=(_fact_entry(shown),)))

    conn = await brain.get_connection()
    try:
        rows = await conn.fetch(
            "SELECT id, reference_count, last_referenced_at FROM user_memory WHERE user_id = $1 ORDER BY id",
            OWNER,
        )
    finally:
        await conn.close()
    by_id = {int(row[0]): (int(row[1] or 0), float(row[2] or 0.0)) for row in rows}
    assert by_id[shown][0] == 1 and by_id[shown][1] > 1_700_000_000.0
    assert by_id[hidden][0] == 0


# ── never raises, never touches the snapshot ────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_bump_never_changes_what_the_reflection_returns(monkeypatch, caplog):
    """Both bumps blow up: the reflection still applies its plan and returns
    True; with no plan it still returns False. The failures are logged."""
    async def bullets_boom(*_a, **_kw):
        raise RuntimeError("bullet stamps down")

    def facts_boom(**_kw):
        raise RuntimeError("fact bump down")

    from ai import fact_retrieval

    monkeypatch.setattr(memory_refs, "bump", bullets_boom)
    monkeypatch.setattr(fact_retrieval, "queue_reference_bump_ids", facts_boom)
    caplog.set_level(logging.WARNING, logger=reflect.logger.name)

    ctx = _ctx(memory_lookup_ids=(A,), relevant_facts=(_fact_entry(7),))
    fake_brain = create_autospec(PalliBrain, instance=True)
    fake_brain.add_open_thread.return_value = "thread-1"
    plan = _empty_plan(open_threads_to_add=[
        {"topic_summary": "x", "why_it_matters": "y", "kind": "work", "urgency": "z"},
    ])
    with patch("core.turn.reflect.brain", fake_brain):
        assert await _reflect(ctx, plan=plan) is True
    fake_brain.add_open_thread.assert_awaited_once()

    assert await _reflect(ctx, plan=None) is False
    assert "bullet stamps down" in caplog.text and "fact bump down" in caplog.text
    assert await memory_refs.load_refs(OWNER) == {}  # nothing landed, nothing raised


@pytest.mark.asyncio
async def test_a_bump_from_the_reply_path_never_touches_the_snapshot():
    """R7: the core's ranking inputs move once a day. The stamps a turn
    leaves are in the table at once, and NOT in the in-process snapshot —
    neither when the snapshot is empty nor when it already holds a picture."""
    await memory_refs.ensure_loaded(OWNER)
    assert memory_refs.snapshot(OWNER) == {}
    await _reflect(_ctx(memory_lookup_ids=(A,), relevant_facts=()))
    assert memory_refs.snapshot(OWNER) == {}
    assert _stamps(await memory_refs.load_refs(OWNER)) == {A: 1}

    memory_refs.forget(OWNER)
    await memory_refs.ensure_loaded(OWNER)
    before = dict(memory_refs.snapshot(OWNER))
    assert set(before) == {A}
    await _reflect(_ctx(memory_lookup_ids=(A, B), relevant_facts=()))
    assert memory_refs.snapshot(OWNER) == before  # still the loaded picture
    assert _stamps(await memory_refs.load_refs(OWNER)) == {A: 2, B: 1}  # the table moved on


@pytest.mark.asyncio
async def test_record_memory_use_reports_how_many_ids_each_bump_was_handed(monkeypatch):
    """The helper's return: small counts — what it handed to each bump, and
    zero for a bump that failed or had nothing."""
    async def facts_bump(**_kw):
        pass

    monkeypatch.setattr(brain, "bump_user_memory_references", facts_bump)
    ctx = _ctx(memory_lookup_ids=(A, B), relevant_facts=(_bullet_entry(A), _fact_entry(7)))
    assert await reflect._record_memory_use(ctx, OWNER) == {"bullets": 2, "facts": 1}

    async def bullets_boom(*_a, **_kw):
        raise RuntimeError("down")

    monkeypatch.setattr(memory_refs, "bump", bullets_boom)
    assert await reflect._record_memory_use(ctx, OWNER) == {"bullets": 0, "facts": 1}
    assert await reflect._record_memory_use(_ctx(), OWNER) == {"bullets": 0, "facts": 0}


@pytest.mark.asyncio
async def test_the_fact_bump_has_landed_when_record_memory_use_returns(monkeypatch):
    """Fix wave (T5 M19): the queued bump task is awaited inside the helper,
    not dropped — the recorder holds the ids the moment the helper returns,
    with no stray task left on the loop; and a bump that fails inside its
    task is its own warning, never ours: nothing raises and the count says
    what was handed over."""
    recorded: list[list[int]] = []

    async def facts_bump(*, user_id, memory_ids):
        recorded.append(list(memory_ids))

    monkeypatch.setattr(brain, "bump_user_memory_references", facts_bump)
    ctx = _ctx(memory_lookup_ids=(), relevant_facts=(_fact_entry(3), _fact_entry(4)))
    assert await reflect._record_memory_use(ctx, OWNER) == {"bullets": 0, "facts": 2}
    assert recorded == [[3, 4]]
    current = asyncio.current_task()
    assert [t for t in asyncio.all_tasks() if t is not current and not t.done()] == []

    async def facts_boom(*, user_id, memory_ids):
        raise RuntimeError("bump down inside its task")

    monkeypatch.setattr(brain, "bump_user_memory_references", facts_boom)
    assert await reflect._record_memory_use(ctx, OWNER) == {"bullets": 0, "facts": 2}
