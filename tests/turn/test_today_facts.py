"""Today's facts (R15, memory that reaches, T12): a small dated "today" slot.

What Robin tells PaLLi during the day is written by the after-reply
reflection at once (``user_memory`` rows with ``memory_type="today"``),
read into the reply's prompt under its own heading ("## Today, so far"),
and retired by the nightly pass once the night has filed it — so a 7 PM
line counts as a fact at 7:01 PM, not the next morning.

The slot's one gate, ``core.turn.companion_flags.today_slot_on``, reads
``companion.memory_reach``; the suite holds it OFF (tests/conftest.py,
``_today_slot_off_by_default``) and the tests here that want it ON patch
it in their own bodies. Off means today, byte for byte: no rows written,
no section, nothing retired.

Every store test runs on a tmp PalliBrain; no model is ever called (the
reflection's model output is faked as the JSON payload its parser reads).
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ai.fact_retrieval import get_decay_weighted_facts as _REAL_FACTS
from core.database import PalliBrain
from core.turn import companion_flags
from core.turn.types import Turn, TurnContext, TurnMetadata

OWNER = 7
OTHER = 8

TODAY_HEADER = "## Today, so far"
TODAY_LEAD = "What they told me earlier today, kept here until the nightly pass files it:"


# ── helpers ──────────────────────────────────────────────────────────────


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / f"today_facts_{uuid.uuid4().hex}.sqlite3"


async def _brain(tmp_path: Path) -> PalliBrain:
    brain = PalliBrain(str(_db_path(tmp_path)))
    await brain.initialize()
    return brain


def _at(brain: PalliBrain, ts: float) -> None:
    """Set the memory store's clock: rows written next carry ``ts``."""
    brain._memory_store._now = lambda: ts


def _slot(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(companion_flags, "today_slot_on", lambda: on)


async def _rows(brain: PalliBrain, user_id: int = OWNER) -> list[dict]:
    from ai.fact_retrieval import get_user_facts_raw

    return await get_user_facts_raw(user_id=user_id, brain_client=brain)


def _today_rows(rows: list[dict], status: str | None = "active") -> list[dict]:
    return [
        r for r in rows
        if r["memory_type"] == "today" and (status is None or r["status"] == status)
    ]


def _owner_turn(content: str = "hi", owner_id: int = OWNER) -> Turn:
    return Turn(
        surface="dashboard",
        owner_id=owner_id,
        content=content,
        images=[],
        metadata=TurnMetadata(),
        received_at=datetime(2026, 10, 13, 23, 1, 0),
    )


def _guest_turn() -> Turn:
    return Turn(
        surface="discord_text",
        owner_id=12345,
        content="[Dana]: hi",
        metadata=TurnMetadata(channel_id=12345, origin="guest"),
        received_at=datetime(2026, 10, 13, 23, 1, 0),
    )


def _fact(value: str, *, fact_id: int, memory_type: str = "user_fact") -> dict:
    """A user_memory fact as recall's ``_w_facts`` shapes it."""
    return {"key": "fact", "value": value, "confidence": 0.0, "id": fact_id, "memory_type": memory_type}


SEVEN_PM = datetime(2026, 10, 13, 19, 0, 0).timestamp()
# The night's run time (ai.night_cycle.night_run_offset): the slot's day
# starts here — what the night retires, it retires at this hour.
RUN_AT = timedelta(hours=6, minutes=30)
BOUNDARY_13TH = datetime(2026, 10, 13, 6, 30, 0).timestamp()


@pytest.fixture(autouse=True)
def _night_runs_at_six_thirty(monkeypatch):
    """The slot's day boundary is the night's run time, read through the
    night cycle's own helper; pinned here to the 06:30 default so no
    settings file moves it under these tests."""
    import ai.night_cycle as night_cycle

    monkeypatch.setattr(night_cycle, "night_run_offset", lambda companion=None: RUN_AT)


# ── the gate and its suite pin ───────────────────────────────────────────


def test_the_today_slot_is_off_by_default_in_every_test(monkeypatch):
    """The suite's pin holds the slot's gate OFF even when the settings say
    memory_reach is on — the live yaml will carry it on after the wrap."""
    monkeypatch.setattr(
        companion_flags, "companion_settings", lambda: SimpleNamespace(memory_reach=True)
    )
    assert companion_flags.today_slot_on() is False


def test_the_real_gate_reads_companion_memory_reach_fail_closed(monkeypatch):
    gate = companion_flags.today_slot_on_impl
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: SimpleNamespace(memory_reach=True))
    assert gate() is True
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: SimpleNamespace(memory_reach=False))
    assert gate() is False
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: SimpleNamespace())
    assert gate() is False
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: None)
    assert gate() is False

    def boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(companion_flags, "companion_settings", boom)
    assert gate() is False


# ── the store ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_note_today_fact_dedupes_and_stops_at_eight(tmp_path):
    from core.memory_store import TODAY_DAILY_CAP, TODAY_TYPE

    assert (TODAY_TYPE, TODAY_DAILY_CAP) == ("today", 8)
    brain = await _brain(tmp_path)
    try:
        first = await brain.note_today_fact(user_id=OWNER, content="dog_name: Miso", source="reflection")
        again = await brain.note_today_fact(user_id=OWNER, content="dog_name: Miso", source="memory_worthy")
        assert isinstance(first, int) and again == first
        assert len(_today_rows(await _rows(brain))) == 1

        for i in range(2, 9):
            assert isinstance(
                await brain.note_today_fact(user_id=OWNER, content=f"line_{i}: {i}", source="reflection"),
                int,
            )
        assert len(_today_rows(await _rows(brain))) == 8
        # The ninth new line: the slot is full.
        assert await brain.note_today_fact(user_id=OWNER, content="line_9: 9", source="reflection") is None
        # A repeat of a line already kept still finds its row at the cap.
        assert await brain.note_today_fact(user_id=OWNER, content="dog_name: Miso", source="reflection") == first
        assert len(_today_rows(await _rows(brain))) == 8
        # Another person's slot is their own.
        assert isinstance(
            await brain.note_today_fact(user_id=OTHER, content="line_9: 9", source="reflection"), int
        )
        # Nothing for an empty line.
        assert await brain.note_today_fact(user_id=OWNER, content="   ", source="reflection") is None

        row = next(r for r in _today_rows(await _rows(brain)) if r["id"] == first)
        assert row["content"] == "dog_name: Miso"
        assert row["source_detail"] == {"source": "reflection"}
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_active_today_facts_lists_the_active_slot_newest_first(tmp_path):
    brain = await _brain(tmp_path)
    try:
        _at(brain, SEVEN_PM - 7200)
        await brain.note_today_fact(user_id=OWNER, content="older: 1", source="reflection")
        _at(brain, SEVEN_PM)
        await brain.note_today_fact(user_id=OWNER, content="newer: 2", source="memory_worthy")
        await brain.store_user_memory(user_id=OWNER, memory_type="user_fact", content="favorite_color: blue")
        await brain.note_today_fact(user_id=OTHER, content="theirs: 3", source="reflection")
        rows = await brain.active_today_facts(user_id=OWNER)
        assert [r["content"] for r in rows] == ["newer: 2", "older: 1"]
        assert all(r["memory_type"] == "today" and r["status"] == "active" for r in rows)
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_retire_today_facts_retires_only_older_active_today_rows(tmp_path):
    brain = await _brain(tmp_path)
    try:
        _at(brain, SEVEN_PM - 3600)
        old_today = await brain.note_today_fact(user_id=OWNER, content="old: 1", source="reflection")
        old_fact = await brain.store_user_memory(user_id=OWNER, memory_type="user_fact", content="favorite_color: blue")
        other = await brain.note_today_fact(user_id=OTHER, content="theirs: 1", source="reflection")
        _at(brain, SEVEN_PM + 10)
        new_today = await brain.note_today_fact(user_id=OWNER, content="new: 2", source="reflection")

        assert await brain.retire_today_facts(user_id=OWNER, before_ts=SEVEN_PM) == 1
        by_id = {r["id"]: r for r in await _rows(brain)}
        assert by_id[old_today]["status"] == "retired"
        assert by_id[new_today]["status"] == "active"
        assert by_id[old_fact]["status"] == "active"
        assert {r["id"]: r["status"] for r in await _rows(brain, OTHER)} == {other: "active"}
        # Already retired: not counted twice.
        assert await brain.retire_today_facts(user_id=OWNER, before_ts=SEVEN_PM) == 0
        # A retired row frees its place in the slot.
        assert [r["id"] for r in await brain.active_today_facts(user_id=OWNER)] == [new_today]
    finally:
        await brain.close()


# ── the read: recall's facts fetch → plan_band → the prompt ─────────────


async def _recall_ctx(monkeypatch, brain: PalliBrain, turn: Turn, *, now: float) -> TurnContext:
    """recall.build_turn_context end to end, its facts fetch the REAL
    get_decay_weighted_facts on the tmp brain (recall's own call: eight
    facts, the turn's words, without her ``moment`` rows)."""
    import ai.fact_retrieval as fact_retrieval
    from tests.turn.test_recall_facts import _load_recall, _stub_recall_dependencies

    calls: list[dict] = []
    # The harness stubs the per-turn read (no model call) since fix round 1.
    _stub_recall_dependencies(monkeypatch, fact_capture={}, fake_facts=[])

    async def on_the_tmp_brain(**kwargs):
        calls.append(dict(kwargs))
        return await _REAL_FACTS(**kwargs, brain_client=brain, now=now)

    monkeypatch.setattr(fact_retrieval, "get_decay_weighted_facts", on_the_tmp_brain)
    ctx = await _load_recall().build_turn_context(turn)
    assert calls and calls[0]["exclude_memory_types"] == ("moment",)
    return ctx


@pytest.mark.asyncio
async def test_a_seven_pm_line_reaches_the_reply_at_seven_oh_one(tmp_path, monkeypatch):
    from core.agent.context_adapter import build_agent_loop_context

    brain = await _brain(tmp_path)
    try:
        _at(brain, SEVEN_PM - 3 * 86400)
        await brain.store_user_memory(user_id=OWNER, memory_type="user_fact", content="favorite_color: blue")
        _at(brain, SEVEN_PM - 3600)
        await brain.note_today_fact(user_id=OWNER, content="home_city: Lisbon", source="reflection")
        _at(brain, SEVEN_PM)
        await brain.note_today_fact(user_id=OWNER, content="dog_name: Miso", source="reflection")

        turn = _owner_turn("how was your evening?")
        _slot(monkeypatch, True)
        ctx = await _recall_ctx(monkeypatch, brain, turn, now=SEVEN_PM + 60)

        # The band first, then today's rows, newest first, typed "today".
        assert [f["memory_type"] for f in ctx.relevant_facts] == ["user_fact", "today", "today"]
        assert [f["value"] for f in ctx.relevant_facts] == [
            "favorite_color: blue", "dog_name: Miso", "home_city: Lisbon",
        ]

        out = build_agent_loop_context(turn, ctx)
        assert out.facts_text == (
            "## Things I Know About This User\n"
            "- fact: favorite_color: blue\n"
            "\n"
            f"{TODAY_HEADER}\n"
            f"{TODAY_LEAD}\n"
            "- dog_name: Miso\n"
            "- home_city: Lisbon"
        )

        # The slot off: no today row leaves the fetch — no place, no section.
        _slot(monkeypatch, False)
        ctx_off = await _recall_ctx(monkeypatch, brain, turn, now=SEVEN_PM + 60)
        assert [f["memory_type"] for f in ctx_off.relevant_facts] == ["user_fact"]
        off = build_agent_loop_context(turn, ctx_off)
        assert off.facts_text == "## Things I Know About This User\n- fact: favorite_color: blue"
        # The render's own gate holds too, should a today row ever reach it.
        assert build_agent_loop_context(turn, ctx).facts_text == off.facts_text
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_no_today_rows_leaves_the_prompt_byte_identical(tmp_path, monkeypatch):
    """With nothing in the slot, recall's facts and the rendered band are
    exactly what they were before the slot existed — on or off."""
    from core.agent.context_adapter import build_agent_loop_context

    brain = await _brain(tmp_path)
    try:
        _at(brain, SEVEN_PM - 86400)
        await brain.store_user_memory(user_id=OWNER, memory_type="user_fact", content="favorite_color: blue")
        await brain.store_user_memory(user_id=OWNER, memory_type="moment", content="a heavy day", importance=0.9)
        turn = _owner_turn("hello")
        for on in (False, True):
            _slot(monkeypatch, on)
            ctx = await _recall_ctx(monkeypatch, brain, turn, now=SEVEN_PM)
            assert [f["memory_type"] for f in ctx.relevant_facts] == ["user_fact"]
            assert build_agent_loop_context(turn, ctx).facts_text == (
                "## Things I Know About This User\n- fact: favorite_color: blue"
            )
    finally:
        await brain.close()


# ── recall's fetch: today's rows never take a typed fact's place ────────


async def _fetch(brain: PalliBrain, *, now: float) -> list:
    """recall's own call (core/turn/recall.py ``_w_facts``), on the tmp brain."""
    return await _REAL_FACTS(
        user_id=OWNER, max_results=8, query="how was your evening?",
        exclude_memory_types=("moment",), now=now, brain_client=brain,
    )


def _ids(weighted) -> list:
    return [w.fact["id"] for w in weighted]


async def _seed_typed(brain: PalliBrain, n: int, *, now: float) -> None:
    for i in range(n):
        _at(brain, now - (i + 2) * 86400)
        await brain.store_user_memory(
            user_id=OWNER, memory_type="user_fact", content=f"fact_{i}: {i}",
            importance=0.5 + i * 0.03,
        )


async def _seed_today(brain: PalliBrain, ts: float, content: str) -> int:
    """A today row at ``ts`` written past the store's cap (the read's own
    limits are what is under test)."""
    _at(brain, ts)
    return await brain.store_user_memory(user_id=OWNER, memory_type="today", content=content)


@pytest.mark.asyncio
async def test_todays_rows_take_no_place_in_recalls_eight(tmp_path, monkeypatch):
    """Fix round 1, Important 1: ten typed facts, then eight today rows.
    Off, the fetch is exactly the fetch of the same database before any
    today row existed (no place taken, no id to bump); on, all eight typed
    facts and then the today rows, newest first."""
    now = SEVEN_PM + 60
    brain = await _brain(tmp_path)
    try:
        await _seed_typed(brain, 10, now=now)
        _slot(monkeypatch, True)
        before = _ids(await _fetch(brain, now=now))
        assert len(before) == 8

        today = [await _seed_today(brain, BOUNDARY_13TH + 3600 * (i + 1), f"line_{i}: {i}") for i in range(8)]

        _slot(monkeypatch, False)
        assert _ids(await _fetch(brain, now=now)) == before

        _slot(monkeypatch, True)
        got = await _fetch(brain, now=now)
        assert _ids(got) == before + list(reversed(today))
        assert [w.fact["memory_type"] for w in got[8:]] == ["today"] * 8
        # A caller that leaves today rows out gets none, on or off.
        left_out = await _REAL_FACTS(
            user_id=OWNER, max_results=8, now=now, brain_client=brain,
            exclude_memory_types=("moment", "today"),
        )
        assert _ids(left_out) == before
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_the_fetch_keeps_only_todays_rows_since_the_nights_boundary(tmp_path, monkeypatch):
    """Fix round 1, Minor 3: a row the night failed to retire is not
    "earlier today". Only rows written since the boundary (the night's run
    time, 06:30 here) come back, and only those count against the eight."""
    brain = await _brain(tmp_path)
    try:
        await _seed_typed(brain, 1, now=SEVEN_PM)
        stale = [
            await _seed_today(brain, datetime(2026, 10, 12, 22, 0).timestamp(), "stale_late: 1"),
            await _seed_today(brain, datetime(2026, 10, 13, 6, 0).timestamp(), "stale_dawn: 2"),
        ]
        fresh = [
            await _seed_today(brain, BOUNDARY_13TH + 60 * (i + 1), f"fresh_{i}: {i}") for i in range(9)
        ]
        _slot(monkeypatch, True)

        got = await _fetch(brain, now=SEVEN_PM + 60)
        today_ids = [w.fact["id"] for w in got if w.fact["memory_type"] == "today"]
        assert today_ids == list(reversed(fresh))[:8]  # the newest eight fresh rows
        assert not set(stale) & set(today_ids)

        # 03:00 on the 14th is still the 13th's night: its rows still count,
        # the 12th's and the pre-dawn 13th's still do not.
        late = await _fetch(brain, now=datetime(2026, 10, 14, 3, 0).timestamp())
        late_ids = [w.fact["id"] for w in late if w.fact["memory_type"] == "today"]
        assert late_ids == list(reversed(fresh))[:8]

        # Past the next boundary (07:00 on the 14th) none of them is today's.
        tomorrow = await _fetch(brain, now=datetime(2026, 10, 14, 7, 0).timestamp())
        assert [w for w in tomorrow if w.fact["memory_type"] == "today"] == []
    finally:
        await brain.close()


def test_split_today_and_plan_band_put_todays_rows_after_the_band():
    from core.turn.memory_lookup import plan_band, split_today

    facts = [
        _fact("dog_name: Miso", fact_id=11, memory_type="today"),
        _fact("favorite_color: blue", fact_id=3),
        _fact("home_city: Lisbon", fact_id=12, memory_type="today"),
        _fact("a moment", fact_id=4, memory_type="moment"),
    ]
    band, today = split_today(facts)
    assert [f["id"] for f in band] == [3, 4]
    assert [f["id"] for f in today] == [12, 11]  # newest first

    planned, ids = plan_band(
        _owner_turn(), semantic_results=[], min_sim=0.5, compiled_identity=None, facts=facts,
    )
    assert ids == ()
    assert [(f["id"], f["memory_type"]) for f in planned] == [(3, "user_fact"), (12, "today"), (11, "today")]

    # No today rows: plan_band's answer is exactly the band.
    plain = [_fact("favorite_color: blue", fact_id=3)]
    assert plan_band(
        _owner_turn(), semantic_results=[], min_sim=0.5, compiled_identity=None, facts=plain,
    ) == (plain, ())


def test_render_facts_pinned_header_first_then_today(monkeypatch):
    from core.agent.context_adapter import _render_facts

    turn = _owner_turn()
    _slot(monkeypatch, True)
    band = tuple(_fact(f"k{i}: v{i}", fact_id=i) for i in range(1, 10))  # nine: the band shows eight
    today = (_fact("dog_name: Miso", fact_id=20, memory_type="today"),)
    rendered = _render_facts(TurnContext(turn=turn, relevant_facts=band + today))
    lines = rendered.split("\n")
    assert lines[0] == "## Things I Know About This User"
    assert lines[1:9] == [f"- fact: k{i}: v{i}" for i in range(1, 9)]
    assert lines[9:] == ["", TODAY_HEADER, TODAY_LEAD, "- dog_name: Miso"]

    # Only today's rows: the today section alone, no empty pinned header.
    assert _render_facts(TurnContext(turn=turn, relevant_facts=today)) == (
        f"{TODAY_HEADER}\n{TODAY_LEAD}\n- dog_name: Miso"
    )
    # A today row with no text renders nothing.
    empty = (_fact("", fact_id=21, memory_type="today"),)
    assert _render_facts(TurnContext(turn=turn, relevant_facts=empty)) == ""

    # The slot off: today's rows render nowhere.
    _slot(monkeypatch, False)
    assert _render_facts(TurnContext(turn=turn, relevant_facts=today)) == ""
    assert TODAY_HEADER not in _render_facts(TurnContext(turn=turn, relevant_facts=band + today))


# ── a guest's turn: never ────────────────────────────────────────────────


def test_a_guests_turn_never_gets_todays_facts(monkeypatch):
    from core.agent.context_adapter import build_agent_loop_context
    from core.turn.memory_lookup import plan_band

    _slot(monkeypatch, True)
    guest = _guest_turn()
    facts = [_fact("favorite_color: blue", fact_id=3), _fact("dog_name: Miso", fact_id=11, memory_type="today")]

    planned, _ = plan_band(guest, semantic_results=[], min_sim=0.5, compiled_identity=None, facts=facts)
    assert all(f["memory_type"] != "today" for f in planned)

    ctx = TurnContext(turn=guest, relevant_facts=tuple(facts))
    out = build_agent_loop_context(guest, ctx, is_owner=False)
    assert out.facts_text == ""
    rendered = [v for v in vars(out).values() if isinstance(v, str)]
    assert not any("Miso" in v or TODAY_HEADER in v for v in rendered)


@pytest.mark.asyncio
async def test_a_guests_turn_runs_no_reflection_with_the_slot_on(monkeypatch):
    _slot(monkeypatch, True)
    captured = {}

    async def fake_run_turn(turn, *, deps, on_chunk=None):
        captured["deps"] = deps
        from core.agent.runner import RunResult

        return RunResult(responded=True, response_text="hi", iterations=1)

    monkeypatch.setattr("core.agent.runner.run_turn", fake_run_turn)

    async def stub_cadence_llm(prompt):
        return ""

    async def stub_build_callable():
        return stub_cadence_llm

    async def stub_client():
        return object()

    async def noop_user(turn, content):
        pass

    async def noop_asst(*, turn, response_text):
        pass

    async def fake_refs():
        return {"app_user_id": 42, "palli_user_id": 42, "discord_user_id": 777}

    monkeypatch.setattr("core.agent.dispatch._build_cadence_llm_callable", stub_build_callable)
    monkeypatch.setattr("core.agent.dispatch._get_openai_compat_client", stub_client)
    monkeypatch.setattr("core.turn.persistence._persist_user_side_with_content", noop_user)
    monkeypatch.setattr("core.turn.persistence._persist_assistant_side_to_history", noop_asst)
    monkeypatch.setattr("core.local_mode.get_local_owner_refs", fake_refs)
    monkeypatch.setenv("PALLI_AGENT_LOOP", "react")

    from core.agent.dispatch import dispatch_turn

    await dispatch_turn(_guest_turn())
    assert captured["deps"].is_owner is False
    assert captured["deps"].apply_reflection is None


# ── the write: the after-reply reflection ────────────────────────────────


def _ctx_for_owner(*, turn_read=None):
    return SimpleNamespace(
        turn=SimpleNamespace(owner_id=OWNER, surface="local", content="", metadata=SimpleNamespace()),
        turn_read=turn_read,
    )


async def _apply_facts(monkeypatch, brain: PalliBrain, facts, *, fail_key: str = "") -> list:
    import core.turn.reflect as reflect

    calls: list = []

    async def maintain(**kwargs):
        calls.append(kwargs)
        if fail_key and f"pref_{fail_key}" in kwargs["updates"]:
            raise RuntimeError("identity store down")

    monkeypatch.setattr(reflect, "brain", brain)
    monkeypatch.setattr(reflect.identity_service, "silently_maintain_document", maintain)
    await reflect._apply_user_facts(owner_id=OWNER, facts=facts)
    return calls


@pytest.mark.asyncio
async def test_apply_user_facts_keeps_each_written_fact_in_todays_slot(tmp_path, monkeypatch):
    from core.turn.types import ReflectionFactAdd

    brain = await _brain(tmp_path)
    try:
        _slot(monkeypatch, True)
        facts = [
            ReflectionFactAdd(key="pref_dog_name", value="Miso", confidence=0.9),
            ReflectionFactAdd(key="home_city", value="Lisbon", confidence=0.9),
        ]
        await _apply_facts(monkeypatch, brain, facts, fail_key="home_city")
        rows = _today_rows(await _rows(brain))
        # The failed preferences write keeps nothing; the written one is kept.
        assert [(r["content"], r["source_detail"]) for r in rows] == [
            ("dog_name: Miso", {"source": "reflection"}),
        ]
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_with_the_switch_off_apply_user_facts_writes_no_today_row(tmp_path, monkeypatch):
    from core.turn.types import ReflectionFactAdd

    facts = [ReflectionFactAdd(key="dog_name", value="Miso", confidence=0.9)]
    brain_on, brain_off = await _brain(tmp_path), await _brain(tmp_path)
    try:
        _slot(monkeypatch, True)
        calls_on = await _apply_facts(monkeypatch, brain_on, facts)
        _slot(monkeypatch, False)
        calls_off = await _apply_facts(monkeypatch, brain_off, facts)
        # The preferences writes are the same either way …
        assert calls_off == calls_on
        # … and with the switch off the slot is never written.
        assert await _rows(brain_off) == []
        assert len(_today_rows(await _rows(brain_on))) == 1
    finally:
        await brain_on.close()
        await brain_off.close()


@pytest.mark.asyncio
async def test_memory_worthy_lands_in_todays_slot_at_any_weight(tmp_path, monkeypatch):
    import core.turn.reflect as reflect

    brain = await _brain(tmp_path)
    monkeypatch.setattr(reflect, "brain", brain)
    monkeypatch.setattr("ai.turn_read.record_timeline_entry", AsyncMock())
    monkeypatch.setattr(
        companion_flags, "companion_settings",
        lambda: SimpleNamespace(caring_memory=True, caring_memory_min_weight=0.15),
    )
    light =SimpleNamespace(memory_worthy="They start the new job on Monday.", weight=0.2)
    try:
        _slot(monkeypatch, False)
        await reflect._persist_caring_memory(ctx=_ctx_for_owner(turn_read=light), owner_id=OWNER)
        assert await _rows(brain) == []

        _slot(monkeypatch, True)
        await reflect._persist_caring_memory(ctx=_ctx_for_owner(turn_read=light), owner_id=OWNER)
        rows = await _rows(brain)
        # Below the moment's 0.4 floor: no moment row, but today's slot keeps it.
        assert [(r["memory_type"], r["content"], r["source_detail"]) for r in rows] == [
            ("today", "They start the new job on Monday.", {"source": "memory_worthy"}),
        ]
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_apply_reflection_with_a_faked_model_output_fills_the_slot(tmp_path, monkeypatch):
    """The reflection's model output, faked as the JSON its parser reads:
    one durable fact → the preferences write and, with the switch on, a
    today row — at once, not after the night."""
    import core.turn.reflect as reflect

    model_output = json.dumps({
        "user_facts_add": [{"key": "dog_name", "value": "Miso", "confidence": 0.9}],
    })
    brain = await _brain(tmp_path)
    monkeypatch.setattr(reflect, "brain", brain)
    monkeypatch.setattr(reflect.identity_service, "silently_maintain_document", AsyncMock())

    async def fake_extract(ctx, response_text):
        return reflect._plan_from_payload(reflect._parse_json_payload(model_output))

    monkeypatch.setattr(reflect, "_extract_plan", fake_extract)
    try:
        _slot(monkeypatch, False)
        await reflect.apply_reflection(_ctx_for_owner(), response_text="Miso is a great name.")
        assert _today_rows(await _rows(brain)) == []

        _slot(monkeypatch, True)
        await reflect.apply_reflection(_ctx_for_owner(), response_text="Miso is a great name.")
        assert [r["content"] for r in _today_rows(await _rows(brain))] == ["dog_name: Miso"]
    finally:
        await brain.close()


# ── the night: retired once filed ────────────────────────────────────────


def _night(monkeypatch, *, reconcile=None):
    import ai.consolidation as c
    from ai.consolidation import AgingResult, DayResult, ReconcileResult

    monkeypatch.setattr(c, "consolidation_enabled", c.consolidation_enabled_impl)
    monkeypatch.setattr(c, "companion_settings", lambda: SimpleNamespace(consolidation=True))

    async def fake_day(**k):
        return DayResult(day=k["day"], wrote=True)

    async def fake_age(**k):
        return AgingResult()

    async def fake_rec(**k):
        if reconcile is not None:
            await reconcile()
        return ReconcileResult()

    monkeypatch.setattr(c, "consolidate_day", fake_day)
    monkeypatch.setattr(c, "age_facts", fake_age)
    monkeypatch.setattr(c, "reconcile_day", fake_rec)
    return c


@pytest.mark.asyncio
async def test_the_night_retires_todays_rows_and_counts_them(tmp_path, monkeypatch):
    import core.database as database

    brain = await _brain(tmp_path)
    monkeypatch.setattr(database, "brain", brain)
    c = _night(monkeypatch)
    try:
        now = time.time()
        _at(brain, now - 4 * 3600)
        a = await brain.note_today_fact(user_id=OWNER, content="dog_name: Miso", source="reflection")
        b = await brain.note_today_fact(user_id=OWNER, content="home_city: Lisbon", source="memory_worthy")
        fact = await brain.store_user_memory(user_id=OWNER, memory_type="user_fact", content="favorite_color: blue")

        # The switch off: the night retires nothing and says nothing of it.
        out_off = await c.run_nightly_consolidation(owner_id=OWNER, day="2026-10-13")
        assert "today_retired" not in out_off
        assert len(await brain.active_today_facts(user_id=OWNER)) == 2

        _slot(monkeypatch, True)
        out = await c.run_nightly_consolidation(owner_id=OWNER, day="2026-10-13")
        assert out["today_retired"] == 2
        by_id = {r["id"]: r["status"] for r in await _rows(brain)}
        assert by_id == {a: "retired", b: "retired", fact: "active"}
        assert await brain.active_today_facts(user_id=OWNER) == []
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_no_today_row_is_offered_to_the_reconcile(tmp_path):
    """Fix round 1, Important 2: the reconcile's fifteen places go to the
    durable facts it exists to check. A today row comes from the very day
    under review (it would win the overlap ranking), and a correction to
    one would be retired moments later, so none is ever offered."""
    from ai.consolidation import reconcile_day

    brain = await _brain(tmp_path)
    try:
        cid = await brain.store_conversation(
            user_id=OWNER, platform="dashboard", channel_id=1, thread_id=None,
            role="user", content="Miso the dog chewed the map again", username="Robin",
        )
        conn = await brain.get_connection()
        try:
            await conn.execute(
                "UPDATE conversations SET timestamp=$1 WHERE id=$2",
                datetime(2026, 10, 13, 12, 0).timestamp(), cid,
            )
        finally:
            await conn.close()
        typed = await brain.store_user_memory(
            user_id=OWNER, memory_type="user_fact", content="The dog is called Rex",
        )
        await brain.note_today_fact(user_id=OWNER, content="dog_name: Miso", source="reflection")
        await brain.note_today_fact(
            user_id=OWNER, content="Miso chewed the map again", source="memory_worthy",
        )

        prompts: list[str] = []

        async def llm(prompt):
            prompts.append(prompt)
            return '{"contradictions": []}'

        result = await reconcile_day(
            owner_id=OWNER, day="2026-10-13", llm_call=llm, max_updates=3, brain_client=brain,
        )
        assert result.checked == 1
        assert len(prompts) == 1
        assert f"{typed}: The dog is called Rex" in prompts[0]
        assert "dog_name: Miso" not in prompts[0]
        assert "Miso chewed the map again" not in prompts[0]
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_the_nights_cutoff_is_the_start_of_its_pass(tmp_path, monkeypatch):
    """The retire takes the rows written before the pass began; a line kept
    while the pass runs belongs to the new day and stays."""
    import core.database as database

    brain = await _brain(tmp_path)
    monkeypatch.setattr(database, "brain", brain)
    try:
        _at(brain, time.time() - 3600)
        earlier = await brain.note_today_fact(user_id=OWNER, content="dog_name: Miso", source="reflection")
        brain._memory_store._now = time.time
        during: list[int] = []

        async def a_line_kept_mid_pass():
            during.append(
                await brain.note_today_fact(user_id=OWNER, content="home_city: Lisbon", source="reflection")
            )

        c = _night(monkeypatch, reconcile=a_line_kept_mid_pass)
        _slot(monkeypatch, True)
        out = await c.run_nightly_consolidation(owner_id=OWNER, day="2026-10-13")
        assert out["today_retired"] == 1
        by_id = {r["id"]: r["status"] for r in _today_rows(await _rows(brain), status=None)}
        assert by_id == {earlier: "retired", during[0]: "active"}
    finally:
        await brain.close()


def test_a_failed_retire_or_prune_names_itself_in_the_nights_health_line():
    """Fix round 1, Minor 3: a failed retire is no longer silent — the
    night's health line names it (and the housekeeping prune's, Minor 4)."""
    from ai.night_cycle import _surface_errors

    assert _surface_errors({"consolidation": {"today_retired": {"error": "database is locked"}}}) == (
        "today_retired: database is locked"
    )
    assert _surface_errors({"today_prune": {"error": "disk I/O error"}}) == "today_prune: disk I/O error"
    # A count is a healthy night.
    assert _surface_errors({"consolidation": {"today_retired": 3}, "today_prune": {"removed": 2}}) == ""


@pytest.mark.asyncio
async def test_a_failed_retire_never_costs_the_night(tmp_path, monkeypatch):
    import core.database as database

    class _Down:
        async def retire_today_facts(self, **kwargs):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(database, "brain", _Down())
    c = _night(monkeypatch)
    _slot(monkeypatch, True)
    out = await c.run_nightly_consolidation(owner_id=OWNER, day="2026-10-13")
    assert out["day_gist"]["wrote"] is True
    assert out["today_retired"] == {"error": "database is locked"}


# ── the housekeeping: retired rows go after three days ───────────────────


@pytest.mark.asyncio
async def test_the_night_prunes_retired_today_rows_after_three_days(tmp_path, monkeypatch):
    """Fix round 1, Minor 4: a retired today row is kept three days (the
    night's record and a look back can still find it), then removed by the
    night's housekeeping. Active today rows, other types and the young
    retired rows stay. Off: no step, no key, nothing removed."""
    import core.database as database
    import core.retention as retention

    now = time.time()
    brain = await _brain(tmp_path)
    monkeypatch.setattr(database, "brain", brain)
    try:
        _at(brain, now - 4 * 86400)
        old_retired = await brain.note_today_fact(user_id=OWNER, content="old: 1", source="reflection")
        other_user = await brain.note_today_fact(user_id=OTHER, content="theirs: 1", source="reflection")
        old_active = await brain.note_today_fact(user_id=OWNER, content="never retired: 1", source="reflection")
        old_fact = await brain.store_user_memory(user_id=OWNER, memory_type="user_fact", content="favorite_color: blue")
        _at(brain, now - 2 * 86400)
        young_retired = await brain.note_today_fact(user_id=OWNER, content="young: 2", source="reflection")
        conn = await brain.get_connection()
        try:
            for row_id in (old_retired, other_user, young_retired):
                await conn.execute("UPDATE user_memory SET status='retired' WHERE id=$1", row_id)
        finally:
            await conn.close()

        out_off = await retention.run_nightly_housekeeping(now=now, settings=SimpleNamespace())
        assert "today_prune" not in out_off
        assert len(await _rows(brain)) == 4 and len(await _rows(brain, OTHER)) == 1

        _slot(monkeypatch, True)
        out = await retention.run_nightly_housekeeping(now=now, settings=SimpleNamespace())
        assert out["today_prune"] == {"removed": 2}  # Robin's old one and the other user's
        left = {r["id"]: (r["memory_type"], r["status"]) for r in await _rows(brain)}
        assert left == {
            old_active: ("today", "active"),
            old_fact: ("user_fact", "active"),
            young_retired: ("today", "retired"),
        }
        assert old_retired not in left
        assert await _rows(brain, OTHER) == [] and other_user
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_a_broken_prune_is_an_error_in_the_record_never_a_raise(monkeypatch):
    import core.database as database
    import core.retention as retention

    class _Down:
        async def prune_retired_today_facts(self, **kwargs):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(database, "brain", _Down())
    _slot(monkeypatch, True)
    out = await retention.run_nightly_housekeeping(now=time.time(), settings=SimpleNamespace())
    assert out["today_prune"] == {"error": "database is locked"}
    assert {"history_prune", "retention"} <= set(out)
