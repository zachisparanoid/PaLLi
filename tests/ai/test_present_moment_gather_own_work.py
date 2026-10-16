"""The moment's own-work source — the authorship trace since 2026-08-14.

Originally one capped HEAD-read of WORKBENCH.md: text[:600] of an
append-only file showed her July-28 hello-world forever while her real
hands built unseen (the three-doors inversion's quiet accomplice). The
sense now renders the RECENT logbook entries with authorship framing via
ai.workbench_trace; gating (companion.own_work, via this module's
`_companion()` helper) and the absent-not-erroring contract are
unchanged. Renderer behavior itself is pinned in test_workbench_trace.py.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest


def _today_log(tmp_path):
    p = tmp_path / "WORKBENCH.md"
    p.write_text(
        f"## {date.today().isoformat()} — 4 AM session\n"
        "- **the-essay/** — stuck on a good question, and keeping it. "
        "Committed as abc1234.\n",
        encoding="utf-8")
    return p


def test_own_work_line_is_the_authorship_trace(monkeypatch, tmp_path):
    import ai.present_moment_gather as g
    import ai.workbench_trace as wt

    monkeypatch.setattr(wt, "WORKBENCH_MD", _today_log(tmp_path))
    monkeypatch.setattr(g, "_companion", lambda: SimpleNamespace(own_work=True))
    line = g._own_work_line()
    assert "YOUR OWN HANDS" in line          # the authorship framing
    assert "the-essay" in line
    assert "(committed abc1234)" in line


def test_own_work_line_empty_when_flag_off(monkeypatch, tmp_path):
    import ai.present_moment_gather as g
    import ai.workbench_trace as wt

    monkeypatch.setattr(wt, "WORKBENCH_MD", _today_log(tmp_path))
    monkeypatch.setattr(g, "_companion", lambda: SimpleNamespace(own_work=False))
    assert g._own_work_line() == ""


def test_first_session_placeholder_is_not_a_sense(monkeypatch, tmp_path):
    """The seeded workspace says "(nothing here yet — nothing is owed)".
    That is the absence of work, not a fact about her present; under the
    trace it has no dated entries and renders as silence."""
    import ai.present_moment_gather as g
    import ai.workbench_trace as wt

    p = tmp_path / "WORKBENCH.md"
    p.write_text("# Workbench\n\n(nothing here yet — nothing is owed)\n",
                 encoding="utf-8")
    monkeypatch.setattr(wt, "WORKBENCH_MD", p)
    monkeypatch.setattr(g, "_companion", lambda: SimpleNamespace(own_work=True))
    assert g._own_work_line() == ""


@pytest.mark.asyncio
async def test_own_work_actually_joins_the_gather(monkeypatch):
    """A source that exists but is never assembled is a silent no-op. Every
    source records ok/dark in sense_health, so the key's presence proves the
    wire, whatever the flag says."""
    import ai.present_moment_gather as g

    out = await g.gather_moment_inputs(owner_user_id=1)
    assert "own_work" in out.sense_health
