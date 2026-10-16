from __future__ import annotations

import logging
import pytest


def _load():
    from tests.agent.conftest import load_module
    return load_module("core/agent/observability.py", "agent_observability_under_test")


def test_token_tracker_aggregates():
    mod = _load()
    t = mod.TokenTracker()
    t.add(prompt=100, completion=50)
    t.add(prompt=200, completion=80)
    assert t.total_prompt == 300
    assert t.total_completion == 130


def test_log_loop_start_emits_info(caplog):
    mod = _load()
    with caplog.at_level(logging.INFO, logger="core.agent.runner"):
        mod.log_loop_start(
            session_key="chan-1",
            surface="discord_text",
            turn_origin="user",
            cadence_summary="register=casual",
            tool_names=["read_file", "local_shell"],
        )
    assert any("loop_start" in rec.message for rec in caplog.records)
    assert any("chan-1" in rec.message for rec in caplog.records)


def test_log_loop_end_includes_token_totals(caplog):
    mod = _load()
    tracker = mod.TokenTracker()
    tracker.add(prompt=500, completion=300)
    with caplog.at_level(logging.INFO, logger="core.agent.runner"):
        mod.log_loop_end(
            session_key="chan-2",
            iterations=4,
            tracker=tracker,
            tool_calls=3,
            terminated_by="natural",
            latency_ms=1234,
        )
    msgs = " ".join(r.message for r in caplog.records)
    assert "loop_end" in msgs
    assert "500" in msgs
    assert "300" in msgs


def test_log_detection_trigger_critical_logs_warning(caplog):
    mod = _load()
    with caplog.at_level(logging.WARNING, logger="core.agent.detection"):
        mod.log_detection_trigger(
            session_key="chan-3",
            detector="global_circuit_breaker",
            level="critical",
            count=30,
            paired_tool_name=None,
        )
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records)
