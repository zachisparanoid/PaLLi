"""Step 27 Phase B — a sidecar is launched, delivered, handed back, and its lane released."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@dataclass
class Md:
    channel_id: int = 7
    extra: dict = field(default_factory=dict)
    user_side_prerecorded: bool = True          # the client recorded his message on arrival


@dataclass
class Turn:
    surface: str = "discord_text"
    owner_id: int = 1
    content: str = "what's the wifi password again?"
    metadata: Md = field(default_factory=Md)


def _run(coro):
    return asyncio.run(coro)


def test_talk_sidecar_runs_the_child_turn_delivers_hands_back_and_uses_the_talk_lane(tmp_path):
    from core.agent import sidecar, task_slots as ts
    ts.reset_for_tests(); sidecar.reset_for_tests()
    seen = {}

    async def dispatch(turn):
        seen["child_key"] = turn.metadata.extra["session_key_override"]
        seen["sidecar_of"] = turn.metadata.extra["sidecar_of"]
        seen["kind"] = turn.metadata.extra["sidecar_kind"]
        seen["prerecorded"] = turn.metadata.user_side_prerecorded
        seen["talk_held_during"] = ts.talk_held("discord:7")
        return SimpleNamespace(responded=True, response_text="it's on the fridge", attachments=())

    async def deliver(result):
        seen["delivered"] = result.response_text

    async def on_done(reply):
        seen["reply"] = reply

    async def go():
        t = sidecar.run_sidecar(parent_key="discord:7", turn=Turn(), kind="talk", dispatch=dispatch,
                                deliver=deliver, sessions_root=tmp_path, on_done=on_done)
        assert t is not None and t.key == "discord:7#sidecar-1"
        assert ts.held("discord:7") == []                     # a conversation is not a task
        await t
    _run(go())
    assert seen["child_key"] == "discord:7#sidecar-1" and seen["sidecar_of"] == "discord:7" and seen["kind"] == "talk"
    assert seen["prerecorded"] is True and seen["talk_held_during"] is True
    assert seen["delivered"] == "it's on the fridge" and seen["reply"] == "it's on the fridge"
    assert ts.talk_held("discord:7") is False                 # lane released
    # correction 6: the sidecar seeded its own child transcript with his words
    lines = (tmp_path / "discord_7_sidecar-1.jsonl").read_text(encoding="utf-8").strip().splitlines()
    rows = [json.loads(l) for l in lines if l.strip()]
    assert rows[0]["role"] == "user" and "wifi password" in json.dumps(rows[0]["content"])


def test_task_sidecar_takes_a_task_slot(tmp_path):
    from core.agent import sidecar, task_slots as ts
    ts.reset_for_tests(); sidecar.reset_for_tests()
    seen = {}

    async def dispatch(turn):
        seen["held_during"] = ts.held("discord:7")
        return SimpleNamespace(responded=True, response_text="printed", attachments=())
    async def deliver(r): pass
    async def on_done(r): pass
    async def go():
        t = sidecar.run_sidecar(parent_key="discord:7", turn=Turn(content="also print the adapter"),
                                kind="task", dispatch=dispatch, deliver=deliver,
                                sessions_root=tmp_path, on_done=on_done)
        assert t is not None
        await t
    _run(go())
    assert seen["held_during"] == ["sidecar-1"] and ts.held("discord:7") == []


def test_launch_returns_none_when_task_slots_are_full_but_talk_still_opens(tmp_path):
    from core.agent import sidecar, task_slots as ts
    ts.reset_for_tests(); sidecar.reset_for_tests()
    with patch.object(ts, "cap", lambda: 1):
        assert ts.acquire("discord:7", "the run")
        async def dispatch(turn): return SimpleNamespace(responded=False, response_text="", attachments=())
        async def deliver(r): pass
        async def on_done(r): pass
        t = sidecar.run_sidecar(parent_key="discord:7", turn=Turn(), kind="task", dispatch=dispatch,
                                deliver=deliver, sessions_root=tmp_path, on_done=on_done)
        assert t is None                                      # a full house queues (spec §4)
        async def go():
            t2 = sidecar.run_sidecar(parent_key="discord:7", turn=Turn(), kind="talk", dispatch=dispatch,
                                     deliver=deliver, sessions_root=tmp_path, on_done=on_done)
            assert t2 is not None                             # talk never waits on the cap
            await t2
        _run(go())


def test_second_talk_sidecar_waits_while_one_runs(tmp_path):
    from core.agent import sidecar, task_slots as ts
    ts.reset_for_tests(); sidecar.reset_for_tests()
    ts.talk_acquire("discord:7")
    async def dispatch(turn): raise AssertionError("must not run")
    async def deliver(r): pass
    async def on_done(r): pass
    assert sidecar.run_sidecar(parent_key="discord:7", turn=Turn(), kind="talk", dispatch=dispatch,
                               deliver=deliver, sessions_root=tmp_path, on_done=on_done) is None
    ts.talk_release("discord:7")


def test_lane_released_even_when_dispatch_raises(tmp_path):
    from core.agent import sidecar, task_slots as ts
    ts.reset_for_tests(); sidecar.reset_for_tests()
    async def dispatch(turn): raise RuntimeError("boom")
    async def deliver(r): pass
    done = []
    async def on_done(r): done.append(r)
    async def go():
        t = sidecar.run_sidecar(parent_key="discord:7", turn=Turn(), kind="task", dispatch=dispatch,
                                deliver=deliver, sessions_root=tmp_path, on_done=on_done)
        await t
    _run(go())
    assert ts.held("discord:7") == [] and done == [""]        # handed back empty, never raised
