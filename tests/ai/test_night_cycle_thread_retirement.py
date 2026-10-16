"""Batch 5, task 8 (memory that picks, 2026-09-28; ruling R47): the thread
retirement rides the night pass right after the preferences rebuild,
reading ``memory.thread_archive_days`` (fallback 45). Live the arbiter's
daily backstop already dismisses every active topic thread older than 45
days, so this step finds about none — it is the never-surfaced rule and
the settings knob, not a second backstop. Its failure never costs the
night and reaches the health line.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import ai.consolidation as cons
import ai.disclosure_harvest as harvest
import ai.memory_sweep as memory_sweep
import ai.narrative_self as ns
import ai.night_cycle as nc
import ai.passions as passions
import core.memory_service as memory_service
from core.database import brain


def _wire(monkeypatch, calls: list, *, retire):
    async def fake_resolve():
        return 41

    async def fake_gather(*, owner_id, date):
        return "material"

    async def fake_reflect(**kw):
        calls.append("reflect")
        return {"wrote": kw["date"]}

    async def fake_consolidation(**kw):
        calls.append("consolidation")
        return {"skipped": "flag_off"}

    async def fake_ritual(**kw):
        calls.append("narrative")
        return {"week": "2026-W39", "docs": {}}

    async def fake_sweep(**kw):
        calls.append("memory_sweep")
        return {"moved": 0, "stamped": 0}

    async def fake_rebuild(**kw):
        calls.append("preferences_rebuild")
        return {"status": "ok", "written": 0, "failed": 0}

    async def fake_passions_step(**kw):
        calls.append("passions")
        return {"journal": {"skipped": "flag_off"}, "interests": {"skipped": "flag_off"}}

    async def fake_harvest(**kw):
        calls.append("disclosure_harvest")
        return {"faded": 0, "planted": None}

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)
    monkeypatch.setattr(memory_sweep, "sweep_memory", fake_sweep)
    monkeypatch.setattr(memory_service, "rebuild_user_memory_from_documents", fake_rebuild)
    monkeypatch.setattr(passions, "run_passions_step", fake_passions_step)
    monkeypatch.setattr(harvest, "run_nightly_disclosure_harvest", fake_harvest)
    monkeypatch.setattr(brain, "retire_unsurfaced_open_threads", retire)


def test_run_once_retires_never_surfaced_threads_after_the_rebuild_with_the_settings_days(monkeypatch):
    calls: list = []
    retired: list = []

    async def fake_retire(**kw):
        calls.append("thread_retirement")
        retired.append(kw)
        return 2

    _wire(monkeypatch, calls, retire=fake_retire)
    monkeypatch.setattr(nc, "_thread_archive_days", lambda: 30)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-27"))
    assert retired == [{"owner_user_id": 41, "older_than_days": 30}]
    assert calls.index("preferences_rebuild") < calls.index("thread_retirement") < calls.index("passions")
    assert result["thread_retirement"] == {"retired": 2, "older_than_days": 30}


def test_thread_archive_days_reads_the_settings_snapshot_and_falls_back_to_45(monkeypatch):
    import core.settings_boot as settings_boot

    monkeypatch.setattr(
        settings_boot.settings_service, "snapshot",
        lambda: SimpleNamespace(memory=SimpleNamespace(thread_archive_days=12)),
    )
    assert nc._thread_archive_days() == 12
    monkeypatch.setattr(settings_boot.settings_service, "snapshot", lambda: SimpleNamespace(memory=None))
    assert nc._thread_archive_days() == 45

    def boom():
        raise RuntimeError("settings not booted")

    monkeypatch.setattr(settings_boot.settings_service, "snapshot", boom)
    assert nc._thread_archive_days() == 45


def test_a_failing_retirement_never_costs_the_night(monkeypatch):
    calls: list = []

    async def boom(**kw):
        calls.append("thread_retirement")
        raise RuntimeError("database is locked")

    _wire(monkeypatch, calls, retire=boom)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-27"))
    assert result["thread_retirement"] == {"error": "database is locked"}
    assert result["wrote"] == "2026-09-27"
    assert "passions" in calls and "disclosure_harvest" in calls


def test_surface_errors_names_the_retirement():
    assert "thread_retirement: database is locked" in nc._surface_errors(
        {"thread_retirement": {"error": "database is locked"}}
    )
    assert nc._surface_errors({"thread_retirement": {"retired": 0, "older_than_days": 45}}) == ""
