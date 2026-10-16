"""0 means UNLIMITED on her workbench budgets (the pen's convention:
self_notes_daily_cap documents 5 scarce / 20 default / 60 generous /
0 unlimited). Before this, 0 meant 'never work' on sessions, 'silently 20
minutes' on the timeout, and was schema-rejected on the rest.

Owner deviation 2026-08-03 from the parked plan's ledger: session ROUNDS are
included in the convention (the plan had excluded them as the runaway guard).
At 0 the backstops are the runner's loop detection, the wall clock (when
nonzero), and stop. Plan: docs/superpowers/plans/2026-07-28-zero-means-unlimited-plan.md.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


def _comp(**kw):
    base = dict(own_work=True, own_work_daily_sessions=3,
                own_work_session_rounds=8, own_work_session_minutes=20,
                own_work_active_projects_max=2, own_work_min_free_disk_gb=5)
    base.update(kw)
    return SimpleNamespace(**base)


def test_schema_accepts_zero_on_every_budget_knob():
    from core.settings_schema import CompanionConfig

    c = CompanionConfig(own_work_daily_sessions=0, own_work_session_rounds=0,
                        own_work_session_minutes=0,
                        own_work_active_projects_max=0,
                        own_work_min_free_disk_gb=0)
    assert c.own_work_session_minutes == 0 and c.own_work_active_projects_max == 0
    assert c.own_work_session_rounds == 0


def test_quiet_minutes_zero_is_no_gate_now():
    """Was "deliberately OUTSIDE the convention" (ge=1: a 0 quiet window
    would mean starting private sessions mid-conversation). Superseded by
    the owner's 2026-08-18 call — her time is not rationed to his downtime,
    "she can do them whenever, even while talking to me" — so 0 = no gate
    and is the default; nonzero keeps the 08-03 behavior."""
    from core.settings_schema import CompanionConfig

    assert CompanionConfig(own_work_quiet_minutes=0).own_work_quiet_minutes == 0
    assert CompanionConfig(own_work_quiet_minutes=10).own_work_quiet_minutes == 10


def test_defaults_are_unchanged():
    from core.settings_schema import CompanionConfig

    c = CompanionConfig()
    assert (c.own_work_daily_sessions, c.own_work_session_rounds,
            c.own_work_session_minutes, c.own_work_active_projects_max,
            c.own_work_min_free_disk_gb) == (3, 8, 20, 2, 5)
    assert c.own_work_quiet_minutes == 0          # 2026-08-18


def test_zero_session_minutes_means_no_timeout():
    from ai.workbench import _session_timeout_s

    assert _session_timeout_s(_comp(own_work_session_minutes=0)) is None
    assert _session_timeout_s(_comp(own_work_session_minutes=20)) == 1200.0


def test_zero_rounds_means_unlimited_for_workbench_turns(monkeypatch):
    """The deviation: 0 rounds -> effectively-unbounded iteration budget.

    sys.maxsize (not None) on purpose — the runner resolves
    ``deps.max_iterations or _resolve_max_iterations()``, so a falsy value
    silently becomes the global 50. The sentinel must survive that ``or``.
    """
    import core.agent.dispatch as dp
    import core.turn.companion_flags as cf

    wb_turn = SimpleNamespace(surface="workbench")
    chat_turn = SimpleNamespace(surface="discord")

    monkeypatch.setattr(cf, "companion_settings",
                        lambda: _comp(own_work_session_rounds=0))
    assert dp._workbench_round_cap(wb_turn) == sys.maxsize
    assert dp._workbench_round_cap(chat_turn) is None

    monkeypatch.setattr(cf, "companion_settings",
                        lambda: _comp(own_work_session_rounds=100))
    assert dp._workbench_round_cap(wb_turn) == 100


def test_zero_active_projects_renders_no_number(tmp_path):
    from ai.workbench import _render_session_prompt

    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n")
    (tmp_path / "SUGGESTIONS.md").write_text("# Suggestions\n")
    text = _render_session_prompt(root=tmp_path, sessions_left=1, active_max=0)
    assert "at most 0" not in text
    assert "genuinely pull at you" in text
    capped = _render_session_prompt(root=tmp_path, sessions_left=2, active_max=2)
    assert "at most 2 projects going" in capped


def test_unlimited_sessions_render_as_words_not_numbers(tmp_path):
    from ai.workbench import _render_session_prompt

    (tmp_path / "WORKBENCH.md").write_text("x")
    (tmp_path / "SUGGESTIONS.md").write_text("y")
    text = _render_session_prompt(root=tmp_path, sessions_left=-1, active_max=2)
    assert "session(s) left" not in text
    assert "as many sittings as you want" in text


@pytest.mark.asyncio
async def test_zero_daily_sessions_never_blocks(monkeypatch, tmp_path):
    """The sharpest trap: 0 used to mean 'she never works again'."""
    import ai.workbench as wb
    import ai.workbench_store as ws

    monkeypatch.setattr(wb, "companion_settings",
                        lambda: _comp(own_work_daily_sessions=0))
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n")
    (tmp_path / "SUGGESTIONS.md").write_text("# Suggestions\n")
    (tmp_path / "projects").mkdir()
    store_root = tmp_path / "store"
    monkeypatch.setattr(ws, "WORKBENCH_DIR", store_root)
    for i in range(5):                        # five sessions already today
        ws.write_session(record={"started": float(i)}, root=store_root)

    dispatched = []

    async def fake_dispatch(turn):
        dispatched.append(turn)
        return SimpleNamespace(responded=True, response_text="worked.")

    async def fake_refs():
        return {"palli_user_id": 1, "app_user_id": 1}

    monkeypatch.setattr(wb, "_dispatch", fake_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", fake_refs)
    monkeypatch.setattr(wb, "_notice_own_work", lambda **kw: None)
    monkeypatch.setattr(wb, "_commit_workspace", lambda msg: True)

    out = await wb.workbench_engine.run_session_once()
    assert out.get("ran") is True and len(dispatched) == 1
