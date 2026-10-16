"""Engine pure-logic units: transcript tool parsing, flag validation,
repeat merging. The app-touching path is exercised by test_chassis_smoke."""
from __future__ import annotations

import json

import pytest

import engine
from engine import ScenarioResult


def test_tools_used_in_parses_tool_use_blocks(tmp_path):
    session = tmp_path / "dashboard_1.jsonl"
    lines = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}], "timestamp": "t"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "introspect", "input": {"query": "x"}},
            {"type": "text", "text": "checked"},
        ], "timestamp": "t"},
        {"role": "tool", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok", "is_error": False}], "timestamp": "t"},
    ]
    session.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
    assert engine.tools_used_in(tmp_path) == ["introspect"]


def test_tools_used_in_tolerates_garbage_lines(tmp_path):
    (tmp_path / "x.jsonl").write_text("not json\n{\"role\": \"user\"}\n", encoding="utf-8")
    assert engine.tools_used_in(tmp_path) == []


def test_resolve_flags_defaults_and_unknown_key():
    merged = engine.resolve_flags({"night_cycle": False})
    assert merged["night_cycle"] is False and merged["stake_ledger"] is True
    with pytest.raises(AssertionError):
        engine.resolve_flags({"totally_unknown_flag": True})


def _res(verdict: str) -> ScenarioResult:
    return ScenarioResult(scenario_id="s", pack="p", family="f", verdict=verdict)


def test_merge_repeat_runs_precedence_and_rate():
    merged = engine.merge_repeat_runs([_res("PASS"), _res("FAIL"), _res("PASS")])
    assert merged.verdict == "FAIL" and merged.n_runs == 3 and merged.n_passed == 2
    merged2 = engine.merge_repeat_runs([_res("PASS"), _res("ERROR")])
    assert merged2.verdict == "ERROR" and merged2.n_passed == 1
    merged3 = engine.merge_repeat_runs([_res("PASS")])
    assert merged3.verdict == "PASS" and merged3.n_runs == 1
