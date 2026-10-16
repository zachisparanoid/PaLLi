"""``features.proactive_conversations`` is a real off switch again
(2026-10-14). The shipped yaml called it "a real kill switch" and palli_ctl
shows it as a toggle, but nothing had read it since step 19 deleted the lane
it gated. Now false stops every message PaLLi starts on its own: its reach-outs
and shares to the owner (the arbiter's ceiling reads 0, the rail-dark value),
group openers, and the hunch rail. Reminders, and replies to a person's ask
(the forge's follow-up included), never ask it."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai import arbiter
from core.turn import companion_flags, group_openers, proactive


def _snapshot(on: bool):
    return SimpleNamespace(features=SimpleNamespace(proactive_conversations=on),
                           companion=SimpleNamespace(arbiter_daily_send_ceiling=3,
                                                     group_openers=True,
                                                     group_openers_per_day=1))


@pytest.fixture
def switch(monkeypatch):
    from core.settings_boot import settings_service

    state = {"on": False}
    monkeypatch.setattr(settings_service, "snapshot", lambda: _snapshot(state["on"]))
    return state


def test_the_reader_follows_the_setting(switch):
    assert companion_flags.speaks_first_on() is False
    switch["on"] = True
    assert companion_flags.speaks_first_on() is True


def test_unbooted_settings_read_as_on(monkeypatch):
    """The schema default; early start-up and bare test paths behave as before."""
    from core.settings_boot import settings_service

    monkeypatch.setattr(settings_service, "snapshot", lambda: None)
    assert companion_flags.speaks_first_on() is True


def test_off_darkens_the_arbiter_ceiling(switch):
    assert arbiter._ceiling() == 0
    held = arbiter.admit_or_hold()
    assert held is not None and held["disposition"] == "ceiling_blocked"
    switch["on"] = True
    assert arbiter._ceiling() == 3


def test_off_holds_a_workbench_share(switch):
    out = asyncio.run(arbiter.deliver_share(text="I finished the map", owner_user_id=1))
    assert out["disposition"] == "ceiling_blocked"


def test_off_stops_group_openers(switch, monkeypatch):
    monkeypatch.setattr(group_openers, "_idle", lambda: pytest.fail("looked past the switch"))
    assert asyncio.run(group_openers.run_pass(owner_id=1)) == "off"


def test_off_stops_the_hunch_rail(switch, monkeypatch):
    async def _never(**_kw):
        pytest.fail("loaded a hunch past the switch")

    monkeypatch.setattr(proactive, "_load_unconsumed_candidate", _never)
    assert asyncio.run(proactive._consider_candidate_rail(owner_id=1)) is False
