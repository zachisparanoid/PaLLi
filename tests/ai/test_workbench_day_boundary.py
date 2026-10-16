"""The workbench day boundary (2026-08-06): her session budget's "day" turns
at a boundary hour (default 04:00, matching the night cycle) instead of
midnight. A nocturnal owner's quiet happens right after midnight, so a
midnight refill meant she spent all three sessions by 1 AM and had nothing
for twenty-three hours of daytime quiet. 0 = legacy midnight semantics.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from types import SimpleNamespace

from ai.workbench import _budget_window_start
from ai import workbench_store


def test_window_start_after_boundary_is_today():
    assert _budget_window_start(datetime(2026, 8, 6, 12, 19), 4) == datetime(2026, 8, 6, 4, 0)


def test_window_start_before_boundary_is_yesterday():
    assert _budget_window_start(datetime(2026, 8, 6, 1, 30), 4) == datetime(2026, 8, 5, 4, 0)
    assert _budget_window_start(datetime(2026, 8, 6, 3, 59), 4) == datetime(2026, 8, 5, 4, 0)


def test_window_start_zero_means_legacy_midnight():
    assert _budget_window_start(datetime(2026, 8, 6, 12, 19), 0) is None


def test_count_since_spans_calendar_files(tmp_path, monkeypatch):
    """A midnight burst lives in yesterday's file AND today's; the window
    counter must see both.

    The store's "today" is frozen to the fixture's world: count_since reads
    {since-day, today} only (correct in production, where the 04:00 window
    start is never more than a day back), so with a REAL clock this test
    silently expired at midnight 2026-08-07 — the a38875e lesson again,
    from the other side: fixtures must not encode "today"."""
    frozen = datetime(2026, 8, 6, 1, 30).timestamp()
    monkeypatch.setattr(
        workbench_store, "time",
        SimpleNamespace(time=lambda: frozen,
                        strftime=time.strftime, localtime=time.localtime))
    day1, day2 = "2026-08-05", "2026-08-06"
    base = datetime(2026, 8, 5, 23, 30).timestamp()
    rows1 = [{"started": base, "outcome": "ran"}]                     # 23:30 Aug 5
    rows2 = [{"started": base + 3600, "outcome": "ran"},              # 00:30 Aug 6
             {"started": base + 5400, "outcome": "ran"}]              # 01:00 Aug 6
    (tmp_path / f"{day1}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows1) + "\n", encoding="utf-8")
    (tmp_path / f"{day2}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows2) + "\n", encoding="utf-8")

    since_2300 = datetime(2026, 8, 5, 23, 0).timestamp()
    assert workbench_store.count_since(since_2300, root=tmp_path) == 3
    since_midnight = datetime(2026, 8, 6, 0, 0).timestamp()
    assert workbench_store.count_since(since_midnight, root=tmp_path) == 2


def test_schema_default_is_4_and_bounded():
    from core.settings_schema import CompanionConfig
    c = CompanionConfig()
    assert c.own_work_day_key_hour == 4


# ── session spacing (impulse-freshness rider, 2026-08-07): the quiet gate
# alone let three sessions burn in 33 minutes at 4 AM — her own sign-off
# called the third "noise itself." 0 = no gap (a gap is budget-shaped). ──

def _gap_harness(monkeypatch, tmp_path, *, gap_minutes, last_started_ago_s):
    import ai.workbench as wb
    from ai import workbench_store as ws

    comp = SimpleNamespace(own_work=True, own_work_daily_sessions=3,
                           own_work_min_gap_minutes=gap_minutes)
    monkeypatch.setattr(wb, "companion_settings", lambda: comp)
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / "WORKBENCH.md").write_text("## active\n- a thing\n")
    (tmp_path / "projects").mkdir()
    store = tmp_path / "store"
    monkeypatch.setattr(ws, "WORKBENCH_DIR", store)
    if last_started_ago_s is not None:
        ws.write_session(record={"started": time.time() - last_started_ago_s})

    # Beyond the gap, the session must never actually run in-test: stub the
    # dispatch/refs/git seams so a pass-through is visible as ran=True.
    async def _fake_dispatch(turn):
        return SimpleNamespace(response_text="tinkered quietly.\n")

    async def _fake_refs():
        return {"app_user_id": 1, "palli_user_id": 1}

    monkeypatch.setattr(wb, "_dispatch", _fake_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", _fake_refs)
    monkeypatch.setattr(wb, "_commit_workspace", lambda message: True)
    return wb


def test_min_gap_blocks_inside_window(monkeypatch, tmp_path):
    wb = _gap_harness(monkeypatch, tmp_path,
                      gap_minutes=90, last_started_ago_s=20 * 60)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out.get("ran") is False
    assert "gap" in str(out.get("why", ""))


def test_min_gap_allows_at_or_after_window(monkeypatch, tmp_path):
    wb = _gap_harness(monkeypatch, tmp_path,
                      gap_minutes=90, last_started_ago_s=95 * 60)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out.get("ran") is True


def test_min_gap_zero_disables(monkeypatch, tmp_path):
    wb = _gap_harness(monkeypatch, tmp_path,
                      gap_minutes=0, last_started_ago_s=60)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out.get("ran") is True


def test_min_gap_no_prior_session_no_gap(monkeypatch, tmp_path):
    wb = _gap_harness(monkeypatch, tmp_path,
                      gap_minutes=90, last_started_ago_s=None)
    out = asyncio.run(wb.workbench_engine.run_session_once())
    assert out.get("ran") is True


def test_min_gap_schema_default_is_90():
    from core.settings_schema import CompanionConfig
    assert CompanionConfig().own_work_min_gap_minutes == 90
