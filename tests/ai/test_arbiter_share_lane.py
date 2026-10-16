"""The share lane (2026-08-18): her workbench's SHARE line as a real message,
riding the arbiter's judgment of the MOMENT (conversation-state → quiet
window → learned quiet w/ presence override → freshness) but not its
ceiling — the owner's standing request ("a message when it's done") is not
an impulse to ration. Ceiling 0 (rail DARK) still darkens it: the emergency
brake means "she does not reach out on her own", full stop.

Every test pins its own flags — owner flips are environment (3352e3d)."""
import time
import types

import pytest

import ai.arbiter as arbiter
from tests.ai.test_arbiter import _Comp


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(arbiter, "ARBITER_DATA_DIR", tmp_path / "arbiter")
    comp = _Comp()
    monkeypatch.setattr(arbiter, "_companion", lambda: comp)

    async def _fresh_ok(**kw):
        return (True, "fresh (fixture)")

    async def _chan(*, owner_id):
        return ("discord", "123")

    async def _last_msg(*, owner_id):
        return time.time() - 3600.0

    sent = []

    async def _record(**kw):
        sent.append(kw)
        return True

    monkeypatch.setattr(arbiter, "_freshness_check", _fresh_ok)
    monkeypatch.setattr(arbiter, "_pick_surface", _chan)
    monkeypatch.setattr(arbiter, "_last_user_message_at", _last_msg)
    monkeypatch.setattr(arbiter, "_record_outbound", _record)
    monkeypatch.setattr(arbiter, "_run_phase", lambda sk: "")
    monkeypatch.setattr(arbiter, "_is_working", lambda sk: False)
    return types.SimpleNamespace(comp=comp, sent=sent)


@pytest.mark.asyncio
async def test_open_moment_sends_with_workbench_share_custody(wired):
    v = await arbiter.deliver_share(text="the lake page has a sky-history now",
                                    owner_user_id=7)
    assert v["disposition"] == "sent"
    assert len(wired.sent) == 1
    kw = wired.sent[0]
    assert kw["origin"] == "workbench_share"
    assert kw["text"] == "the lake page has a sky-history now"
    assert kw["channel_id"] == 123 and kw["owner_id"] == 7


@pytest.mark.asyncio
async def test_a_share_does_not_spend_or_obey_the_daily_ceiling(wired):
    wired.comp.arbiter_daily_send_ceiling = 1
    for _ in range(3):
        v = await arbiter.deliver_share(text="made a thing", owner_user_id=7)
        assert v["disposition"] == "sent"
    assert len(wired.sent) == 3
    # the organic rail's count is untouched — a share is not one of HER
    # 3/day reaches; the ledger still carries each one, distinctly.
    assert arbiter.sends_today() == 0
    rows = (arbiter.ARBITER_DATA_DIR / arbiter._ledger_path().name).read_text().splitlines()
    assert sum(1 for r in rows if '"sent_share"' in r) == 3


@pytest.mark.asyncio
async def test_rail_dark_darkens_shares_too(wired):
    wired.comp.arbiter_daily_send_ceiling = 0
    v = await arbiter.deliver_share(text="made a thing", owner_user_id=7)
    assert v["disposition"] == "ceiling_blocked" and wired.sent == []


@pytest.mark.asyncio
async def test_his_quiet_window_holds_a_share(wired, monkeypatch):
    wired.comp.arbiter_quiet_window = "00:00-23:59"
    v = await arbiter.deliver_share(text="made a thing", owner_user_id=7)
    assert v["disposition"] == "held_quiet_window" and wired.sent == []


@pytest.mark.asyncio
async def test_mid_conversation_holds_a_share(wired, monkeypatch):
    async def _just_now(*, owner_id):
        return time.time() - 10.0
    monkeypatch.setattr(arbiter, "_last_user_message_at", _just_now)
    v = await arbiter.deliver_share(text="made a thing", owner_user_id=7)
    assert v["disposition"] == "held_conversation" and wired.sent == []


@pytest.mark.asyncio
async def test_already_told_him_is_stale_dropped(wired, monkeypatch):
    async def _stale(**kw):
        return (False, "she said it in the last reply")
    monkeypatch.setattr(arbiter, "_freshness_check", _stale)
    v = await arbiter.deliver_share(text="made a thing", owner_user_id=7)
    assert v["disposition"] == "stale_dropped" and wired.sent == []


@pytest.mark.asyncio
async def test_no_channel_holds_and_send_failure_closes(wired, monkeypatch):
    async def _none(*, owner_id):
        return None
    monkeypatch.setattr(arbiter, "_pick_surface", _none)
    v = await arbiter.deliver_share(text="made a thing", owner_user_id=7)
    assert v["disposition"] == "held_no_channel"

    async def _chan(*, owner_id):
        return ("discord", "123")
    async def _boom(**kw):
        raise RuntimeError("discord down")
    monkeypatch.setattr(arbiter, "_pick_surface", _chan)
    monkeypatch.setattr(arbiter, "_record_outbound", _boom)
    v = await arbiter.deliver_share(text="made a thing", owner_user_id=7)
    assert v["disposition"] == "error_closed"


@pytest.mark.asyncio
async def test_empty_text_is_held_no_framing(wired):
    v = await arbiter.deliver_share(text="   ", owner_user_id=7)
    assert v["disposition"] == "held_no_framing" and wired.sent == []
