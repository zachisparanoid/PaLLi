"""recall's read of the authorship trace for the reply band (2026-08-16):
gated on companion.own_work, fail-closed on unbootable settings,
best-effort on any error, and it flows through the RecallContext field to
the agent-loop context adapter."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.turn.recall as recall


def test_field_exists_default_empty():
    from core.turn.types import TurnContext

    assert TurnContext.__dataclass_fields__["own_work_trace"].default == ""


def test_trace_dark_when_flag_off(monkeypatch):
    monkeypatch.setattr(recall, "companion_settings",
                        lambda: SimpleNamespace(own_work=False))
    assert recall._read_own_work_trace() == ""


def test_trace_dark_when_settings_unbootable(monkeypatch):
    monkeypatch.setattr(recall, "companion_settings", lambda: None)
    assert recall._read_own_work_trace() == ""


def test_trace_renders_when_on(monkeypatch, tmp_path):
    from datetime import date

    import ai.workbench_trace as wt

    p = tmp_path / "WORKBENCH.md"
    p.write_text(
        f"## {date.today().isoformat()} — 4 AM session\n"
        "- **claw/** — a small machine with something inside. Committed as 91aa314.\n",
        encoding="utf-8")
    monkeypatch.setattr(wt, "WORKBENCH_MD", p)
    monkeypatch.setattr(recall, "companion_settings",
                        lambda: SimpleNamespace(own_work=True))
    out = recall._read_own_work_trace()
    assert "claw/" in out and "YOUR OWN HANDS" in out


def test_trace_empty_when_reader_raises(monkeypatch):
    import ai.workbench_trace as wt

    def boom(**kw):
        raise RuntimeError("disk")
    monkeypatch.setattr(wt, "build_authorship_trace", boom)
    monkeypatch.setattr(recall, "companion_settings",
                        lambda: SimpleNamespace(own_work=True))
    assert recall._read_own_work_trace() == ""


def test_adapter_carries_the_trace():
    from core.agent.context_adapter import AgentLoopContext

    assert AgentLoopContext.__dataclass_fields__["own_work_trace_text"].default == ""
