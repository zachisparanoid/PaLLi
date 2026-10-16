"""Step 27 Phase A — chime_in / ask_him: acts, not leaks."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


def _ctx():
    from core.agent.tools import ToolContext
    return ToolContext(turn_owner_id=1, is_owner=True, surface="discord_text", channel_id=555)


def test_chime_delivers_through_the_release_leg(monkeypatch):
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import ChimeInTool
    sent: list[dict] = []

    async def fake_speak(**kw):
        sent.append(kw)
        return {"medium": "text"}
    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    out = asyncio.run(ChimeInTool().execute({"text": "found the fan's part number — it's a 9225"}, _ctx()))
    assert sent == [{"owner_user_id": 1, "channel_id": 555,
                     "text": "found the fan's part number — it's a 9225", "origin": "duplex_chime"}]
    assert out.startswith("Said")


def test_chime_over_cap_is_declined_without_sending(monkeypatch):
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import ChimeInTool
    sent: list[dict] = []

    async def fake_speak(**kw):
        sent.append(kw)
        return {"medium": "text"}
    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    out = asyncio.run(ChimeInTool().execute({"text": "x" * (dx.CHIME_MAX_CHARS + 1)}, _ctx()))
    assert sent == [] and "300" in out


def test_ask_him_delivers_raises_task_problem_and_returns_at_once(monkeypatch):
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import AskHimTool
    import ai.present_moment as pm
    sent: list[dict] = []
    fired: list[dict] = []

    async def fake_speak(**kw):
        sent.append(kw)
        return {"medium": "voice"}
    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    monkeypatch.setattr(pm, "notice", lambda kind, **f: fired.append({"kind": kind, **f}))
    out = asyncio.run(AskHimTool().execute({"question": "there's a captcha — can you clear it?"}, _ctx()))
    assert sent[0]["origin"] == "duplex_ask"
    assert fired == [{"kind": "task_problem", "what": "asked_him"}]
    assert "Asked" in out and "your call" in out


def test_speak_mid_task_rides_the_arbiter_release_leg(monkeypatch):
    from core.agent import duplex as dx
    import ai.arbiter as arb
    seen: dict = {}

    async def fake_probe():
        return None

    async def fake_release(**kw):
        seen.update(kw)
        return {"medium": "text"}
    monkeypatch.setattr(arb, "_probe_voice_moment", fake_probe)
    monkeypatch.setattr(arb, "_release_outbound", fake_release)
    out = asyncio.run(dx.speak_mid_task(owner_user_id=1, channel_id=555, text="hey", origin="duplex_chime"))
    assert out == {"medium": "text"}
    assert seen["origin"] == "duplex_chime" and seen["extra"] == {"mid_task": True} and seen["vm"] is None


def test_registered_only_when_duplex_on_and_never_at_the_workbench(monkeypatch):
    import core.agent.builtin_tools as bt
    names_off = set(bt.build_default_registry(surface="discord_text").all_names())
    assert "chime_in" not in names_off and "ask_him" not in names_off
    monkeypatch.setattr(bt, "_duplex_enabled", lambda: True)
    names_on = set(bt.build_default_registry(surface="discord_text").all_names())
    assert {"chime_in", "ask_him"} <= names_on
    wb = set(bt._build_workbench_registry().all_names())
    assert "chime_in" not in wb and "ask_him" not in wb


def test_tool_deeds_know_the_two_hands(tmp_path):
    from ai.recent_deeds import record_tool_deed, read_recent_deeds
    record_tool_deed("chime_in", {"text": "found it"}, root=tmp_path)
    record_tool_deed("ask_him", {"question": "captcha?"}, root=tmp_path)
    details = [d["detail"] for d in read_recent_deeds(root=tmp_path, limit=20)]
    assert any(x.startswith("said mid-task: found it") for x in details)
    assert any(x.startswith("asked them mid-task: captcha?") for x in details)


def test_ask_him_parks_a_followup_thread(monkeypatch):
    """Phase B (spec §2, his call 1): the blocked step waits on him — park it
    on her thread board so it resurfaces if he answers after the run ended."""
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import AskHimTool
    from core.database import brain
    import ai.present_moment as pm

    async def fake_speak(**kw): return {"medium": "text"}
    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    monkeypatch.setattr(pm, "notice", lambda kind, **f: None)
    planted = []

    async def fake_add(**kw):
        planted.append(kw); return "thread-1"
    monkeypatch.setattr(brain, "add_open_thread", fake_add)
    monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
    asyncio.run(AskHimTool().execute({"question": "there's a captcha — can you clear it?"}, _ctx()))
    [p] = planted
    assert p["owner_user_id"] == 1 and p["kind"] == "followup" and p["source_id"] == "task:discord:555"
    assert "captcha" in p["topic_summary"] and p["urgency"] in ("normal", "medium", "low", "high")


def test_ask_him_parks_nothing_when_off(monkeypatch):
    from core.agent import duplex as dx
    from core.agent.builtin_tools.duplex_hands import AskHimTool
    from core.database import brain
    import ai.present_moment as pm

    async def fake_speak(**kw): return {"medium": "text"}
    monkeypatch.setattr(dx, "speak_mid_task", fake_speak)
    monkeypatch.setattr(pm, "notice", lambda kind, **f: None)
    planted = []

    async def fake_add(**kw): planted.append(kw)
    monkeypatch.setattr(brain, "add_open_thread", fake_add)
    asyncio.run(AskHimTool().execute({"question": "x?"}, _ctx()))
    assert planted == []


def test_duplex_hands_enabled_follows_the_duplex_gate_at_call_time(monkeypatch):
    """R15, T7: the registry's duplex gate reads through duplex_hands.enabled(),
    which asks core.agent.duplex.duplex_enabled() each time it is called."""
    from core.agent import duplex as dx
    from core.agent.builtin_tools import duplex_hands
    monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
    assert duplex_hands.enabled() is True
    monkeypatch.setattr(dx, "duplex_enabled", lambda: False)
    assert duplex_hands.enabled() is False


def test_duplex_hands_enabled_fails_closed(monkeypatch):
    from core.agent import duplex as dx
    from core.agent.builtin_tools import duplex_hands

    def boom():
        raise RuntimeError("settings unreadable")
    monkeypatch.setattr(dx, "duplex_enabled", boom)
    assert duplex_hands.enabled() is False


def test_registry_duplex_gate_follows_duplex_hands(monkeypatch):
    import core.agent.builtin_tools as bt
    from core.agent import duplex as dx
    monkeypatch.setattr(dx, "duplex_enabled", lambda: True)
    assert bt._duplex_enabled() is True
    monkeypatch.setattr(dx, "duplex_enabled", lambda: False)
    assert bt._duplex_enabled() is False


def test_registry_init_reads_the_duplex_gate_through_duplex_hands():
    """R15, T7: the registry no longer imports core.agent for the duplex gate,
    which frees one of its 25 import slots for the next tool."""
    from tests.architecture.conftest import collect_repo_modules, in_repo_imports
    [reg] = [m for m in collect_repo_modules()
             if m.rel_path == "core/agent/builtin_tools/__init__.py"]
    imports = in_repo_imports(reg)
    assert "core.agent" not in imports
    assert "core.agent.builtin_tools.duplex_hands" in imports
