"""A thread whose message was dropped stale waits a day (the presence slice,
2026-10-13). On 10-12 one unanswered question (one thread) was drafted four
times and dropped stale four times: each try spent a judge call and said
nothing new. Now a thread the judge called stale carries no message for 24
hours, unless the thread itself changes; the judge is not asked again.
The repeat check itself is unchanged."""
from __future__ import annotations

import json
import time
import types

import pytest

from ai import arbiter
from tests.ai.test_arbiter import _say, wired  # noqa: F401  (the fixture)


def _rows():
    path = arbiter._ledger_path()
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()] if path.exists() else []


def _judge(monkeypatch, verdicts):
    calls = []

    async def _check(**kw):
        calls.append(kw["framing"])
        return verdicts.pop(0)

    monkeypatch.setattr(arbiter, "_freshness_check", _check)
    return calls


@pytest.mark.asyncio
async def test_a_stale_drop_names_its_thread_in_the_ledger(wired, monkeypatch):
    _judge(monkeypatch, [(False, "asked already")])
    await arbiter.consider_release(impulse=_say(thread_id="abcdef12"), owner_user_id=1,
                                   arbiter_live=True)
    stale = [r for r in _rows() if r["disposition"] == "stale_dropped"]
    assert stale and stale[0]["thread_id"] == "abcdef1234567890"


@pytest.mark.asyncio
async def test_the_same_thread_waits_a_day_without_a_judge_call(wired, monkeypatch):
    calls = _judge(monkeypatch, [(False, "asked already"), (True, "fresh")])
    await arbiter.consider_release(impulse=_say(thread_id="abcdef12"), owner_user_id=1,
                                   arbiter_live=True)
    out = await arbiter.consider_release(impulse=_say(thread_id="abcdef12", framing="again?"),
                                         owner_user_id=1, arbiter_live=True)
    assert out["disposition"] == "held_stale_wait"
    assert calls == ["hey — verdict on the name?"]          # the judge was asked once
    assert wired.sent == []
    assert [r["disposition"] for r in _rows()] == ["stale_dropped", "held_stale_wait"]
    assert arbiter.sends_today() == 0


@pytest.mark.asyncio
async def test_another_thread_or_none_is_not_held(wired, monkeypatch):
    _judge(monkeypatch, [(False, "asked already"), (True, "fresh")])
    await arbiter.consider_release(impulse=_say(thread_id="abcdef12"), owner_user_id=1,
                                   arbiter_live=True)
    out = await arbiter.consider_release(impulse=_say(framing="something new"), owner_user_id=1,
                                         arbiter_live=True)
    assert out["disposition"] == "sent"


@pytest.mark.asyncio
async def test_after_a_day_the_thread_may_carry_a_message_again(wired, monkeypatch):
    _judge(monkeypatch, [(False, "asked already"), (True, "fresh")])
    await arbiter.consider_release(impulse=_say(thread_id="abcdef12"), owner_user_id=1,
                                   arbiter_live=True)
    later = time.time() + arbiter.STALE_WAIT_SECONDS + 60
    monkeypatch.setattr(arbiter.time, "time", lambda: later)
    out = await arbiter.consider_release(impulse=_say(thread_id="abcdef12"), owner_user_id=1,
                                         arbiter_live=True)
    assert out["disposition"] == "sent"


@pytest.mark.asyncio
async def test_a_thread_that_changed_since_the_drop_may_carry_one_again(wired, monkeypatch):
    _judge(monkeypatch, [(False, "asked already"), (True, "fresh")])
    await arbiter.consider_release(impulse=_say(thread_id="abcdef12"), owner_user_id=1,
                                   arbiter_live=True)

    async def _changed(*, owner_user_id):
        T = types.SimpleNamespace
        return [T(id="abcdef1234567890", kind=T(value="personal"), topic_summary="ferret",
                  urgency="low", updated_at=time.time() + 5)]

    monkeypatch.setattr(arbiter, "_list_active", _changed)
    out = await arbiter.consider_release(impulse=_say(thread_id="abcdef12"), owner_user_id=1,
                                         arbiter_live=True)
    assert out["disposition"] == "sent"


@pytest.mark.asyncio
async def test_tonights_pick_waits_too(wired, monkeypatch):
    monkeypatch.setattr(arbiter, "_names_tonights_pick", lambda token: token == "tonight")
    calls = _judge(monkeypatch, [(False, "said already"), (True, "fresh")])
    await arbiter.consider_release(impulse=_say(thread_id="tonight"), owner_user_id=1,
                                   arbiter_live=True)
    out = await arbiter.consider_release(impulse=_say(thread_id="tonight"), owner_user_id=1,
                                         arbiter_live=True)
    assert out["disposition"] == "held_stale_wait" and len(calls) == 1
