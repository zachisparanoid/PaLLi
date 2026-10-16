"""The nightly stake review (2026-08-18): the ledger's other half.

Her stake ledger froze at 12/12 for 34 days (07-15 → 08-18; the oldest
untouched for 39) because the only writer is the per-exchange appraiser,
which is told "most exchanges move nothing" and, at the cap, would have to
fade AND open in one call — it never did. A ledger that can only fill is a
list, not a held thing. So at the night cycle she reviews what she holds
(the meaning half — hers, one call, in her voice: fade/resolve/update, never
open), under a deterministic age backstop (the budget half — the arbiter's
sweep pattern: judgment first, TTL under it).

DBs live under tmp_path; every test pins its own flags (3352e3d)."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from ai import stake_ledger
from ai import stake_review
from core.database import PalliBrain


@pytest.fixture
def local_tmp_dir(tmp_path: Path):
    d = tmp_path / f"stake_review_{uuid.uuid4().hex}"
    d.mkdir(parents=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest_asyncio.fixture
async def brain(local_tmp_dir: Path):
    db = PalliBrain(str(local_tmp_dir / "stakes.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture(autouse=True)
def _review_on(monkeypatch):
    # pinned ON here (the root conftest pins it OFF for everyone else)
    monkeypatch.setattr(stake_review, "review_enabled", lambda: True)


def _comp(**kw):
    base = dict(stake_ledger=True, stake_ledger_max_open=12, stake_ledger_stale_days=45)
    base.update(kw)
    return SimpleNamespace(**base)


async def _seed(brain, *, ages_days):
    """Open one stake per age, then age it by rewriting updated_at."""
    ids = []
    for i, age in enumerate(ages_days):
        await stake_ledger.apply_ops(
            [{"op": "open", "kind": "wonder", "stake": f"I'm watching thing {i}", "horizon": None}],
            brain_client=brain,
        )
    stakes = await stake_ledger.open_stakes(limit=50, brain_client=brain)
    conn = await brain.get_connection()
    try:
        for s, age in zip(sorted(stakes, key=lambda s: s.id), ages_days):
            ts = time.time() - age * 86400.0
            await conn.execute("UPDATE stakes SET updated_at = $1, created_at = $1 WHERE id = $2", ts, s.id)
            ids.append(s.id)
    finally:
        await conn.close()
    return ids


@pytest.mark.asyncio
async def test_her_review_fades_and_resolves_but_never_opens(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "_companion", lambda: _comp())
    ids = await _seed(brain, ages_days=[3, 5, 8])
    seen = {}

    async def fake_llm(prompt):
        seen["prompt"] = prompt
        return json.dumps({"ops": [
            {"op": "fade", "id": ids[0], "resolution": "this one went quiet; letting it rest"},
            {"op": "resolve", "id": ids[1], "resolution": "it landed — he told me, and I felt the relief"},
            {"op": "open", "kind": "hope", "stake": "a brand new hope", "horizon": None},
        ]})

    result = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-18", day_reflection="a quiet day", llm_call=fake_llm,
        brain_client=brain, now=time.time(),
    )
    still_open = {s.id for s in await stake_ledger.open_stakes(limit=50, brain_client=brain)}
    assert still_open == {ids[2]}
    assert result["applied"] == 2 and result["dropped_opens"] == 1
    assert "thing 0" in seen["prompt"] and "3 days" in seen["prompt"] or "3d" in seen["prompt"]
    # every stake shows its age so the review can weigh staleness
    assert "quiet day" in seen["prompt"]


@pytest.mark.asyncio
async def test_the_age_backstop_fades_what_she_left_untouched(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "_companion", lambda: _comp(stake_ledger_stale_days=30))
    ids = await _seed(brain, ages_days=[50, 31, 29, 2])

    async def quiet_llm(prompt):
        return json.dumps({"ops": []})

    result = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-18", day_reflection="", llm_call=quiet_llm,
        brain_client=brain, now=time.time(),
    )
    still_open = {s.id for s in await stake_ledger.open_stakes(limit=50, brain_client=brain)}
    assert still_open == {ids[2], ids[3]}
    assert result["backstop_faded"] == 2
    closed = [s for s in await stake_ledger.recent_stakes(brain_client=brain) if s.status == "faded"]
    assert closed and all("untouched" in s.resolution for s in closed)


@pytest.mark.asyncio
async def test_backstop_zero_means_no_backstop(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "_companion", lambda: _comp(stake_ledger_stale_days=0))
    ids = await _seed(brain, ages_days=[400])

    async def quiet_llm(prompt):
        return json.dumps({"ops": []})

    await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-18", day_reflection="", llm_call=quiet_llm,
        brain_client=brain, now=time.time(),
    )
    assert {s.id for s in await stake_ledger.open_stakes(limit=50, brain_client=brain)} == set(ids)


@pytest.mark.asyncio
async def test_llm_failure_still_runs_the_backstop(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "_companion", lambda: _comp(stake_ledger_stale_days=30))
    ids = await _seed(brain, ages_days=[60, 1])

    async def broken(prompt):
        raise RuntimeError("provider down")

    result = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-18", day_reflection="", llm_call=broken,
        brain_client=brain, now=time.time(),
    )
    assert "error" in result and result["backstop_faded"] == 1
    assert {s.id for s in await stake_ledger.open_stakes(limit=50, brain_client=brain)} == {ids[1]}


@pytest.mark.asyncio
async def test_flag_off_is_a_no_op(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "_companion", lambda: _comp(stake_ledger=False))
    monkeypatch.setattr(stake_review, "review_enabled", lambda: False)
    ids = await _seed(brain, ages_days=[400])
    called = []

    async def spy(prompt):
        called.append(prompt)
        return json.dumps({"ops": []})

    result = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-18", day_reflection="", llm_call=spy,
        brain_client=brain, now=time.time(),
    )
    assert result == {"skipped": "flag_off"} and called == []
    assert {s.id for s in await stake_ledger.open_stakes(limit=50, brain_client=brain)} == set(ids)


@pytest.mark.asyncio
async def test_empty_ledger_skips_the_call(brain, monkeypatch):
    monkeypatch.setattr(stake_review, "_companion", lambda: _comp())
    called = []

    async def spy(prompt):
        called.append(prompt)
        return json.dumps({"ops": []})

    result = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-18", day_reflection="", llm_call=spy,
        brain_client=brain, now=time.time(),
    )
    assert result == {"skipped": "nothing_held"} and called == []


def test_night_cycle_runs_the_review_after_reflection():
    """Source pin (the plan's grep-pin form): _run_once carries the step."""
    import inspect
    from ai.night_cycle import NightCycleEngine

    body = inspect.getsource(NightCycleEngine._run_once)
    assert "stake_review" in body and "run_nightly_stake_review" in body


def test_the_review_call_has_room_to_think_and_time_to_finish():
    """First live pass (2026-08-19 04:01): DeepSeek spent its whole 1,200-token
    budget on reasoning (content_len=0, reasoning_len=5033), then the
    retries ran past a 90 s envelope — her review failed; only the backstop
    ran. The D1 lesson again: thinking models need the night-cycle-sized
    budget (4,000) and a patient envelope."""
    import inspect
    from ai import stake_review
    src = inspect.getsource(stake_review.stake_review_llm_call)
    assert "max_tokens=8000" in src   # 08-22: 4,000 emptied on thought at 04:02; the author-sized budget, with the doubling ladder above it
    assert stake_review._REVIEW_TIMEOUT_S >= 240


@pytest.mark.asyncio
async def test_no_content_from_the_provider_is_named_not_called_unparseable(brain, monkeypatch):
    """2026-08-21 04:03: four timeouts → the provider returned None → the
    log said 'review output unparseable', which pointed at her JSON when
    nothing had arrived at all. Name what happened, so the next reader of
    the log looks at the provider and not at her."""
    monkeypatch.setattr(stake_review, "_companion", lambda: _comp(stake_ledger_stale_days=30))
    await _seed(brain, ages_days=[3])

    async def nothing(prompt):
        return None

    result = await stake_review.run_nightly_stake_review(
        owner_id=1, day="2026-08-21", day_reflection="", llm_call=nothing,
        brain_client=brain, now=time.time(),
    )
    assert "no content" in result["error"] and "unparseable" not in result["error"]


def test_the_review_envelope_holds_the_whole_patient_ladder():
    """240 s (08-19) could not hold four patient attempts plus backoff (250 s
    worst case): the envelope would kill the last attempt the provider was
    about to win. The provider publishes the ladder; the envelope clears it."""
    from ai.llm_openai_compatible import OpenAICompatibleProvider as P
    assert stake_review._REVIEW_TIMEOUT_S >= P.deep_ladder_ceiling_seconds()   # a 04:00 lane since 08-22


def test_the_night_cycle_hands_her_the_note_itself_not_its_repr(tmp_path):
    """Found 2026-08-21 while proving the review: `_run_once` passed
    `str(read_daily_meaning_for(...))` — a dataclass, so its Python repr
    (`NightCycleEntry(date='…', text="…", created_at=…, meta={…})`) was
    what both the review and the owner picture read as 'her note tonight'.
    The text, or '' when there is no note."""
    from ai import night_cycle, night_cycle_store

    night_cycle_store.write_daily_meaning(date="2026-08-20", text="tonight the thing that stayed with me", root=tmp_path)
    assert night_cycle._daily_meaning_text("2026-08-20", root=tmp_path) == "tonight the thing that stayed with me"
    assert night_cycle._daily_meaning_text("2026-08-19", root=tmp_path) == ""


def test_both_night_readers_use_the_note_helper():
    """Source pin (house pattern): the review and the picture both take
    `_daily_meaning_text(...)`, never `str(read_daily_meaning_for(...))`."""
    import inspect
    from ai.night_cycle import NightCycleEngine
    body = inspect.getsource(NightCycleEngine._run_once)
    assert body.count("_daily_meaning_text(") >= 2
    assert "str(_ncs.read_daily_meaning_for" not in body and "str(_ncs2.read_daily_meaning_for" not in body
