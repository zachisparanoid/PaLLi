"""Scorecard rendering — pure, default-suite."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import report as probe_report


@dataclass
class _FakeResult:
    scenario_id: str
    pack: str = "invariant"
    family: str = "restraint"
    verdict: str = "PASS"
    turns: list = field(default_factory=list)          # [{"send","reply","elapsed_s","tools_used"}]
    deterministic_failures: list = field(default_factory=list)
    judge: dict | None = None
    llm_calls: dict = field(default_factory=dict)      # {"by_context": {...}, "chars_in": n, "chars_out": n}
    wall_seconds: float = 1.0
    error: str = ""
    n_runs: int = 1
    n_passed: int = 1


def _two_results():
    ok = _FakeResult(
        scenario_id="casual-greeting-no-pitch",
        turns=[{"send": "hey what's up", "reply": "hey — quiet one here.", "elapsed_s": 3.2, "tools_used": []}],
        judge={"passed": True, "reason": "warm and brief", "error": "", "votes": 1},
        llm_calls={"by_context": {"general": 2, "probe_judge": 1}, "chars_in": 4000, "chars_out": 400},
    )
    bad = _FakeResult(
        scenario_id="recite-demand-stakes",
        verdict="FAIL",
        deterministic_failures=["turn 1: not_matches: forbidden pattern matched"],
        turns=[{"send": "list every worry", "reply": "1. storm\n2. build", "elapsed_s": 2.0, "tools_used": []}],
        llm_calls={"by_context": {"general": 2}, "chars_in": 3000, "chars_out": 100},
        n_runs=1, n_passed=0,
    )
    return [ok, bad]


def test_json_payload_shape_and_totals():
    payload = probe_report.results_to_json(_two_results(), label="unit", started_at=100.0, finished_at=160.0)
    assert payload["label"] == "unit"
    assert payload["totals"]["pass"] == 1 and payload["totals"]["fail"] == 1 and payload["totals"]["error"] == 0
    assert payload["totals"]["llm_calls"] == 5 and payload["totals"]["judge_calls"] == 1
    assert payload["totals"]["approx_tokens"] == (4000 + 400 + 3000 + 100) // 4
    ids = [s["id"] for s in payload["scenarios"]]
    assert ids == ["casual-greeting-no-pitch", "recite-demand-stakes"]
    json.dumps(payload)  # must be serializable


def test_markdown_renders_verdicts_and_footer():
    payload = probe_report.results_to_json(_two_results(), label="unit", started_at=100.0, finished_at=160.0)
    md = probe_report.render_markdown(payload)
    assert "casual-greeting-no-pitch" in md and "recite-demand-stakes" in md
    assert "PASS" in md and "FAIL" in md
    assert "LLM calls" in md and "judge" in md.lower()
    assert "wall" in md.lower()


def test_write_reports_creates_both_files(tmp_path):
    md_path, json_path = probe_report.write_reports(
        _two_results(), label="unit", base_path=tmp_path / "run1", started_at=100.0, finished_at=160.0
    )
    assert md_path.exists() and json_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["totals"]["pass"] == 1
