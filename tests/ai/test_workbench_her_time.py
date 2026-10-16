"""Her time is hers (item 5c, 2026-08-18).

Owner: "there shouldn't be dedicated time or only during downtime … she can
do them whenever, since she's a bot she can technically do them even while
talking to me. All of the above, one at a time." So: the conversation-quiet
gate is off by default (own_work_quiet_minutes 0); sessions are OCCASIONED
— a daydream left her a line, the owner touched SUGGESTIONS.md, a hand-run probe
— with a slow heartbeat under them (own_work_heartbeat_minutes, 0 = only
occasions); the session prompt says why she sat down; and her own-time
activities (a session, a dive) hold one lock — one thing at a time. The
daily cap and the gap stay the real bounds; he still preempts nothing here
because his chat runs on its own session key.

Every test pins its own flags (3352e3d)."""
from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import ai.workbench as wb


def _comp(**kw):
    base = dict(own_work=True, own_work_daily_sessions=3, own_work_session_rounds=8,
                own_work_session_minutes=20, own_work_active_projects_max=2,
                own_work_min_free_disk_gb=0, own_work_quiet_minutes=0,
                own_work_heartbeat_minutes=480, own_work_min_gap_minutes=90)
    base.update(kw)
    return SimpleNamespace(**base)


def _engine(comp):
    eng = wb.WorkbenchEngine()
    eng.is_running = True
    p = patch.object(wb.WorkbenchEngine, "_settings", lambda self: comp)
    p.start()
    return eng, p


def test_quiet_gate_off_by_default_and_schema_allows_zero():
    from core.settings_schema import CompanionConfig
    c = CompanionConfig()
    assert c.own_work_quiet_minutes == 0
    assert c.own_work_heartbeat_minutes == 480
    assert CompanionConfig(own_work_quiet_minutes=10).own_work_quiet_minutes == 10
    assert CompanionConfig(own_work_heartbeat_minutes=0).own_work_heartbeat_minutes == 0


@pytest.mark.asyncio
async def test_no_quiet_gate_means_eligible_even_mid_conversation():
    eng, p = _engine(_comp(own_work_quiet_minutes=0))
    try:
        # a message from him 5 seconds ago — she may still sit down
        async def refs():
            return {"palli_user_id": 1}
        rows = [{"timestamp": time.time() - 5}]
        with patch.object(wb, "_get_owner_refs", refs), \
             patch("core.database.brain.get_conversation_history", return_value=rows, create=True):
            assert await eng._eligible(time.time()) is True
    finally:
        p.stop()


def test_occasion_wakes_and_heartbeat_fires_only_in_quiet():
    eng, p = _engine(_comp(own_work_heartbeat_minutes=60))
    try:
        eng._last_heartbeat_at = time.time()
        fire, woke = eng._decide(time.time())
        assert fire is False and woke == []
        eng._ingest("daydream_intent", {})
        fire, woke = eng._decide(time.time())
        assert fire is True and [w["kind"] for w in woke] == ["daydream_intent"]
        assert eng._pending == []
        eng._last_heartbeat_at = time.time() - 61 * 60
        fire, woke = eng._decide(time.time())
        assert fire is True and woke[0]["kind"] == "heartbeat"
    finally:
        p.stop()


def test_heartbeat_zero_means_occasions_only():
    eng, p = _engine(_comp(own_work_heartbeat_minutes=0))
    try:
        eng._last_heartbeat_at = 0.0
        fire, woke = eng._decide(time.time())
        assert fire is False
        eng._ingest("manual", {})
        fire, woke = eng._decide(time.time())
        assert fire is True and woke[0]["kind"] == "manual"
    finally:
        p.stop()


def test_notice_is_safe_when_engine_down():
    wb.workbench_engine.is_running = False
    wb.notice("daydream_intent")
    assert wb.workbench_engine._pending == []


def test_suggestions_mtime_is_an_occasion(tmp_path):
    eng, p = _engine(_comp())
    try:
        sug = tmp_path / "SUGGESTIONS.md"
        sug.write_text("# Suggestions\n", encoding="utf-8")
        with patch.object(wb, "WORKSPACE_ROOT", tmp_path):
            eng._poll_suggestions_mtime()          # first sight: baseline
            assert eng._pending == []
            sug.write_text("# Suggestions\n- maybe birds?\n", encoding="utf-8")
            base = sug.stat().st_mtime
            os.utime(sug, (base + 2, base + 2))
            eng._poll_suggestions_mtime()
            assert [e["kind"] for e in eng._pending] == ["suggestion_changed"]
    finally:
        p.stop()


def test_a_session_that_could_not_run_holds_its_occasions():
    """Budget/gap said no → the occasion is not lost (the PM lossless law)."""
    eng, p = _engine(_comp())
    try:
        eng._ingest("daydream_intent", {})
        fire, woke = eng._decide(time.time())
        assert fire is True and eng._pending == []
        eng._hold(woke, why="session gap (10m < 90m)")
        assert [e["kind"] for e in eng._pending] == ["daydream_intent"]
    finally:
        p.stop()


def test_occasion_line_tells_her_why_she_sat_down():
    line = wb._occasion_line([{"kind": "daydream_intent"}, {"kind": "suggestion_changed"}])
    assert "daydream" in line and "SUGGESTIONS" in line
    assert "because" in line.lower()
    assert wb._occasion_line([{"kind": "heartbeat"}]).strip() != ""
    assert wb._occasion_line([]) == ""


def test_session_prompt_carries_the_occasion(tmp_path, monkeypatch):
    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n", encoding="utf-8")
    (tmp_path / "SUGGESTIONS.md").write_text("# Suggestions\n", encoding="utf-8")
    monkeypatch.setattr(wb, "companion_settings", lambda: _comp())
    text = wb._render_session_prompt(root=tmp_path, sessions_left=2, active_max=2,
                                     woke_on=[{"kind": "suggestion_changed"}])
    assert "SUGGESTIONS" in text and "because" in text.lower()


@pytest.mark.asyncio
async def test_her_time_is_one_thing_at_a_time(monkeypatch, tmp_path):
    """A running dive holds her time; the session waits its turn (the
    occasion is held, not lost); when the dive ends the session runs."""
    from ai import her_time
    from ai import workbench_store as ws
    monkeypatch.setattr(wb, "companion_settings", lambda: _comp())
    monkeypatch.setattr(wb, "WORKSPACE_ROOT", tmp_path)
    (tmp_path / "WORKBENCH.md").write_text("# Workbench\n"); (tmp_path / "SUGGESTIONS.md").write_text("")
    (tmp_path / "projects").mkdir()
    monkeypatch.setattr(ws, "WORKBENCH_DIR", tmp_path / "store")
    import ai.inner_monologue_store as ims
    monkeypatch.setattr(ims, "MONOLOGUE_DIR", tmp_path / "mono")

    async def fake_dispatch(turn):
        return SimpleNamespace(responded=True, response_text="done.")
    async def refs():
        return {"palli_user_id": 1, "app_user_id": 1}
    monkeypatch.setattr(wb, "_dispatch", fake_dispatch)
    monkeypatch.setattr(wb, "_get_owner_refs", refs)
    monkeypatch.setattr(wb, "_notice_own_work", lambda **kw: None)
    monkeypatch.setattr(wb, "_notice_daydream", lambda **kw: None)
    monkeypatch.setattr(wb, "_commit_workspace", lambda msg: True)

    her_time.reset_for_tests()
    async with her_time.hold("a curiosity dive"):
        assert her_time.busy() == "a curiosity dive"
        result = await wb.workbench_engine.run_session_once()
        assert result["ran"] is False and "dive" in result["why"]
    assert her_time.busy() is None
    result = await wb.workbench_engine.run_session_once()
    assert result["ran"] is True


def test_daydream_intent_is_a_workbench_occasion(tmp_path, monkeypatch):
    """The daydream tick that leaves her a line also sits her down (the
    chain: dive → daydream → intent → session)."""
    import json
    import ai.daydream as dd
    import ai.daydream_store as dds
    noticed = []
    monkeypatch.setattr(dds, "DAYDREAM_DIR", tmp_path / "daydream")
    monkeypatch.setattr(wb, "notice", lambda kind, **f: noticed.append(kind))

    async def fake_llm(rendered):
        return json.dumps({"daydream": "next: the poem", "quest_ops": [],
                           "curiosity_seed": "", "workbench_intent": "pick the poem back up"})

    async def fake_prompt(woke_on):
        return "prompt"

    with patch.object(dd, "_llm_call", fake_llm), \
         patch.object(dd, "_assemble_prompt", fake_prompt), \
         patch("ai.quest_store.sync_wishes_file", return_value={}):
        asyncio.run(dd.run_daydream_tick([{"kind": "heartbeat"}], owner_user_id=1))
    assert noticed == ["daydream_intent"]


@pytest.mark.asyncio
async def test_a_dive_holds_her_time(monkeypatch):
    from ai import her_time
    import ai.curiosity_research as cr
    her_time.reset_for_tests()
    seen = {}

    class _Exa:
        async def search(self, **kw):
            seen["busy"] = her_time.busy()
            return {"results": []}          # "no results; letting it go" → returns None early
    monkeypatch.setattr(cr, "_exa_client", lambda: _Exa())
    await cr.research_curiosity(topic="the shape of stillness", owner_user_id=1)
    assert seen["busy"] == "a curiosity dive"
    assert her_time.busy() is None
