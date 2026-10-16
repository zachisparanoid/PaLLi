"""Step 23 · The look, on purpose — `look_at_the_house` (Task 7).

Spec §3: one read-only hand in the list_members shape (owner-only, no
approval); renders the device book; fresh=true takes one confirmation sweep
first, at most once per two minutes. Looking is not a deed. Registered
beside her other hands, gated by companion.lan_presence.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import lan_presence as lp
from core.agent.builtin_tools import the_house
from core.agent.tools import ToolContext
from tests.core.test_lan_presence_engine import Clock, Scripted, _flags

PHONE = {"aa:bb:cc:dd:ee:03": {"ip": "192.0.2.3", "hostname": "pixel"}}


def _ctx():
    return ToolContext(turn_owner_id=1, is_owner=True)


def test_shape_is_a_hand():
    t = the_house.LookAtTheHouseTool()
    assert t.name == "look_at_the_house"
    assert t.owner_only is True and t.requires_approval is False
    assert t.input_schema["properties"]["fresh"]["type"] == "boolean"
    assert [x.name for x in the_house.build_house_tools()] == ["look_at_the_house"]


@pytest.mark.asyncio
async def test_look_renders_the_book(tmp_path):
    clock = Clock()
    eng = lp.LanPresenceEngine(book_path=tmp_path / "b.json", subnet="192.0.2.0/24", iface="ens18",
                               sweeper=Scripted([dict(PHONE)]), prober=Scripted([]), clock=clock,
                               notice=lambda *a, **k: None)
    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(the_house, "_engine", lambda: eng):
        await eng.tick_sweep()
        out = await the_house.LookAtTheHouseTool().execute({}, _ctx())
    assert out.startswith("the house right now: 1 device online")
    assert "pixel" in out


@pytest.mark.asyncio
async def test_fresh_sweeps_once_and_is_rate_limited(tmp_path):
    clock = Clock()
    sweeper = Scripted([dict(PHONE), dict(PHONE), dict(PHONE)])
    eng = lp.LanPresenceEngine(book_path=tmp_path / "b.json", subnet="192.0.2.0/24", iface="ens18",
                               sweeper=sweeper, prober=Scripted([]), clock=clock, notice=lambda *a, **k: None)
    tool = the_house.LookAtTheHouseTool()
    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(the_house, "_engine", lambda: eng):
        await tool.execute({"fresh": True}, _ctx())
        clock.t += 30
        await tool.execute({"fresh": True}, _ctx())          # within two minutes: no second sweep
        clock.t += 120
        await tool.execute({"fresh": True}, _ctx())          # past it: one more
    assert len(sweeper.calls) == 2


@pytest.mark.asyncio
async def test_empty_book_and_flag_off_answer_plainly(tmp_path):
    eng = lp.LanPresenceEngine(book_path=tmp_path / "b.json", subnet="192.0.2.0/24", iface="ens18",
                               sweeper=Scripted([]), prober=Scripted([]), notice=lambda *a, **k: None)
    tool = the_house.LookAtTheHouseTool()
    with patch.object(lp, "companion_settings", lambda: _flags()), patch.object(the_house, "_engine", lambda: eng):
        out = await tool.execute({}, _ctx())
    assert "nothing in the device book yet" in out.lower()
    with patch.object(lp, "companion_settings", lambda: _flags(lan_presence=False)), patch.object(the_house, "_engine", lambda: eng):
        out = await tool.execute({}, _ctx())
    assert "off" in out.lower()


def test_registered_only_under_the_flag_and_never_at_the_workbench(monkeypatch):
    import core.agent.builtin_tools as bt

    monkeypatch.setattr(bt, "_lan_presence_enabled", lambda: False)
    assert "look_at_the_house" not in bt.build_default_registry().all_names()
    monkeypatch.setattr(bt, "_lan_presence_enabled", lambda: True)
    assert "look_at_the_house" in bt.build_default_registry().all_names()
    assert "look_at_the_house" not in bt.build_default_registry(surface="workbench").all_names()


def test_gate_reads_the_booted_companion_flag(monkeypatch):
    import core.agent.builtin_tools as bt
    from core.settings_boot import settings_service

    monkeypatch.setattr(settings_service, "snapshot", lambda: None)
    assert bt._lan_presence_enabled() is False                    # unbooted = off
    snap = SimpleNamespace(companion=SimpleNamespace(lan_presence=True))
    monkeypatch.setattr(settings_service, "snapshot", lambda: snap)
    assert bt._lan_presence_enabled() is True
