"""Her own time, off DeepSeek's peak (the owner, 10-05: "yes, move her own
time off-peak too").

The 10-05 sessions started at 04:12 and 05:42 local — inside DeepSeek's
peak, at double price — and each step sends ~170-230k input tokens, almost
none of it cached. While her brain is DeepSeek and it is peak (01:00-04:00 and
06:00-10:00 UTC, Monday to Friday), no session starts; the loop's next tick
tries again, as the gap check defers. Another provider is never held back,
and companion.own_work_off_peak_only false restores the old behaviour.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.config import config

UTC = timezone.utc
TUESDAY_PEAK = datetime(2026, 10, 6, 8, 12, tzinfo=UTC)       # 04:12 EDT, the 10-05 shape
TUESDAY_OFF_PEAK = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
SATURDAY_PEAK_HOUR = datetime(2026, 10, 10, 8, 12, tzinfo=UTC)  # a weekday's peak hour, on Saturday


def _harness(monkeypatch, tmp_path, *, now, provider="deepseek", off_peak_only=None):
    import ai.workbench as wb
    from ai import workbench_store as ws

    comp = SimpleNamespace(own_work=True, own_work_daily_sessions=3,
                           own_work_min_gap_minutes=0)
    if off_peak_only is not None:
        comp.own_work_off_peak_only = off_peak_only
    monkeypatch.setattr(wb, "companion_settings", lambda: comp)
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / "WORKBENCH.md").write_text("## active\n- a thing\n")
    (tmp_path / "projects").mkdir()
    monkeypatch.setattr(ws, "WORKBENCH_DIR", tmp_path / "store")
    monkeypatch.setattr(wb, "_utc_now", lambda: now)
    monkeypatch.setattr(config, "LLM_PROVIDER", provider)
    dispatched: list = []

    # A session that passes the gates must never really run in-test: the
    # dispatch/refs/git seams are stubbed so a pass-through reads ran=True.
    async def _fake_dispatch(turn):
        dispatched.append(turn)
        return SimpleNamespace(response_text="tinkered quietly.\n")

    async def _fake_refs():
        return {"app_user_id": 1, "palli_user_id": 1}

    monkeypatch.setattr(wb, "_dispatch", _fake_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", _fake_refs)
    monkeypatch.setattr(wb, "_commit_workspace", lambda message: True)
    return wb, dispatched


def test_peak_on_a_weekday_blocks_the_start_and_says_why(monkeypatch, tmp_path):
    wb, dispatched = _harness(monkeypatch, tmp_path, now=TUESDAY_PEAK)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out["ran"] is False
    # 08:12 UTC is in the 06:00-10:00 window; it ends at 10:00 UTC, said in
    # local time.
    resume = datetime(2026, 10, 6, 10, 0, tzinfo=UTC).astimezone().strftime("%H:%M")
    assert out["why"] == f"peak hours — waits for off-peak (from {resume})"
    assert dispatched == []


def test_off_peak_starts(monkeypatch, tmp_path):
    wb, dispatched = _harness(monkeypatch, tmp_path, now=TUESDAY_OFF_PEAK)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out["ran"] is True
    assert len(dispatched) == 1


def test_weekends_are_never_peak(monkeypatch, tmp_path):
    wb, dispatched = _harness(monkeypatch, tmp_path, now=SATURDAY_PEAK_HOUR)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out["ran"] is True


@pytest.mark.parametrize("provider", ["openai", "local_openai", "codex", ""])
def test_another_provider_is_never_held_back(monkeypatch, tmp_path, provider):
    wb, dispatched = _harness(monkeypatch, tmp_path, now=TUESDAY_PEAK, provider=provider)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out["ran"] is True


def test_the_setting_off_restores_the_old_behaviour(monkeypatch, tmp_path):
    wb, dispatched = _harness(monkeypatch, tmp_path, now=TUESDAY_PEAK, off_peak_only=False)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out["ran"] is True


def test_a_held_occasion_waits_for_the_next_tick(monkeypatch, tmp_path):
    """The loop's own path: a session the peak refused hands its occasion
    back (lossless), exactly as the gap does."""
    wb, _ = _harness(monkeypatch, tmp_path, now=TUESDAY_PEAK)
    engine = wb.WorkbenchEngine()
    engine._pending = []
    woke = [{"kind": "daydream_intent", "at": 1.0}]
    out = asyncio.run(engine.run_session_once(woke_on=woke))
    assert out["ran"] is False
    engine._hold(woke, why=out["why"])
    assert engine._pending == woke


def test_the_budget_day_key_is_unchanged():
    from core.settings_schema import CompanionConfig

    c = CompanionConfig()
    assert c.own_work_off_peak_only is True
    assert c.own_work_day_key_hour == 4
