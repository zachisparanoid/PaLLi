"""Through the real doors (step 2, part 4; the who-is-talking plan, A1): on the
owner's turn in a shared room, her quick pre-reads read the room itself,
guests' lines included, each saying who said it. Before, they read the
conversations database, which never holds a guest's words (10-06: she took a
friend's message for the owner's). A DM, and a room with the switch off, read
as before."""
from __future__ import annotations

import pytest

from core.agent import room_tail
from core.agent.speakers import OWNER_MARK
from tests.agent.she_decides_harness import DmHarness
from tests.bots.room_harness import RoomHarness


def _peek_at_recall(monkeypatch) -> list:
    """What the mood read would get: the room the runner set for recall."""
    import core.turn.recall as recall_mod

    seen: list = []
    stand_in = recall_mod.build_turn_context

    async def peek(turn):
        seen.append(room_tail.current_room_tail.get())
        return await stand_in(turn)

    monkeypatch.setattr("core.turn.recall.build_turn_context", peek)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_on_the_owners_turn_her_quick_vote_reads_the_room(monkeypatch, tmp_path, door):
    room = RoomHarness(monkeypatch, tmp_path, door=door)
    seen = _peek_at_recall(monkeypatch)
    await room.say("sam", "did anyone check the server?", first_pass="stay_quiet",
                   reason="Sam is asking the room, not me")
    await room.say("owner", "palli, check the disk", model_chooses="answer",
                   answer_text="It's at 12%.")

    vote = room.cadence_prompts[-1]
    context, latest = vote.split("Latest message:\n", 1)
    guest = "Sam" if door == "discord" else "Dana"
    assert f"[{guest}]: did anyone check the server?" in context
    assert "palli, check the disk" not in context            # shown once, as the latest
    assert latest.split("\n", 1)[0].endswith(f"{OWNER_MARK}]: palli, check the disk")

    # The mood read gets the same room (the runner sets it around recall).
    assert seen and seen[-1] is not None
    assert f"[{guest}]: did anyone check the server?" in seen[-1]
    assert "palli, check the disk" not in seen[-1]
    assert room_tail.current_room_tail.get() is None          # reset after recall


@pytest.mark.asyncio
async def test_a_guests_first_read_shows_who_said_the_latest_line(monkeypatch, tmp_path):
    room = RoomHarness(monkeypatch, tmp_path)
    await room.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    await room.say("sam", "is the server ok?", first_pass="stay_quiet")
    context, latest = room.cadence_prompts[-1].split("Latest message:\n", 1)
    assert latest.startswith("[Sam]: is the server ok?")
    assert f"{OWNER_MARK}]: palli, check the disk" in context


@pytest.mark.asyncio
async def test_in_a_dm_the_pre_reads_read_as_before(monkeypatch, tmp_path):
    dm = DmHarness(monkeypatch, tmp_path)
    seen = _peek_at_recall(monkeypatch)
    dm.model_answers("12%.")
    await dm.says("check the disk")
    assert dm.cadence_prompts and OWNER_MARK not in dm.cadence_prompts[-1]
    assert seen == [None]


@pytest.mark.asyncio
async def test_with_the_switch_off_the_room_reads_as_before(monkeypatch, tmp_path):
    room = RoomHarness(monkeypatch, tmp_path, switch_on=False)
    seen = _peek_at_recall(monkeypatch)
    await room.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    assert all(OWNER_MARK not in p for p in room.cadence_prompts)
    assert seen == [None]
