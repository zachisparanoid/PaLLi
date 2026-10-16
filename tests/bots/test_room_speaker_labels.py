"""Through the real doors (step 2, part 3): in a shared room with she_decides
on, the reply call sees every line with who said it — the owner's line with the
owner's mark, a guest's with their name — on Discord and Telegram alike."""
from __future__ import annotations

import pytest

from core.agent.speakers import OWNER_MARK
from tests.bots.room_harness import RoomHarness


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_she_reads_who_said_each_line(monkeypatch, tmp_path, door):
    room = RoomHarness(monkeypatch, tmp_path, door=door)
    await room.say("sam", "did anyone check the server?", first_pass="stay_quiet",
                   reason="Sam is asking the room, not me")
    await room.say("owner", "palli, check the disk", model_chooses="answer",
                   answer_text="It's at 12%.")
    ctx = room.last_run_context
    assert f"{OWNER_MARK}]: palli, check the disk" in ctx
    # Discord shows the guest as "Sam". The Telegram fakes give everyone the
    # same display name, "Dana", the owner included: the mark is the only thing
    # that tells them apart, which is the point.
    guest_name = "Sam" if door == "discord" else "Dana"
    assert f"[{guest_name}]: did anyone check the server?" in ctx
    assert f"{OWNER_MARK}]: did anyone check the server?" not in ctx


@pytest.mark.asyncio
async def test_with_the_switch_off_the_room_reads_as_before(monkeypatch, tmp_path):
    room = RoomHarness(monkeypatch, tmp_path, switch_on=False)
    await room.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    assert OWNER_MARK not in room.last_run_context
