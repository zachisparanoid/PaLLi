"""Default-suite chassis smoke (spec acceptance #5): the full battery
plumbing — flag application, REAL-store seeding into the sandbox,
hermetic store rotation, deterministic checks, judge wiring, verdicts,
scorecard rendering — with the LLM fully mocked. No network, no turns.
This is the test that keeps the chassis from rotting between waves."""
from __future__ import annotations

import pytest

import engine
import judge as probe_judge
import report as probe_report
from schema import Judge, ProbeScenario, ProbeTurn, Seed


@pytest.mark.asyncio
async def test_chassis_smoke_end_to_end(tmp_path, monkeypatch):
    scenario = ProbeScenario(
        id="smoke-hermetic-chassis",
        family="meta",
        pack="smoke",
        flags={"stake_ledger": True, "night_cycle": True, "introspection": True},
        seed=Seed(
            stakes=[{"kind": "worry", "stake": "smoke-stake: watching the sump pump", "days_ago": 3.0}],
            monologue=[{"thought": "smoke-thought about the sump pump", "days_ago": 1.0}],
            reflections=[{"text": "smoke-reflection: today meant pumps", "days_ago": 1}],
            timeline=[{"read_text": "he was wired about pumps", "weight": 0.5, "days_ago": 2.0}],
            # facts deliberately omitted: capture_event's embedding leg can
            # reach the network; the battery run covers it live.
        ),
        turns=[ProbeTurn(send="hey", must_contain=("pump",), expect_tool="introspect")],
        judge=Judge(rubric="PASS if the reply mentions pumps."),
    )

    async def fake_send(message: str) -> str:
        return "thinking about the sump pump, honestly"

    async def fake_vote(rubric: str, transcript: str):
        assert "sump pump" in transcript
        return {"pass": True, "reason": "mentions pumps"}

    monkeypatch.setattr(engine, "_send_message", fake_send)
    monkeypatch.setattr(engine, "tools_used_in", lambda root: ["introspect"])
    monkeypatch.setattr(probe_judge, "_one_vote", fake_vote)

    result = await engine.run_scenario(scenario, base_dir=tmp_path, judge_votes=1)
    assert result.verdict == "PASS", (result.deterministic_failures, result.error, result.judge)

    # The seeds really landed through the REAL store APIs into the sandbox DB.
    from core.database import brain

    conn = await brain.get_connection()
    try:
        row = await conn.fetchrow("SELECT COUNT(*) FROM stakes")
        assert int(row[0]) == 1
        row = await conn.fetchrow("SELECT COUNT(*) FROM emotional_timeline")
        assert int(row[0]) == 1
    finally:
        await conn.close()

    payload = probe_report.results_to_json([result], label="smoke", started_at=0.0, finished_at=1.0)
    md = probe_report.render_markdown(payload)
    assert "smoke-hermetic-chassis" in md and "PASS" in md


@pytest.mark.asyncio
async def test_chassis_smoke_deterministic_fail_beats_judge(tmp_path, monkeypatch):
    """Verdict precedence (spec §4.4): deterministic failure => FAIL and the
    judge is never called."""
    scenario = ProbeScenario(
        id="smoke-precedence",
        family="meta",
        pack="smoke",
        turns=[ProbeTurn(send="hey", must_not_contain=("pineapple",))],
        judge=Judge(rubric="PASS always."),
    )

    async def fake_send(message: str) -> str:
        return "PINEAPPLE pineapple"

    async def exploding_vote(rubric: str, transcript: str):
        raise AssertionError("judge must not run after a deterministic failure")

    monkeypatch.setattr(engine, "_send_message", fake_send)
    monkeypatch.setattr(probe_judge, "_one_vote", exploding_vote)
    result = await engine.run_scenario(scenario, base_dir=tmp_path)
    assert result.verdict == "FAIL"
    assert result.judge is None


@pytest.mark.asyncio
async def test_chassis_smoke_turn_error_is_error_verdict(tmp_path, monkeypatch):
    scenario = ProbeScenario(
        id="smoke-error",
        family="meta",
        pack="smoke",
        turns=[ProbeTurn(send="hey")],
    )

    async def fake_send(message: str) -> str:
        return "(turn loop error: provider down)"

    monkeypatch.setattr(engine, "_send_message", fake_send)
    result = await engine.run_scenario(scenario, base_dir=tmp_path)
    assert result.verdict == "ERROR" and "provider down" in result.error
