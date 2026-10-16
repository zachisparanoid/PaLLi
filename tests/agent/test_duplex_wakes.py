"""Step 27 Phase A — the two senses: task_problem and task_overrun."""
from __future__ import annotations

import asyncio

import pytest


def test_phrases_render_as_lived_facts():
    from ai.present_moment import _humanize_events
    text = _humanize_events([{"kind": "task_problem"}, {"kind": "task_overrun"}])
    assert "hit a snag" in text
    assert "running past what you expected" in text


def test_overrun_watch_fires_at_factor_then_each_multiple(monkeypatch):
    from core.agent import duplex as dx
    import core.agent.steering as st
    import ai.present_moment as pm
    st.clear_run_state("discord:7")
    st.set_expectation("discord:7", 10.0)          # ten minutes, factor 1.5 → 15 min
    fired: list[dict] = []
    monkeypatch.setattr(pm, "notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    clock = {"t": 0.0}

    async def fake_sleep(s):
        clock["t"] += s
        if clock["t"] > 40 * 60:
            raise asyncio.CancelledError   # the runner's finally

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dx.overrun_watch("discord:7", started_at=0.0, factor=1.5,
                                     default_minutes=99, now=lambda: clock["t"],
                                     sleep=fake_sleep, tick_seconds=60.0))
    assert [f["multiple"] for f in fired] == [1, 2]      # 15 min and 30 min; 45 never came
    assert fired[0]["kind"] == "task_overrun" and fired[0]["expected_min"] == 10.0


def test_overrun_watch_uses_default_when_she_set_no_estimate(monkeypatch):
    from core.agent import duplex as dx
    import core.agent.steering as st
    import ai.present_moment as pm
    st.clear_run_state("discord:8")
    fired: list[dict] = []
    monkeypatch.setattr(pm, "notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    clock = {"t": 0.0}

    async def fake_sleep(s):
        clock["t"] += s
        if clock["t"] > 20 * 60:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dx.overrun_watch("discord:8", started_at=0.0, factor=1.5,
                                     default_minutes=10, now=lambda: clock["t"],
                                     sleep=fake_sleep, tick_seconds=60.0))
    assert len(fired) == 1 and fired[0]["expected_min"] == 10
