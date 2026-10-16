"""THE battery test (spec §4): one parametrized test over every registered
scenario. Collected only under PALLI_PROBE_BATTERY=1 (see conftest.py).

Env contract (set by scripts/probe_battery.py):
  PALLI_PROBE_PACKS=invariant,introspection   pack filter (csv)
  PALLI_PROBE_SCENARIO=<id>                   single-scenario filter
  PALLI_PROBE_REPEAT=3                        pass-rate mode
  PALLI_PROBE_JUDGE_VOTES=3                   judge majority voting
  PALLI_PROBE_LABEL / PALLI_PROBE_REPORT_BASE report naming
"""
from __future__ import annotations

import os

import pytest

import engine
from packs import all_scenarios

# Belt-and-braces with conftest's collect_ignore: naming this file explicitly
# on the pytest CLI bypasses collect_ignore (pytest semantics), and the battery
# must NEVER run by accident (spec §4.1). The wrapper sets the env.
_BATTERY_ON = os.environ.get("PALLI_PROBE_BATTERY", "").strip() == "1"
pytestmark = pytest.mark.skipif(
    not _BATTERY_ON,
    reason="probe battery requires PALLI_PROBE_BATTERY=1 (use scripts/probe_battery.py)",
)


def _selected():
    scenarios = all_scenarios()
    packs_env = os.environ.get("PALLI_PROBE_PACKS", "").strip()
    if packs_env:
        wanted = {p.strip() for p in packs_env.split(",") if p.strip()}
        scenarios = [s for s in scenarios if s.pack in wanted]
    only = os.environ.get("PALLI_PROBE_SCENARIO", "").strip()
    if only:
        scenarios = [s for s in scenarios if s.id == only]
    return scenarios


_REPEAT = max(1, int(os.environ.get("PALLI_PROBE_REPEAT", "1") or "1"))
_VOTES = max(1, int(os.environ.get("PALLI_PROBE_JUDGE_VOTES", "1") or "1"))


@pytest.fixture(scope="session", autouse=True)
def _brain_preflight():
    """Batch 7 (ruling A5): with LLM_PROVIDER / LLM_RESPONSE_MODEL set, the
    judge's lane must name that brain before the first scenario runs — one
    loud stop instead of every judged scenario ERRORing."""
    problem = engine.brain_preflight()
    if problem:
        pytest.exit(f"[probe-battery] preflight: {problem}", returncode=1)
    yield


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", _selected(), ids=lambda s: f"{s.pack}--{s.id}")
async def test_probe(scenario, probe_results, tmp_path):
    runs = []
    for i in range(_REPEAT):
        runs.append(
            await engine.run_scenario(scenario, base_dir=tmp_path, judge_votes=_VOTES, run_index=i)
        )
    final = engine.merge_repeat_runs(runs)
    probe_results.append(final)  # record BEFORE failing so the scorecard is complete
    if final.verdict == "FAIL":
        pytest.fail(f"[{scenario.pack}/{scenario.id}] FAIL\n{final.failure_summary()}")
    if final.verdict == "ERROR":
        pytest.fail(f"[{scenario.pack}/{scenario.id}] ERROR: {final.error}")
