"""Batch 5, task 7 (memory that picks, 2026-09-28; rulings R22, R47): the
``user_memory`` preferences cache is rebuilt nightly. ``core.memory_service
.rebuild_user_memory_from_documents`` has had no caller since May, so the
cache that recall's fact retrieval reads drifted from the ``preferences``
document reflect writes. The night pass calls it once, with the owner's id,
right after the memory sweep; its failure never costs the night, and its
error — raised, or the rebuilder's own ``status: error`` — reaches the
health line. The monkeypatched-steps style of tests/ai/test_night_cycle.py.
"""
from __future__ import annotations

import asyncio

import ai.consolidation as cons
import ai.disclosure_harvest as harvest
import ai.memory_sweep as memory_sweep
import ai.narrative_self as ns
import ai.night_cycle as nc
import ai.passions as passions
import core.memory_service as memory_service


def _wire(monkeypatch, calls: list, *, rebuild):
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
    monkeypatch.setattr(passions, "run_passions_step", fake_passions_step)
    monkeypatch.setattr(harvest, "run_nightly_disclosure_harvest", fake_harvest)
    monkeypatch.setattr(memory_service, "rebuild_user_memory_from_documents", rebuild)


def test_run_once_rebuilds_the_preferences_cache_once_with_the_owner_id_after_the_sweep(monkeypatch):
    calls: list = []
    rebuilt: list = []

    async def fake_rebuild(**kw):
        calls.append("preferences_rebuild")
        rebuilt.append(kw)
        return {"status": "ok", "owner_user_id": kw["owner_user_id"], "scrubbed": 3, "written": 3, "failed": 0}

    _wire(monkeypatch, calls, rebuild=fake_rebuild)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-27"))
    assert rebuilt == [{"owner_user_id": 41}]
    assert calls.index("memory_sweep") < calls.index("preferences_rebuild") < calls.index("passions")
    assert result["preferences_rebuild"] == {
        "status": "ok", "owner_user_id": 41, "scrubbed": 3, "written": 3, "failed": 0,
    }
    assert result["wrote"] == "2026-09-27"


def test_a_failing_rebuild_never_costs_the_night(monkeypatch):
    calls: list = []

    async def boom(**kw):
        calls.append("preferences_rebuild")
        raise RuntimeError("database is locked")

    _wire(monkeypatch, calls, rebuild=boom)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-27"))
    assert result["preferences_rebuild"] == {"error": "database is locked"}
    assert result["wrote"] == "2026-09-27"
    # the steps after it still ran
    assert "passions" in calls and "disclosure_harvest" in calls


def test_surface_errors_names_the_rebuild():
    # the step raised
    assert "preferences_rebuild: database is locked" in nc._surface_errors(
        {"preferences_rebuild": {"error": "database is locked"}}
    )
    # the rebuilder's own failure shape — it returns status=error instead of raising
    line = nc._surface_errors(
        {"preferences_rebuild": {"status": "error", "error": "OSError: bundle load failed", "written": 0}}
    )
    assert "preferences_rebuild: OSError: bundle load failed" in line
    # a partial rebuild names how many rows it lost
    assert "preferences_rebuild partial: 2 rows failed" in nc._surface_errors(
        {"preferences_rebuild": {"status": "partial", "written": 5, "failed": 2}}
    )
    # a clean night is silent
    assert nc._surface_errors({"preferences_rebuild": {"status": "ok", "written": 43, "failed": 0}}) == ""
