"""Consolidation & gist-aging v1 (Wave 1a, 2026-07-15) — shared module."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai.consolidation import consolidation_enabled


def test_gate_off_when_unbooted(monkeypatch):
    import ai.consolidation as c
    monkeypatch.setattr(c, "companion_settings", lambda: None)
    assert consolidation_enabled() is False


def test_gate_reads_flag(monkeypatch):
    import ai.consolidation as c
    monkeypatch.setattr(c, "consolidation_enabled", c.consolidation_enabled_impl)
    monkeypatch.setattr(c, "companion_settings", lambda: SimpleNamespace(consolidation=True))
    assert consolidation_enabled() is True
    monkeypatch.setattr(c, "companion_settings", lambda: SimpleNamespace(consolidation=False))
    assert consolidation_enabled() is False


def test_schema_default_is_off():
    from core.settings_schema import CompanionConfig
    assert CompanionConfig().consolidation is False


# ── consolidate_day ──────────────────────────────────────────────────────

import time as _time
import uuid
from datetime import datetime
from pathlib import Path

from core.database import PalliBrain

from ai.consolidation import consolidate_day


def _tmp_brain(tmp_path):
    return PalliBrain(str(tmp_path / f"consol_{uuid.uuid4().hex}.sqlite3"))


class _Collect:
    def __init__(self):
        self.writes = []
        self.indexed = []

    def store(self, *, day, text, meta):
        self.writes.append({"day": day, "text": text, "meta": meta})
        return Path(f"/dev/null/{day}.jsonl")

    async def index(self, *, day, text, source_path=""):
        self.indexed.append(day)


async def _seed_convo(brain, *, content, ts, platform="dashboard", role="user"):
    cid = await brain.store_conversation(
        user_id=1, platform=platform, channel_id=1, thread_id=None,
        role=role, content=content, username="Sam" if role == "user" else "Me")
    conn = await brain.get_connection()
    try:
        await conn.execute("UPDATE conversations SET timestamp=$1 WHERE id=$2", ts, cid)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_consolidate_day_writes_one_gist_keyed_by_its_day(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    col = _Collect()
    prompts = []
    day0 = datetime(2026, 5, 2, 0, 0, 0).timestamp()
    try:
        await _seed_convo(brain, content="the pergola estimate came back", ts=day0 + 9 * 3600)
        await _seed_convo(brain, content="reminder machinery", ts=day0 + 10 * 3600, platform="reminder_fire", role="assistant")

        async def llm(prompt):
            prompts.append(prompt)
            return "Early May: the pergola estimate finally landed, and he was lighter for it."

        r = await consolidate_day(owner_id=1, day="2026-05-02", llm_call=llm,
                                  now=_time.time(), brain_client=brain,
                                  store_write=col.store, index_write=col.index)
        assert r.wrote and not r.skipped and not r.error
        assert col.writes[0]["day"] == "2026-05-02"          # keyed by ITS day
        assert col.indexed == ["2026-05-02"]
        assert "pergola estimate" in prompts[0]
        assert "reminder machinery" not in prompts[0]         # machinery excluded
        assert "2026-05-02" in prompts[0]                     # the day named in the prompt
        assert col.writes[0]["meta"]["sources"]["conversations"] > 0
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_empty_day_writes_nothing_and_calls_no_llm(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    col = _Collect()
    try:
        async def llm(prompt):
            raise AssertionError("no material => no LLM call")
        r = await consolidate_day(owner_id=1, day="2026-05-03", llm_call=llm,
                                  now=_time.time(), brain_client=brain,
                                  store_write=col.store, index_write=col.index)
        assert r.skipped and r.reason == "no_material"
        assert col.writes == [] and col.indexed == []
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_consolidate_day_refuses_today_and_future(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    try:
        import datetime as dt
        today = dt.date.today().isoformat()
        r = await consolidate_day(owner_id=1, day=today, llm_call=None,
                                  now=_time.time(), brain_client=brain,
                                  store_write=None, index_write=None)
        assert r.skipped and r.reason == "day_not_finished"
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_consolidate_day_caps_material_and_clips_gist(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    col = _Collect()
    day0 = datetime(2026, 5, 4, 0, 0, 0).timestamp()
    try:
        for i in range(80):
            await _seed_convo(brain, content=("chatter %d " % i) + "x" * 300, ts=day0 + 3600 + i * 60)

        async def llm(prompt):
            assert len(prompt) < 16000  # caps hold even on a heavy day
            return "g" * 2000

        r = await consolidate_day(owner_id=1, day="2026-05-04", llm_call=llm,
                                  now=_time.time(), brain_client=brain,
                                  store_write=col.store, index_write=col.index)
        assert r.wrote
        assert len(col.writes[0]["text"]) <= 600  # GIST_MAX_CHARS
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_consolidate_day_never_raises_on_llm_failure(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    col = _Collect()
    day0 = datetime(2026, 5, 5, 0, 0, 0).timestamp()
    try:
        await _seed_convo(brain, content="one real line", ts=day0 + 3600)

        async def boom(prompt):
            raise RuntimeError("llm down")

        r = await consolidate_day(owner_id=1, day="2026-05-05", llm_call=boom,
                                  now=_time.time(), brain_client=brain,
                                  store_write=col.store, index_write=col.index)
        assert r.error and col.writes == []
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_consolidate_day_includes_reflection_and_day_log_context(tmp_path, monkeypatch):
    import ai.night_cycle_store as ncs
    import core.memory_service as ms
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    col = _Collect()
    prompts = []
    monkeypatch.setattr(ncs, "NIGHT_CYCLE_DIR", tmp_path / "night")
    ncs.write_daily_meaning(date="2026-05-06", text="that night I wrote about the estimate landing")

    async def fake_get_day(*, owner_user_id, day):
        return {"entries": [{"summary": "estimate day"}],
                "markdown": "## 2026-05-06\n- Summary: the estimate landed"}

    monkeypatch.setattr(ms.memory_workspace_service, "get_day", fake_get_day)
    try:
        async def llm(prompt):
            prompts.append(prompt)
            return "a small gist"

        r = await consolidate_day(owner_id=1, day="2026-05-06", llm_call=llm,
                                  now=_time.time(), brain_client=brain,
                                  store_write=col.store, index_write=col.index)
        assert r.wrote
        assert "the estimate landed" in prompts[0]             # day-log present
        assert "about the estimate landing" in prompts[0]      # reflection as context
        assert "context, not content" in prompts[0]            # labeled as such
    finally:
        await brain.close()


# ── age_facts ────────────────────────────────────────────────────────────

from ai.consolidation import age_facts


async def _seed_fact(brain, *, content, memory_type="moment", age_days=60.0, importance=0.7):
    fid = await brain.store_user_memory(user_id=1, memory_type=memory_type, content=content, importance=importance)
    conn = await brain.get_connection()
    try:
        await conn.execute("UPDATE user_memory SET created_at=$1 WHERE id=$2",
                           _time.time() - age_days * 86400.0, fid)
    finally:
        await conn.close()
    return fid


@pytest.mark.asyncio
async def test_age_facts_allowlist_threshold_order_and_mechanics(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    try:
        oldest = await _seed_fact(brain, content="2026-04-30: first sea-trial notes, three pages", age_days=76)
        old = await _seed_fact(brain, content="2026-05-20: pergola quotes compared", age_days=56)
        fresh = await _seed_fact(brain, content="yesterday: quick chart check", age_days=1)
        durable = await _seed_fact(brain, content="Timezone: ET", memory_type="preference", age_days=76)

        seen = []
        async def llm(prompt):
            seen.append(prompt)
            return "a distanced gist of it, I think"

        r = await age_facts(owner_id=1, now=_time.time(), llm_call=llm, max_rewrites=10, brain_client=brain)
        assert r.candidates == 2 and r.rewritten == 2 and not r.error
        assert [p[0] for p in r.pairs] == [oldest, old]  # oldest first
        from ai.fact_retrieval import get_user_facts_raw
        rows = {f["id"]: f for f in await get_user_facts_raw(user_id=1, brain_client=brain)}
        assert rows[fresh]["status"] == "active"
        assert rows[durable]["status"] == "active"        # durable kinds NEVER age in v1
        assert rows[oldest]["status"] == "superseded"
        assert rows[oldest]["content"].startswith("2026-04-30")  # untouched
        new_ids = [p[1] for p in r.pairs]
        assert all(rows[nid]["status"] == "active" for nid in new_ids)
        assert rows[new_ids[0]]["source_detail"]["consolidated_from"] == oldest
        # Review fix: aging fades, never rejuvenates — the successor carries
        # the ORIGINAL's created_at (decay continuity + honest FACT_DATE).
        assert abs(rows[new_ids[0]]["created_at"] - rows[oldest]["created_at"]) < 1.0
        # re-run: nothing left to age (idempotent)
        r2 = await age_facts(owner_id=1, now=_time.time(), llm_call=llm, max_rewrites=10, brain_client=brain)
        assert r2.candidates == 0
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_age_facts_cap_and_empty_gist_skip(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    try:
        for i in range(4):
            await _seed_fact(brain, content=f"moment {i}", age_days=90 - i)

        calls = []
        async def llm(prompt):
            calls.append(prompt)
            return "" if len(calls) == 1 else "gist"

        r = await age_facts(owner_id=1, now=_time.time(), llm_call=llm, max_rewrites=2, brain_client=brain)
        assert r.candidates == 2          # capped, oldest-first
        assert r.rewritten == 1 and r.skipped == 1   # empty gist skipped, no write
        assert len(r.proposals) == 2      # dry-run material regardless of writes
    finally:
        await brain.close()


# ── reconcile_day ────────────────────────────────────────────────────────

from ai.consolidation import reconcile_day


@pytest.mark.asyncio
async def test_reconcile_supersedes_contradicted_fact_with_evidence(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    day0 = datetime(2026, 7, 10, 0, 0, 0).timestamp()
    try:
        fid = await _seed_fact(brain, content="The insulation delivery is planned for August", age_days=20)
        await _seed_convo(brain, content="insulation delivery got moved to September, the supplier is slammed", ts=day0 + 3600)

        async def llm(prompt):
            assert "insulation delivery" in prompt.lower()
            return ('{"contradictions": [{"fact_id": %d, '
                    '"corrected_content": "The insulation delivery moved to September (supplier backlog).", '
                    '"evidence": "he said it was moved to September"}]}' % fid)

        r = await reconcile_day(owner_id=1, day="2026-07-10", llm_call=llm, max_updates=3, brain_client=brain)
        assert len(r.corrections) == 1 and not r.error
        from ai.fact_retrieval import get_user_facts_raw
        rows = {f["id"]: f for f in await get_user_facts_raw(user_id=1, brain_client=brain)}
        assert rows[fid]["status"] == "superseded"
        assert rows[fid]["content"] == "The insulation delivery is planned for August"  # untouched
        new_id = r.corrections[0][1]
        assert "September" in rows[new_id]["content"]
        assert "2026-07-10" in rows[new_id]["source_detail"]["correction_evidence"]
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_reconcile_uncertain_or_garbage_means_no_writes(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    day0 = datetime(2026, 7, 10, 0, 0, 0).timestamp()
    try:
        fid = await _seed_fact(brain, content="The insulation delivery is planned for August", age_days=20)
        await _seed_convo(brain, content="thinking about the insulation delivery again", ts=day0 + 3600)

        async def empty(prompt):
            return '{"contradictions": []}'
        r = await reconcile_day(owner_id=1, day="2026-07-10", llm_call=empty, max_updates=3, brain_client=brain)
        assert r.corrections == [] and not r.error

        async def garbage(prompt):
            return "well, hard to say really"
        r2 = await reconcile_day(owner_id=1, day="2026-07-10", llm_call=garbage, max_updates=3, brain_client=brain)
        assert r2.corrections == [] and r2.error  # parse failure => conservative no-op, noted

        async def foreign_id(prompt):
            return '{"contradictions": [{"fact_id": 424242, "corrected_content": "x", "evidence": "y"}]}'
        r3 = await reconcile_day(owner_id=1, day="2026-07-10", llm_call=foreign_id, max_updates=3, brain_client=brain)
        assert r3.corrections == []  # ids not offered to the model are rejected

        from ai.fact_retrieval import get_user_facts_raw
        rows = {f["id"]: f for f in await get_user_facts_raw(user_id=1, brain_client=brain)}
        assert rows[fid]["status"] == "active"
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_reconcile_skips_when_no_material_or_no_overlap(tmp_path):
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    try:
        await _seed_fact(brain, content="The insulation delivery is planned for August", age_days=20)

        async def llm(prompt):
            raise AssertionError("no material => no LLM")
        r = await reconcile_day(owner_id=1, day="2026-07-11", llm_call=llm, max_updates=3, brain_client=brain)
        assert r.skipped
    finally:
        await brain.close()


# ── nightly composer ─────────────────────────────────────────────────────

from ai.consolidation import run_nightly_consolidation


def test_nightly_flag_off_touches_nothing(monkeypatch):
    import ai.consolidation as c
    monkeypatch.setattr(c, "companion_settings", lambda: SimpleNamespace(consolidation=False))
    async def boom(**k):
        raise AssertionError("flag off => no step runs")
    monkeypatch.setattr(c, "consolidate_day", boom)
    monkeypatch.setattr(c, "age_facts", boom)
    monkeypatch.setattr(c, "reconcile_day", boom)
    out = asyncio.run(run_nightly_consolidation(owner_id=1, day="2026-07-14"))
    assert out == {"skipped": "flag_off"}


def test_nightly_flag_on_runs_three_steps_with_budgets(monkeypatch):
    import ai.consolidation as c
    from ai.consolidation import AgingResult, DayResult, ReconcileResult
    monkeypatch.setattr(c, "consolidation_enabled", c.consolidation_enabled_impl)
    monkeypatch.setattr(c, "companion_settings", lambda: SimpleNamespace(consolidation=True))
    calls = []
    async def fake_day(**k):
        calls.append(("day", k["day"]))
        return DayResult(day=k["day"], wrote=True)
    async def fake_age(**k):
        calls.append(("age", k["max_rewrites"]))
        return AgingResult()
    async def fake_rec(**k):
        calls.append(("rec", k["day"], k["max_updates"]))
        return ReconcileResult()
    monkeypatch.setattr(c, "consolidate_day", fake_day)
    monkeypatch.setattr(c, "age_facts", fake_age)
    monkeypatch.setattr(c, "reconcile_day", fake_rec)
    out = asyncio.run(run_nightly_consolidation(owner_id=1, day="2026-07-14"))
    assert calls == [("day", "2026-07-14"), ("age", 10), ("rec", "2026-07-14", 3)]
    assert out["day_gist"]["wrote"] is True


def test_nightly_step_failure_does_not_stop_the_pass(monkeypatch):
    import ai.consolidation as c
    from ai.consolidation import AgingResult, ReconcileResult
    monkeypatch.setattr(c, "consolidation_enabled", c.consolidation_enabled_impl)
    monkeypatch.setattr(c, "companion_settings", lambda: SimpleNamespace(consolidation=True))
    async def boom(**k):
        raise RuntimeError("store down")
    async def fake_age(**k):
        return AgingResult(rewritten=1)
    async def fake_rec(**k):
        return ReconcileResult()
    monkeypatch.setattr(c, "consolidate_day", boom)
    monkeypatch.setattr(c, "age_facts", fake_age)
    monkeypatch.setattr(c, "reconcile_day", fake_rec)
    out = asyncio.run(run_nightly_consolidation(owner_id=1, day="2026-07-14"))
    assert "error" in out["day_gist"] and out["aging"]["rewritten"] == 1


@pytest.mark.asyncio
async def test_consolidate_day_default_index_writes_to_injected_brain(tmp_path, monkeypatch):
    """Review fix: the default index_write must honor brain_client — a
    sandboxed call must never write FTS rows into the live global brain."""
    import ai.day_gist_store as dgs
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    monkeypatch.setattr(dgs, "DAY_GIST_DIR", tmp_path / "gists")
    day0 = datetime(2026, 5, 7, 0, 0, 0).timestamp()
    try:
        await _seed_convo(brain, content="a real line", ts=day0 + 3600)

        async def llm(prompt):
            return "a small gist"

        r = await consolidate_day(owner_id=1, day="2026-05-07", llm_call=llm,
                                  now=_time.time(), brain_client=brain)
        assert r.wrote
        conn = await brain.get_connection()
        try:
            rows = await conn.fetch(
                "SELECT entry_id FROM monologue_memory_fts WHERE entry_id = $1",
                "day_gist:2026-05-07")
        finally:
            await conn.close()
        assert len(rows) == 1  # landed in the INJECTED brain
    finally:
        await brain.close()


def test_nightly_triggers_index_rebuild_only_when_facts_changed(monkeypatch):
    """Review fix: superseding facts left the workspace index stale until a
    TTL rebuild — the nightly pass now rebuilds when it changed facts."""
    from unittest.mock import AsyncMock

    import core.memory_service as ms
    import ai.consolidation as c
    from ai.consolidation import AgingResult, DayResult, ReconcileResult
    monkeypatch.setattr(c, "consolidation_enabled", c.consolidation_enabled_impl)
    monkeypatch.setattr(c, "companion_settings", lambda: SimpleNamespace(consolidation=True))

    async def fake_day(**k):
        return DayResult(day=k["day"], wrote=True)
    monkeypatch.setattr(c, "consolidate_day", fake_day)
    rebuild = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(ms.memory_workspace_service, "rebuild_index", rebuild)

    async def aged(**k):
        return AgingResult(rewritten=2, pairs=[(1, 9), (2, 10)])
    async def none_aged(**k):
        return AgingResult()
    async def no_rec(**k):
        return ReconcileResult()
    monkeypatch.setattr(c, "age_facts", aged)
    monkeypatch.setattr(c, "reconcile_day", no_rec)
    out = asyncio.run(c.run_nightly_consolidation(owner_id=1, day="2026-07-14"))
    rebuild.assert_awaited_once_with(owner_user_id=1)
    assert out["index_rebuilt"] is True

    rebuild.reset_mock()
    monkeypatch.setattr(c, "age_facts", none_aged)
    out2 = asyncio.run(c.run_nightly_consolidation(owner_id=1, day="2026-07-14"))
    rebuild.assert_not_awaited()
    assert out2.get("index_rebuilt") is False


@pytest.mark.asyncio
async def test_reconcile_prefilter_sees_short_and_possessive_tokens(tmp_path):
    """Review fix: the prefilter now shares fact_retrieval's tokenizer —
    'AC'/'ET'/possessives are visible, so facts retrieval keeps surfacing
    can actually be corrected (17/84 live facts were invisible before)."""
    brain = _tmp_brain(tmp_path)
    await brain.initialize()
    day0 = datetime(2026, 7, 10, 0, 0, 0).timestamp()
    try:
        fid = await _seed_fact(brain, content="User's AC unit is broken", age_days=10)
        await _seed_convo(brain, content="we finally fixed the AC today, it blows cold now", ts=day0 + 3600)

        prompts = []
        async def llm(prompt):
            prompts.append(prompt)
            return '{"contradictions": []}'

        r = await reconcile_day(owner_id=1, day="2026-07-10", llm_call=llm, max_updates=3, brain_client=brain)
        assert r.checked >= 1                       # the fact reached the prefilter
        assert "AC unit is broken" in prompts[0]    # and the LLM actually saw it
    finally:
        await brain.close()
