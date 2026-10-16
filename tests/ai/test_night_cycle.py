"""Night Cycle engine + pass + gate (2026-07-11)."""
from __future__ import annotations

import asyncio
import types
from datetime import datetime
from types import SimpleNamespace

import pytest

from ai.night_cycle import NightCycleEngine, night_cycle_enabled, run_night_cycle


# ── gate (Task 2) ───────────────────────────────────────────────────────

def test_gate_off_when_unbooted(monkeypatch):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: None)
    assert night_cycle_enabled() is False


def test_gate_reads_flag(monkeypatch):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=True))
    assert night_cycle_enabled() is True
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=False))
    assert night_cycle_enabled() is False


# ── the pass (Task 3) ───────────────────────────────────────────────────

def _run(**kw):
    return asyncio.run(run_night_cycle(**kw))


def test_pass_writes_first_person_entry(monkeypatch):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=True))
    written = {}

    async def fake_llm(prompt):
        # a well-behaved reflection references the day it was given
        assert "what the day meant" in prompt  # the approved prompt loaded + substituted
        return "I noticed the reminder work finally landed. He was lighter for it."

    def fake_store(*, date, text, meta):
        written.update(date=date, text=text, meta=meta)

    r = _run(owner_id=1, date="2026-07-11", day_material="[peak] reminders fixed",
             llm_call=fake_llm, store_write=fake_store, now=0.0)
    assert r.get("wrote") == "2026-07-11"
    assert "reminder" in written["text"].lower()
    assert written["meta"]["owner_id"] == 1


def test_pass_never_raises_on_llm_failure(monkeypatch):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=True))

    async def boom(prompt):
        raise RuntimeError("llm down")

    def fake_store(**k):
        raise AssertionError("must not write on failure")

    r = _run(owner_id=1, date="2026-07-11", day_material="x",
             llm_call=boom, store_write=fake_store, now=0.0)
    assert "error" in r


def test_pass_skips_when_flag_off(monkeypatch):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: None)

    async def fake_llm(p):
        raise AssertionError("must not call llm when flag off")

    r = _run(owner_id=1, date="2026-07-11", day_material="x",
             llm_call=fake_llm, store_write=lambda **k: None, now=0.0)
    assert r.get("skipped")


# ── the day's conversation reaches the reflection (Batch 3, 1.4) ──────

def test_gather_day_material_includes_the_days_conversation(monkeypatch):
    """The "## The day with them" section reads the day's REAL conversation
    from the database (both doors), not the memory-workspace day-log.

    History: the 2026-07-15 pin of this test caught _gather_day_material
    importing a nonexistent name (core.memory_service.memory_service), so the
    best-effort except silently dropped the day-log from EVERY reflection.
    Re-pinned 2026-09-28 (owner-approved, Batch 3 item 1.4): the day-log's
    newest file is 2026-04-30, so that section was empty or ancient every
    night; the source is now the owner's conversation rows for the day."""
    import core.memory_service as ms
    import ai.night_cycle as nc

    seen = {}

    async def fake_load(owner_id, date):
        seen.update(owner_id=owner_id, date=date)
        return [
            (1.0, "discord", "user", "the survey finally got a date"),
            (2.0, "discord", "assistant", "that is a relief, after the wait"),
        ]

    async def stale_day_log(*, owner_user_id, day):
        return {"markdown": "## 2026-07-10\n- Summary: a line from the day-log"}

    monkeypatch.setattr(nc, "_load_day_conversation", fake_load)
    monkeypatch.setattr(ms.memory_workspace_service, "get_day", stale_day_log)
    material = asyncio.run(nc._gather_day_material(owner_id=1, date="2026-07-10"))
    assert seen == {"owner_id": 1, "date": "2026-07-10"}
    assert "The day with them" in material
    assert "  them: the survey finally got a date" in material
    assert "  me: that is a relief, after the wait" in material
    assert "a line from the day-log" not in material


def test_gather_day_material_has_no_day_section_on_an_empty_day(monkeypatch):
    """An empty day yields NO "The day with them" heading: the prompt already
    says to call a quiet day quiet, and an empty section would invite
    invention."""
    import ai.night_cycle as nc

    async def nothing(owner_id, date):
        return []

    monkeypatch.setattr(nc, "_load_day_conversation", nothing)
    material = asyncio.run(nc._gather_day_material(owner_id=1, date="2026-07-10"))
    assert "The day with them" not in material


def test_gather_day_material_survives_a_store_failure(monkeypatch, caplog):
    """A database failure loses only the day section; the rest of the
    material (here a surprise marker) still reaches the reflection. The
    failure is a WARNING, not a debug line: a store that failed every night
    would otherwise give her a "quiet day" forever with nothing in the log
    (the 2026-07-15 precedent, where a debug-level swallow kept the day out
    of every reflection)."""
    import logging

    import ai.night_cycle as nc

    async def boom(owner_id, date):
        raise RuntimeError("db down")

    monkeypatch.setattr(nc, "_load_day_conversation", boom)
    monkeypatch.setattr(nc, "_surprise_markers", lambda day, **kw: ["[a real departure]"])
    with caplog.at_level(logging.WARNING, logger="ai.night_cycle"):
        material = asyncio.run(nc._gather_day_material(owner_id=1, date="2026-07-10"))
    assert "The day with them" not in material
    assert "The day's surprises" in material
    assert "[a real departure]" in material
    warned = [
        r for r in caplog.records
        if r.name == "ai.night_cycle" and r.levelno == logging.WARNING
        and "day conversation unavailable" in r.getMessage()
    ]
    assert len(warned) == 1 and "db down" in warned[0].getMessage()


# ── the engine (Task 4) ─────────────────────────────────────────────────

# The run time moved from 04:00 to 06:30 local (the owner, 10-05: "move the
# night run off-peak"); the day boundary stays at 04:00.

def test_seconds_until_the_run_time_before():
    e = NightCycleEngine()
    # 01:00 -> the next 06:30 is 5.5h away; 05:00 (past the 04:00 day
    # boundary) -> 1.5h, never "now"
    assert abs(e._seconds_until_next_run(datetime(2026, 7, 11, 1, 0, 0)) - 5.5 * 3600) < 1
    assert abs(e._seconds_until_next_run(datetime(2026, 7, 11, 5, 0, 0)) - 1.5 * 3600) < 1
    assert abs(e._seconds_until_next_run(datetime(2026, 7, 11, 6, 29, 0)) - 60) < 1


def test_seconds_until_the_run_time_after_wraps_to_tomorrow():
    e = NightCycleEngine()
    # 07:00 -> today's 06:30 already passed, so next is tomorrow's: 23.5h away
    assert abs(e._seconds_until_next_run(datetime(2026, 7, 11, 7, 0, 0)) - 23.5 * 3600) < 1
    # exactly 06:30 -> the run is now, so the NEXT one is tomorrow's
    assert abs(e._seconds_until_next_run(datetime(2026, 7, 11, 6, 30, 0)) - 24 * 3600) < 1


def test_the_next_run_follows_the_setting(monkeypatch):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings",
                        lambda: SimpleNamespace(night_cycle=True, night_cycle_run_at="07:45"))
    e = NightCycleEngine()
    assert abs(e._seconds_until_next_run(datetime(2026, 7, 11, 7, 0, 0)) - 45 * 60) < 1


def test_night_run_at_defaults_to_half_past_six(monkeypatch):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: None)
    assert nc.night_run_at() == (6, 30)
    assert nc.NIGHT_RUN_AT_DEFAULT == "06:30"
    assert nc.NIGHT_CYCLE_HOUR == 4  # the day boundary is unchanged
    from core.settings_schema import CompanionConfig
    assert CompanionConfig().night_cycle_run_at == "06:30"
    assert nc.night_run_at(CompanionConfig()) == (6, 30)


@pytest.mark.parametrize("value, expected", [
    ("07:15", (7, 15)), ("4:00", (4, 0)), (" 23:59 ", (23, 59)), ("04:00", (4, 0)),
    # not a time, or before the 04:00 day boundary: the default, never a crash
    ("25:00", (6, 30)), ("6.30", (6, 30)), ("", (6, 30)), ("03:59", (6, 30)),
    ("01:00", (6, 30)), (None, (6, 30)),
])
def test_night_run_at_reads_the_setting(value, expected):
    import ai.night_cycle as nc
    assert nc.night_run_at(SimpleNamespace(night_cycle_run_at=value)) == expected


def test_a_bad_run_time_warns_once(monkeypatch, caplog):
    import logging
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "_RUN_AT_WARNED", set())
    with caplog.at_level(logging.WARNING, logger=nc.logger.name):
        for _ in range(3):
            nc.night_run_at(SimpleNamespace(night_cycle_run_at="soon"))
    lines = [r for r in caplog.records if "night_cycle_run_at" in r.getMessage()]
    assert len(lines) == 1 and "06:30" in lines[0].getMessage()


def test_half_past_six_new_york_is_off_peak_all_year():
    """The run time's reason (the owner, 10-05): 06:30 in New York is off
    DeepSeek's peak on every weekday, under EDT and under EST; the old 04:00
    was peak on both."""
    from zoneinfo import ZoneInfo
    from ai.deepseek_peak import is_deepseek_peak

    ny = ZoneInfo("America/New_York")
    for day in (datetime(2026, 10, 5), datetime(2026, 11, 2), datetime(2027, 1, 13),
                datetime(2027, 3, 15), datetime(2027, 7, 9)):
        assert day.weekday() < 5
        run = day.replace(hour=6, minute=30, tzinfo=ny)
        assert not is_deepseek_peak(run), run
        assert is_deepseek_peak(day.replace(hour=4, minute=0, tzinfo=ny)), day


def test_idempotent_same_date(tmp_path):
    e = NightCycleEngine(state_path=tmp_path / "state.json")
    assert e._claim_date("2026-07-11") is True
    assert e._claim_date("2026-07-11") is False   # second claim same date is a no-op
    assert e._claim_date("2026-07-12") is True


def test_run_once_calls_consolidation_after_reflection(monkeypatch, tmp_path):
    import ai.night_cycle as nc
    import ai.consolidation as consolidation
    order = []
    async def fake_resolve():
        return 1
    async def fake_gather(*, owner_id, date):
        return "material"
    async def fake_pass(**k):
        order.append("reflection")
        return {"wrote": k["date"]}
    async def fake_consol(*, owner_id, day):
        order.append(("consolidation", day, owner_id))
        return {"skipped": "flag_off"}
    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_pass)
    monkeypatch.setattr(consolidation, "run_nightly_consolidation", fake_consol)
    e = NightCycleEngine(state_path=tmp_path / "s.json")
    result = asyncio.run(e._run_once("2026-07-14"))
    assert order == ["reflection", ("consolidation", "2026-07-14", 1)]
    assert result["consolidation"] == {"skipped": "flag_off"}


def test_run_once_consolidation_failure_never_breaks_the_night(monkeypatch, tmp_path):
    import ai.night_cycle as nc
    import ai.consolidation as consolidation
    async def fake_resolve():
        return 1
    async def fake_gather(**k):
        return ""
    async def fake_pass(**k):
        return {"wrote": "2026-07-14"}
    async def boom(**k):
        raise RuntimeError("consolidation exploded")
    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_pass)
    monkeypatch.setattr(consolidation, "run_nightly_consolidation", boom)
    e = NightCycleEngine(state_path=tmp_path / "s.json")
    result = asyncio.run(e._run_once("2026-07-14"))
    assert result.get("wrote") == "2026-07-14"
    assert "error" in result["consolidation"]


def test_reflects_on_yesterday_not_the_4am_wall_date():
    # The engine wakes at ~4am; the day it just lived is YESTERDAY's calendar
    # date, not the fresh post-midnight date. (Design 4.1: the note "written at
    # 4 a.m." is about "Tuesday ... tonight" — i.e. the day that just ended.)
    e = NightCycleEngine()
    assert e._reflection_date(datetime(2026, 7, 14, 4, 0, 0)) == "2026-07-13"
    assert e._reflection_date(datetime(2026, 1, 1, 4, 0, 0)) == "2025-12-31"


def test_surface_errors_includes_nested_consolidation_failures():
    """Review fix: a failing consolidation used to leave last_error='' —
    actively wiped every night the reflection succeeded."""
    from ai.night_cycle import _surface_errors
    assert _surface_errors({"wrote": "2026-07-14"}) == ""
    assert _surface_errors({"error": "prompt_missing"}) == "prompt_missing"
    nested = {"wrote": "2026-07-14",
              "consolidation": {"day_gist": {"error": "prompt_missing"},
                                "aging": {"rewritten": 1, "error": ""},
                                "reconcile": {"error": "unparseable_verdict"}}}
    surfaced = _surface_errors(nested)
    assert "day_gist: prompt_missing" in surfaced
    assert "reconcile: unparseable_verdict" in surfaced
    top = {"consolidation": {"error": "engine exploded"}}
    assert "consolidation: engine exploded" in _surface_errors(top)


# ── narrative step rides the tick (Wave 1b Task 7) ───────────────────────


def test_run_once_includes_narrative_step_after_consolidation(monkeypatch):
    """The weekly ritual rides the same tick, AFTER consolidation; its
    failure must never cost the night's reflection or consolidation."""
    import ai.consolidation as cons
    import ai.narrative_self as ns
    import ai.night_cycle as nc

    calls = []

    async def fake_resolve():
        return 1

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
        assert kw["owner_id"] == 1
        return {"week": "2026-W29", "docs": {}}

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)

    engine = nc.NightCycleEngine()
    result = asyncio.run(engine._run_once("2026-07-15"))
    assert calls == ["reflect", "consolidation", "narrative"]
    assert result["narrative"] == {"week": "2026-W29", "docs": {}}


def test_run_once_narrative_failure_never_costs_the_night(monkeypatch):
    import ai.consolidation as cons
    import ai.narrative_self as ns
    import ai.night_cycle as nc

    async def fake_resolve():
        return 1

    async def fake_gather(*, owner_id, date):
        return "material"

    async def fake_reflect(**kw):
        return {"wrote": kw["date"]}

    async def fake_consolidation(**kw):
        return {"skipped": "flag_off"}

    async def boom(**kw):
        raise RuntimeError("ritual exploded")

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", boom)

    engine = nc.NightCycleEngine()
    result = asyncio.run(engine._run_once("2026-07-15"))
    assert result["wrote"] == "2026-07-15"
    assert result["narrative"] == {"error": "ritual exploded"}


def test_surface_errors_includes_narrative_errors_and_rejections():
    """Rejected revisions surface LOUDLY in tick health (spec §9), alongside
    narrative errors — the ea5b0f0 lesson: partial reads wipe real failures."""
    from ai.night_cycle import _surface_errors

    line = _surface_errors(
        {
            "wrote": "2026-07-15",
            "consolidation": {"skipped": "flag_off"},
            "narrative": {
                "week": "2026-W29",
                "docs": {
                    "MY_STORY.md": {"committed": "abc123"},
                    "STORY_OF_US.md": {"rejected": "not earned by the record"},
                    "WHAT_I_CARE_ABOUT.md": {"error": "gate_error: lane down"},
                },
            },
        }
    )
    assert "STORY_OF_US.md rejected: not earned by the record" in line
    assert "WHAT_I_CARE_ABOUT.md: gate_error: lane down" in line

    line2 = _surface_errors({"wrote": "x", "narrative": {"error": "ritual exploded"}})
    assert "narrative: ritual exploded" in line2

    # healthy pass (skips + commits only) stays quiet
    assert _surface_errors({"wrote": "x", "narrative": {"skipped": "already_this_week"}}) == ""


def test_surface_errors_includes_passions_errors_and_rejections():
    """Review fix (2026-07-20): the passions step was invisible to the health
    line — it read error/consolidation/narrative and never result['passions'],
    so prompt_missing, unparseable_judgment, gate_error, week_marker_unwritable,
    every gate REJECTION and the containment wrapper's own error all left
    last_error empty and the engine read healthy.

    This is the worst place in the organ for that, because "no page tonight"
    is the DESIGNED common case: a permanently broken organ looked exactly
    like a run of quiet days."""
    from ai.night_cycle import _surface_errors

    line = _surface_errors(
        {
            "wrote": "2026-07-15",
            "consolidation": {"skipped": "flag_off"},
            "narrative": {"skipped": "already_this_week"},
            "passions": {
                "journal": {"error": "gate_error: lane down"},
                "interests": {"gate_rejected": "invented detail"},
            },
        }
    )
    assert "passions journal: gate_error: lane down" in line
    assert "passions interests rejected: invented detail" in line

    # the containment wrapper's own top-level error (night_cycle.py :363)
    assert "passions: passions exploded" in _surface_errors(
        {"wrote": "x", "passions": {"error": "passions exploded"}}
    )

    # the other way round: a refused page, an unwritable week marker
    line2 = _surface_errors(
        {
            "passions": {
                "journal": {"gate_rejected": "made_thing_cap"},
                "interests": {"error": "week_marker_unwritable"},
            }
        }
    )
    assert "passions journal rejected: made_thing_cap" in line2
    assert "passions interests: week_marker_unwritable" in line2

    # a genuinely quiet, healthy night stays quiet — no page is not a failure
    assert (
        _surface_errors(
            {
                "wrote": "x",
                "passions": {
                    "journal": {"skipped": "quiet_day", "reasoning": "nothing moved"},
                    "interests": {"skipped": "already_this_week"},
                },
            }
        )
        == ""
    )
    # ...and so does a night that wrote a page and revised the file
    assert (
        _surface_errors(
            {
                "wrote": "x",
                "passions": {
                    "journal": {"wrote": True, "sha": "abc123", "made_thing": False},
                    "interests": {"wrote": True, "sha": "def456"},
                },
            }
        )
        == ""
    )


def test_surface_errors_includes_the_disclosure_harvest():
    """The passions class again, for organ 4.9 (2026-09-01): the harvest's
    designed common case is planted=None — "nothing tonight" — so a
    permanently broken one (prompt missing, unparseable candidate, the
    containment wrapper's own error) reads exactly like a run of quiet
    weeks unless the health line names it."""
    from ai.night_cycle import _surface_errors

    assert "disclosure_harvest: harvest prompt missing" in _surface_errors(
        {"wrote": "x", "disclosure_harvest": {"planted": None, "faded": 0,
                                              "error": "harvest prompt missing"}}
    )
    # the containment wrapper's own error, the sibling shape
    assert "disclosure_harvest: import exploded" in _surface_errors(
        {"wrote": "x", "disclosure_harvest": {"error": "import exploded"}}
    )
    # a quiet week and a flag-off night are healthy, not failures
    assert _surface_errors(
        {"wrote": "x", "disclosure_harvest": {"planted": None, "faded": 2}}
    ) == ""
    assert _surface_errors(
        {"wrote": "x", "disclosure_harvest": {"skipped": "flag_off"}}
    ) == ""


def test_surface_errors_names_the_harvest_contract_failures():
    """Review I-4: fix 1 closed the class only for EXCEPTIONS, but the
    likeliest failures — a model swap or prompt drift that stops producing
    contract JSON, or a candidate the receipt law refuses — raise nothing.
    They must not read byte-identical to a quiet week either."""
    from ai.night_cycle import _surface_errors

    for reason in ("unparseable", "malformed_text", "no_receipt"):
        line = _surface_errors(
            {"wrote": "x", "disclosure_harvest": {"faded": 0, "planted": None,
                                                  "skipped": reason}}
        )
        assert line == f"disclosure_harvest refused: {reason}", reason

    # the benign states stay silent: nothing tonight, the organ off, the cap
    # declining (I-1's test is what guards a cap that never empties), and a
    # truth she already holds are all healthy nights, not failures.
    for benign in ({"faded": 0, "planted": None},
                   {"skipped": "flag_off"},
                   {"faded": 1, "planted": None, "skipped": "cap_full"},
                   {"faded": 0, "planted": None, "skipped": "already_in_the_book"},
                   {"faded": 0, "planted": "a-thread-abc123"}):
        assert _surface_errors({"wrote": "x", "disclosure_harvest": benign}) == "", benign


# ── passions step rides the tick (Task 6) ────────────────────────────────


def test_run_once_includes_passions_step_after_narrative(monkeypatch):
    """Organ 3's passions step rides the same tick, AFTER the narrative
    ritual (the last step so far); result["passions"] carries its result."""
    import ai.consolidation as cons
    import ai.narrative_self as ns
    import ai.night_cycle as nc
    import ai.passions as passions

    calls = []

    async def fake_resolve():
        return 1

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
        return {"week": "2026-W29", "docs": {}}

    async def fake_passions_step(**kw):
        calls.append("passions")
        assert kw["owner_id"] == 1
        assert kw["date"] == "2026-07-15"
        return {"journal": {"skipped": "flag_off"}, "interests": {"skipped": "flag_off"}}

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)
    monkeypatch.setattr(passions, "run_passions_step", fake_passions_step)

    engine = nc.NightCycleEngine()
    result = asyncio.run(engine._run_once("2026-07-15"))
    assert calls == ["reflect", "consolidation", "narrative", "passions"]
    assert result["passions"] == {
        "journal": {"skipped": "flag_off"},
        "interests": {"skipped": "flag_off"},
    }


def test_run_once_includes_the_disclosure_harvest_after_passions(monkeypatch):
    """Organ 4.9's harvest rides the same tick, AFTER passions (the last
    step so far); result["disclosure_harvest"] carries its result.

    Review I-2: the whole try/except block could be deleted from _run_once
    with a green suite. Fix 1's health-line test calls _surface_errors
    directly with a hand-built dict, so it proves nothing about the wiring
    — this is the sibling of test_run_once_includes_passions_step_after_
    narrative above, and it is what the brief's third deliverable needs."""
    import ai.consolidation as cons
    import ai.disclosure_harvest as harvest
    import ai.narrative_self as ns
    import ai.night_cycle as nc
    import ai.passions as passions

    calls = []

    async def fake_resolve():
        return 1

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
        return {"week": "2026-W29", "docs": {}}

    async def fake_passions_step(**kw):
        calls.append("passions")
        return {"journal": {"skipped": "flag_off"}, "interests": {"skipped": "flag_off"}}

    async def fake_harvest(**kw):
        calls.append("disclosure_harvest")
        assert kw["day"] == "2026-07-15"
        # the REAL dependency is injected, not a stub the step invented
        assert kw["llm_call"] is harvest.disclosure_harvest_llm_call
        return {"faded": 0, "planted": None}

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)
    monkeypatch.setattr(passions, "run_passions_step", fake_passions_step)
    monkeypatch.setattr(harvest, "run_nightly_disclosure_harvest", fake_harvest)

    engine = nc.NightCycleEngine()
    result = asyncio.run(engine._run_once("2026-07-15"))
    assert calls == ["reflect", "consolidation", "narrative", "passions", "disclosure_harvest"]
    assert result["disclosure_harvest"] == {"faded": 0, "planted": None}


def test_run_once_feeds_passions_the_production_now_and_date_pair(monkeypatch):
    """The pair that hid the made-thing week bug (review fix 2026-07-20).

    _run_once feeds run_passions_step the LIVE wall clock as ``now`` while
    ``date`` is the day just LIVED (now − 1 day, via _reflection_date). On the
    first 04:00 tick of an ISO week those two sit in DIFFERENT weeks — Monday
    is W31 while the page that tick writes is Sunday's, W30 — so anything
    week-shaped inside the organ must key off ``date``, never ``now``.
    Nothing asserted this pairing before, which is how a cap specified as
    once-per-ISO-week shipped able to pass twice."""
    import ai.consolidation as cons
    import ai.narrative_self as ns
    import ai.night_cycle as nc
    import ai.passions as passions

    monday_4am = datetime(2026, 7, 27, 4, 0, 0)  # first tick of ISO 2026-W31
    tick = monday_4am.timestamp()
    engine = nc.NightCycleEngine()
    reflect_date = engine._reflection_date(monday_4am)
    assert reflect_date == "2026-07-26"  # Sunday — ISO 2026-W30

    seen: dict = {}

    async def fake_resolve():
        return 1

    async def fake_gather(*, owner_id, date):
        return "material"

    async def fake_reflect(**kw):
        return {"wrote": kw["date"]}

    async def fake_consolidation(**kw):
        return {"skipped": "flag_off"}

    async def fake_ritual(**kw):
        return {"week": "2026-W31", "docs": {}}

    async def fake_passions_step(**kw):
        seen.update(kw)
        return {"journal": {"skipped": "flag_off"}, "interests": {"skipped": "flag_off"}}

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)
    monkeypatch.setattr(passions, "run_passions_step", fake_passions_step)
    monkeypatch.setattr(nc.time, "time", lambda: tick)

    asyncio.run(engine._run_once(reflect_date))

    assert seen["owner_id"] == 1
    assert seen["date"] == reflect_date  # the page's date: the day just lived
    assert seen["now"] == tick  # the tick's own wall clock
    # ...and the divergence itself, spelled out: tick week ≠ page week.
    assert datetime.fromtimestamp(seen["now"]).isocalendar()[1] == 31
    assert datetime.strptime(seen["date"], "%Y-%m-%d").isocalendar()[1] == 30


def test_run_once_passions_failure_never_costs_the_night(monkeypatch):
    """A raising run_passions_step costs nothing else — reflection,
    consolidation, and narrative results stay intact; only result["passions"]
    carries the error, same containment law as consolidation/narrative."""
    import ai.consolidation as cons
    import ai.narrative_self as ns
    import ai.night_cycle as nc
    import ai.passions as passions

    async def fake_resolve():
        return 1

    async def fake_gather(*, owner_id, date):
        return "material"

    async def fake_reflect(**kw):
        return {"wrote": kw["date"]}

    async def fake_consolidation(**kw):
        return {"skipped": "flag_off"}

    async def fake_ritual(**kw):
        return {"week": "2026-W29", "docs": {}}

    async def boom(**kw):
        raise RuntimeError("passions exploded")

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)
    monkeypatch.setattr(passions, "run_passions_step", boom)

    engine = nc.NightCycleEngine()
    result = asyncio.run(engine._run_once("2026-07-15"))
    assert result["wrote"] == "2026-07-15"
    assert result["consolidation"] == {"skipped": "flag_off"}
    assert result["narrative"] == {"week": "2026-W29", "docs": {}}
    assert result["passions"] == {"error": "passions exploded"}


# ── claim-on-success + boot catch-up (R1-durable, 2026-08-04) ───────────
#
# The engine used to claim the date BEFORE running the pass, so a failed
# 4 AM (reasoning burned the whole budget → empty reflection) marked the
# night done and it was never retried: 07-30 and 07-31 were lost exactly
# this way. The date is now claimed only when the reflection actually
# wrote, and an unclaimed night is retried at boot — the owner's frequent
# nightly restarts become the natural recovery lever.


def test_failed_pass_leaves_date_unclaimed(monkeypatch, tmp_path):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=True))
    e = NightCycleEngine(state_path=tmp_path / "state.json")

    async def fake_run_once(date):
        return {"error": "empty_reflection"}

    monkeypatch.setattr(e, "_run_once", fake_run_once)
    asyncio.run(e._attempt_pass())
    assert e._read_last_run_date() == "", "a failed night must stay unclaimed so it can retry"


def test_successful_pass_claims_date(monkeypatch, tmp_path):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=True))
    e = NightCycleEngine(state_path=tmp_path / "state.json")
    expected_date = e._reflection_date(datetime.now())

    async def fake_run_once(date):
        return {"wrote": date}

    monkeypatch.setattr(e, "_run_once", fake_run_once)
    asyncio.run(e._attempt_pass())
    assert e._read_last_run_date() == expected_date


def test_attempt_pass_skips_when_date_already_claimed(monkeypatch, tmp_path):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=True))
    e = NightCycleEngine(state_path=tmp_path / "state.json")
    e._claim_date(e._reflection_date(datetime.now()))
    calls = []

    async def fake_run_once(date):
        calls.append(date)
        return {"wrote": date}

    monkeypatch.setattr(e, "_run_once", fake_run_once)
    asyncio.run(e._attempt_pass())
    assert calls == [], "an already-claimed date must not re-run"


def test_boot_catchup_attempts_pass_before_first_wait(monkeypatch, tmp_path):
    """The tick loop must attempt a pass right after the boot settle — an
    unclaimed yesterday (failed or missed 04:00) is recovered at restart
    instead of being lost forever."""
    import ai.night_cycle as nc
    e = NightCycleEngine(state_path=tmp_path / "state.json")
    e.is_running = True
    attempts = []

    async def fake_attempt_pass():
        attempts.append(True)

    sleep_calls = {"n": 0}

    async def fake_sleep(seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:  # the wait-for-04:00 sleep: stop the loop
            raise asyncio.CancelledError

    monkeypatch.setattr(e, "_attempt_pass", fake_attempt_pass, raising=False)
    # Pin the due-check like the skip-sibling below pins its False — this
    # test ran against the REAL clock and went red every night between
    # midnight and 04:00 (caught 2026-08-08 00:10; the a38875e time-bomb
    # class). The hour logic has its own dedicated test; this one owns
    # the loop mechanics only.
    monkeypatch.setattr(e, "_catchup_due", lambda now: True)
    monkeypatch.setattr(nc.asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(e._tick_loop())
    except asyncio.CancelledError:
        pass  # loop may exit via break (body catch) or propagate from the wait
    assert attempts == [True], "boot must attempt exactly one catch-up pass"


def test_catchup_due_only_after_the_run_time():
    """A restart before the run time must NOT run the night early —
    'yesterday unclaimed' before 06:30 means 'not due yet', not 'missed'.
    (Observed live 2026-08-06 01:53: the arbiter-launch restart wrote the
    night's reflection at 01:54 instead of 04:00.) Since 2026-10-05 that
    includes 04:00-06:30: past the day boundary, but a night started then
    pays DeepSeek's peak price (the owner, 10-05)."""
    from datetime import datetime
    e = NightCycleEngine()
    assert e._catchup_due(datetime(2026, 8, 6, 1, 53)) is False
    assert e._catchup_due(datetime(2026, 8, 6, 3, 59)) is False
    assert e._catchup_due(datetime(2026, 8, 6, 4, 0)) is False
    assert e._catchup_due(datetime(2026, 8, 6, 5, 0)) is False
    assert e._catchup_due(datetime(2026, 8, 6, 6, 29)) is False
    assert e._catchup_due(datetime(2026, 8, 6, 6, 30)) is True
    assert e._catchup_due(datetime(2026, 8, 6, 9, 30)) is True
    assert e._catchup_due(datetime(2026, 8, 6, 23, 59)) is True


def _frozen_clock(monkeypatch, nc, moment):
    """Pin the engine's ``datetime.now()`` to ``moment``."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    monkeypatch.setattr(nc, "datetime", _Frozen)


def _run_loop_once(monkeypatch, nc, e):
    """Run the tick loop through the boot settle, the catch-up and the first
    wait; returns every sleep the loop asked for."""
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:  # the wait for the next run time: stop the loop
            raise asyncio.CancelledError

    monkeypatch.setattr(nc.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(e, "_report_liveness", lambda: _noop())
    monkeypatch.setattr(e, "_refresh_owner_rhythm", lambda: _noop())
    try:
        asyncio.run(e._tick_loop())
    except asyncio.CancelledError:
        pass
    return sleeps


async def _noop():
    return None


def test_a_boot_at_five_waits_for_half_past_six(monkeypatch, tmp_path):
    """The real clock check, not a pinned _catchup_due: a boot at 05:00 with
    yesterday unclaimed starts nothing, and the loop's first wait is the
    hour and a half to 06:30."""
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=True))
    _frozen_clock(monkeypatch, nc, datetime(2026, 10, 6, 5, 0, 0))
    e = NightCycleEngine(state_path=tmp_path / "state.json")
    e.is_running = True
    runs = []

    async def fake_run_once(date):
        runs.append(date)
        return {"wrote": date}

    monkeypatch.setattr(e, "_run_once", fake_run_once)
    sleeps = _run_loop_once(monkeypatch, nc, e)
    assert runs == [], "no night at 05:00: it waits for 06:30"
    assert e._read_last_run_date() == ""
    assert sleeps[0] == 30 and abs(sleeps[1] - 1.5 * 3600) < 1


def test_a_boot_at_seven_with_the_day_unclaimed_catches_up(monkeypatch, tmp_path):
    import ai.night_cycle as nc
    monkeypatch.setattr(nc, "companion_settings", lambda: SimpleNamespace(night_cycle=True))
    _frozen_clock(monkeypatch, nc, datetime(2026, 10, 6, 7, 0, 0))
    e = NightCycleEngine(state_path=tmp_path / "state.json")
    e.is_running = True
    runs = []

    async def fake_run_once(date):
        runs.append(date)
        return {"wrote": date}

    monkeypatch.setattr(e, "_run_once", fake_run_once)
    sleeps = _run_loop_once(monkeypatch, nc, e)
    assert runs == ["2026-10-05"], "the missed night runs at boot, about yesterday"
    assert e._read_last_run_date() == "2026-10-05"
    assert abs(sleeps[1] - 23.5 * 3600) < 1  # then tomorrow's 06:30


def test_a_run_at_half_past_six_reflects_on_yesterday():
    e = NightCycleEngine()
    assert e._reflection_date(datetime(2026, 10, 5, 6, 30, 0)) == "2026-10-04"
    assert e._reflection_date(datetime(2027, 1, 1, 6, 30, 0)) == "2026-12-31"
    assert e._reflection_date(datetime(2026, 10, 5, 23, 59, 0)) == "2026-10-04"


def test_boot_catchup_skipped_before_cycle_hour(monkeypatch, tmp_path):
    """Loop-level: before the run time the boot pass is skipped; the tick
    at the run time (06:30) owns it."""
    import ai.night_cycle as nc
    e = NightCycleEngine(state_path=tmp_path / "state.json")
    e.is_running = True
    attempts = []

    async def fake_attempt_pass():
        attempts.append(True)

    sleep_calls = {"n": 0}

    async def fake_sleep(seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(e, "_attempt_pass", fake_attempt_pass, raising=False)
    monkeypatch.setattr(e, "_catchup_due", lambda now: False)
    monkeypatch.setattr(nc.asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(e._tick_loop())
    except asyncio.CancelledError:
        pass
    assert attempts == [], "no early night: catch-up must defer to the 06:30 tick"


@pytest.mark.asyncio
async def test_rhythm_refresh_flag_off_writes_nothing(monkeypatch, tmp_path):
    """Byte-identity half: rhythm_refresh off => the nightly hook must not
    even import/run the learner (asserted via a call-recorder outside the
    method's own crash-guard, not a raising mock the guard would swallow),
    and no file may appear. Quiet hours ON does not reach in: since owner
    call 7 (2026-09-28) the refresh answers to its own switch alone (the
    call-7 case itself — quiet off, switch on => runs — lives in
    test_night_cycle_rhythm_gate.py). Until then this test pinned the
    reverse premise: arbiter_learned_quiet False => no-op."""
    import ai.night_cycle as nc
    import ai.owner_rhythm as owner_rhythm

    engine = nc.NightCycleEngine(state_path=tmp_path / "state.json")
    comp = types.SimpleNamespace(arbiter_learned_quiet=True, rhythm_refresh=False)
    monkeypatch.setattr(nc, "companion_settings", lambda: comp)

    calls = []
    monkeypatch.setattr(owner_rhythm, "refresh_rhythm_file", lambda **kw: calls.append(kw))
    await engine._refresh_owner_rhythm()   # must be a silent no-op
    assert calls == []


@pytest.mark.asyncio
async def test_rhythm_refresh_crash_never_propagates(monkeypatch, tmp_path):
    """The learner must never kill the night task (the presence-loop
    lesson, same shape as _report_liveness)."""
    import ai.night_cycle as nc
    import ai.owner_rhythm as owner_rhythm

    engine = nc.NightCycleEngine(state_path=tmp_path / "state.json")
    comp = types.SimpleNamespace(
        arbiter_learned_quiet=True,
        rhythm_lookback_days=56,
        rhythm_quiet_max_active_ratio=0.08,
        rhythm_min_observed_weekdays=15,
        rhythm_min_observed_weekend_days=6,
    )
    monkeypatch.setattr(nc, "companion_settings", lambda: comp)

    def _boom(**kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(owner_rhythm, "refresh_rhythm_file", _boom)
    await engine._refresh_owner_rhythm()   # must swallow and log, not raise


# ── surprise markers (organ 8, spec §4) ─────────────────────────────────

def test_day_material_marks_real_surprises(monkeypatch, tmp_path):
    """Spec §4: the night sees the day's real surprises as [a surprise…]
    lines; slight surprises and surprise-free moments mark nothing."""
    import time

    import ai.night_cycle as nc
    import ai.present_moment_store as pms

    monkeypatch.setattr(pms, "PRESENT_MOMENT_DIR", tmp_path / "pm")
    # write via the store's own writer: one real, one slight, one plain
    pms.write_moment(text="a moment", meta={"surprise": {"about": "him", "line": "up at nine", "weight": "real"}},
                     root=tmp_path / "pm")
    pms.write_moment(text="another", meta={"surprise": {"about": "me", "line": "eh", "weight": "slight"}},
                     root=tmp_path / "pm")
    pms.write_moment(text="plain", meta={}, root=tmp_path / "pm")
    day = time.strftime("%Y-%m-%d")
    entries = pms.read_day(day, root=tmp_path / "pm")
    assert len(entries) == 3
    assert (entries[0].meta or {}).get("surprise", {}).get("line") == "up at nine"
    marked = nc._surprise_markers(day, root=tmp_path / "pm")
    assert marked == ["[a surprise, about him: up at nine]"]
