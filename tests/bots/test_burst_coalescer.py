from tests.agent.conftest import load_module

mod = load_module("bots/discord/burst.py", "burst_under_test")
State = mod.ChannelBurstState
def ev(st, now, settle=3.0, typing_window=10.0, max_hold=45.0):
    return mod.evaluate(st, now, settle=settle, typing_window=typing_window, max_hold=max_hold)

def test_no_message_is_idle():
    # typing only, nothing sent -> never fires (erase-without-send)
    assert ev(State(last_message_at=None, typing_active_until=100.0, first_unanswered_at=None), now=50.0) == ("idle", None)

def test_single_message_waits_then_fires_after_settle():
    st = State(last_message_at=10.0, typing_active_until=0.0, first_unanswered_at=10.0)
    assert ev(st, now=11.0) == ("wait", 13.0)   # settle_at = 10 + 3
    assert ev(st, now=13.0) == ("fire", None)

def test_typing_extends_the_wait_past_settle():
    # message at 10, a typing ping keeps active until 18 -> can't fire until 18
    st = State(last_message_at=10.0, typing_active_until=18.0, first_unanswered_at=10.0)
    assert ev(st, now=14.0) == ("wait", 18.0)
    assert ev(st, now=18.0) == ("fire", None)

def test_max_hold_caps_unrelenting_typing():
    # first message at 10, typing keeps pushing active far out; cap at 10+45
    st = State(last_message_at=40.0, typing_active_until=999.0, first_unanswered_at=10.0)
    assert ev(st, now=54.0) == ("wait", 55.0)   # cap = 10 + 45
    assert ev(st, now=55.0) == ("fire", None)


# add to tests/bots/test_burst_coalescer.py
import asyncio, pytest

@pytest.mark.asyncio
async def test_burst_of_three_fires_once():
    fired = []
    # clock we advance by hand; scheduler wakes via asyncio.sleep(0) polling loop
    t = {"now": 0.0}
    coalescer = mod.BurstCoalescer(
        on_fire=lambda cid: fired.append(cid) or asyncio.sleep(0),
        settle=3.0, typing_window=10.0, max_hold=45.0,
        clock=lambda: t["now"],
    )
    for _ in range(3):
        coalescer.note_message(101)
        await asyncio.sleep(0)
    # not yet: still within settle
    t["now"] = 2.0
    await asyncio.sleep(0.05)
    assert fired == []
    # cross settle
    t["now"] = 3.5
    await asyncio.sleep(0.05)
    assert fired == [101]
