"""Her own time: three workbench sessions a day (Batch 6, item 3.7; owner
call 10; rulings R57-R59).

The schema's default is already three; only the settings file says one,
and it moves at the wrap. These tests pin what three a day means in the
code as it is: three sessions run and a fourth is refused; the spacing rule
(own_work_min_gap_minutes) holds between every pair; a session that failed
to start spends neither the budget nor the gap; and her time and the
owner's turn never block each other — her time is one thing at a time
among HER activities only. The owner's call of 2026-08-18 stands: she may
sit down whenever, even while they are talking, and there are no quiet
hours for her own work (tests/ai/test_workbench_her_time.py pins the gate).

The last test is one count for one day (R59): the daydream tells her how
many sessions are left today by the same 04:00 day the budget counts.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai.workbench as wb
from ai import her_time
from ai import workbench_store as ws
from core.settings_schema import CompanionConfig


def _bench(monkeypatch, tmp_path: Path, comp, *, dispatch=None) -> list:
    """Her workspace and her record of sessions in tmp; the session's model
    turn stood in for, so a session that runs is visible and cheap. Returns
    the turns dispatched."""
    monkeypatch.setattr(wb, "companion_settings", lambda: comp)
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path / "workspace")
    (tmp_path / "workspace" / "projects").mkdir(parents=True)
    (tmp_path / "workspace" / "WORKBENCH.md").write_text("# Workbench\n", encoding="utf-8")
    monkeypatch.setattr(ws, "WORKBENCH_DIR", tmp_path / "sessions")
    dispatched: list = []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        return SimpleNamespace(responded=True, response_text="tinkered quietly.")

    async def refs():
        return {"app_user_id": 1, "palli_user_id": 1}

    monkeypatch.setattr(wb, "_dispatch", dispatch or fake_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", refs)
    monkeypatch.setattr(wb, "_notice_own_work", lambda **kw: None)
    monkeypatch.setattr(wb, "_notice_daydream", lambda **kw: None)
    monkeypatch.setattr(wb, "_commit_workspace", lambda message: True)
    # A session's memory entry queues a database index on the running loop,
    # and each asyncio.run here closes its loop under it; the memory is not
    # what these tests are about.
    monkeypatch.setattr(wb, "_remember_session", lambda **kw: None)
    her_time.reset_for_tests()
    return dispatched


def _let_time_pass(tmp_path: Path, minutes: float) -> None:
    """Move every recorded session back by ``minutes``, as if that much
    time had passed since each one started."""
    for path in (tmp_path / "sessions").glob("*.jsonl"):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        for row in rows:
            row["started"] = float(row["started"]) - minutes * 60
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _session() -> dict:
    return asyncio.run(wb.workbench_engine.run_session_once())


# Midday, far from the budget's 04:00 day key and from midnight: a run that
# straddled either on the wall clock could count its sessions in two days.
MIDDAY = datetime(2026, 8, 6, 12, 0)


def _freeze(monkeypatch, when: datetime) -> float:
    """Her budget's clock and her record's clock stopped at ``when``."""
    frozen = when.timestamp()
    clock = SimpleNamespace(time=lambda: frozen, strftime=time.strftime, localtime=time.localtime)
    monkeypatch.setattr(ws, "time", clock)
    monkeypatch.setattr(wb, "time", clock)
    return frozen


def test_three_sessions_run_and_a_fourth_is_refused(monkeypatch, tmp_path):
    comp = CompanionConfig(own_work=True, own_work_min_gap_minutes=0, own_work_min_free_disk_gb=0)
    assert comp.own_work_daily_sessions == 3            # the schema's three, untouched
    dispatched = _bench(monkeypatch, tmp_path, comp)
    _freeze(monkeypatch, MIDDAY)

    assert [_session()["ran"] for _ in range(3)] == [True, True, True]
    fourth = _session()

    assert fourth["ran"] is False
    assert fourth["why"] == "daily budget reached (3/3)"
    assert len(dispatched) == 3


def test_the_spacing_holds_between_the_first_and_second_and_the_second_and_third(monkeypatch, tmp_path):
    comp = CompanionConfig(own_work=True, own_work_min_gap_minutes=90, own_work_min_free_disk_gb=0)
    dispatched = _bench(monkeypatch, tmp_path, comp)
    _freeze(monkeypatch, MIDDAY)

    assert _session()["ran"] is True                                    # the first
    too_soon = _session()
    assert too_soon["ran"] is False and "session gap" in too_soon["why"]
    _let_time_pass(tmp_path, 91)
    assert _session()["ran"] is True                                    # the second
    too_soon = _session()                                               # 0 min after the second,
    assert too_soon["ran"] is False and "session gap" in too_soon["why"]  # 91 after the first
    _let_time_pass(tmp_path, 91)
    assert _session()["ran"] is True                                    # the third
    assert len(dispatched) == 3


def test_a_session_that_failed_to_start_spends_neither_the_budget_nor_the_gap(monkeypatch, tmp_path):
    comp = CompanionConfig(own_work=True, own_work_daily_sessions=1,
                           own_work_min_gap_minutes=90, own_work_min_free_disk_gb=0)
    calls: list = []

    async def flaky_dispatch(turn):
        calls.append(turn)
        if len(calls) == 1:
            raise RuntimeError("the model did not answer")
        return SimpleNamespace(responded=True, response_text="tinkered quietly.")

    _bench(monkeypatch, tmp_path, comp, dispatch=flaky_dispatch)
    _freeze(monkeypatch, MIDDAY)

    failed = _session()
    assert failed["ran"] is False and "dispatch failed" in failed["why"]
    assert ws.read_recent(days_back=1, limit=5) == []                   # nothing recorded
    assert _session()["ran"] is True                                    # no gap, budget whole
    spent = _session()
    assert spent["ran"] is False and spent["why"] == "daily budget reached (1/1)"


@pytest.mark.asyncio
async def test_her_time_and_the_owners_turn_never_block_each_other(monkeypatch, tmp_path):
    """The owner's turn runs on its own lanes (core/agent/task_slots.py:
    the talk lane and the task slots of their channel); her session holds
    her_time, which only her own activities share. Their lanes held never
    stop her session; her session running never takes their lanes; a dive
    of hers waits for her session."""
    from core.agent import task_slots

    comp = CompanionConfig(own_work=True, own_work_min_gap_minutes=0, own_work_min_free_disk_gb=0)
    release = asyncio.Event()
    started = asyncio.Event()
    held_back: list = []

    async def slow_dispatch(turn):
        held_back.append(turn)
        if len(held_back) == 2:
            started.set()
            await release.wait()
        return SimpleNamespace(responded=True, response_text="tinkered quietly.")

    _bench(monkeypatch, tmp_path, comp, dispatch=slow_dispatch)
    now = _freeze(monkeypatch, MIDDAY)
    task_slots.reset_for_tests()
    theirs = "discord:1"
    try:
        # The owner's turn in flight: the talk lane and a task slot are theirs.
        assert task_slots.talk_acquire(theirs) and task_slots.acquire(theirs, "their turn")
        assert await wb.workbench_engine._eligible(now) is True
        first = await wb.workbench_engine.run_session_once()
        assert first["ran"] is True
        assert held_back[0].surface == "workbench"          # her own session, not their door
        task_slots.talk_release(theirs)
        task_slots.release(theirs, "their turn")

        # Her session in flight: their lanes stay free; a dive of hers waits.
        session = asyncio.create_task(wb.workbench_engine.run_session_once())
        await asyncio.wait_for(started.wait(), timeout=5)
        assert her_time.busy() == "a workbench session"
        assert task_slots.talk_acquire(theirs) and task_slots.acquire(theirs, "their turn")
        dived: list = []

        async def dive():
            async with her_time.hold("a curiosity dive"):
                dived.append(True)

        diving = asyncio.create_task(dive())
        await asyncio.sleep(0.05)
        assert dived == []                                   # one thing at a time: hers
        release.set()
        assert (await asyncio.wait_for(session, timeout=5))["ran"] is True
        await asyncio.wait_for(diving, timeout=5)
        assert dived == [True]
    finally:
        release.set()
        task_slots.reset_for_tests()
        her_time.reset_for_tests()


# ── one count, one day (R59) ─────────────────────────────────────────────

def test_the_daydream_counts_the_sessions_left_by_the_budgets_day(monkeypatch, tmp_path):
    """At 01:30 the calendar day is two hours old, but her budget's day
    began at 04:00 yesterday: three sessions since then (20:00, 23:30,
    00:30) spend all three. The daydream used to count the calendar day's
    file only and told her two were left; the budget refused a fourth. It
    now reads the budget's own count."""
    import ai.daydream as dd
    import core.turn.companion_flags as companion_flags

    comp = CompanionConfig(own_work=True, own_work_min_gap_minutes=0, own_work_min_free_disk_gb=0)
    _bench(monkeypatch, tmp_path, comp)
    monkeypatch.setattr(companion_flags, "companion_settings", lambda: comp)
    _freeze(monkeypatch, datetime(2026, 8, 6, 1, 30))
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for day, hours in (("2026-08-05", ((20, 0), (23, 30))), ("2026-08-06", ((0, 30),))):
        (sessions / f"{day}.jsonl").write_text("".join(
            json.dumps({"started": datetime.fromisoformat(f"{day}T{h:02d}:{m:02d}").timestamp(),
                        "outcome": "ran"}) + "\n" for h, m in hours), encoding="utf-8")

    assert "Your time: 0 workbench session(s) left today." in dd._affordances_block()
    refused = _session()
    assert refused["ran"] is False and refused["why"] == "daily budget reached (3/3)"
