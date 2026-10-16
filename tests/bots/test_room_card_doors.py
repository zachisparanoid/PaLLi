"""Through the real doors (step 2, part 5): the room card on the owner's and a
guest's turns in a shared room, on Discord and Telegram; the owner's accounts
on the owner's turns only, in a room and in a DM; nothing private on a
guest's turn; the switch off reads as before."""
from __future__ import annotations

import pytest

from tests.agent.she_decides_harness import OWNER_DISCORD_ID, OWNER_TELEGRAM_ID, DmHarness
from tests.bots.room_harness import RoomHarness

ROOM = "## This Room"
ACCOUNTS = "## The Owner's Accounts"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_the_owners_turn_in_a_room(monkeypatch, tmp_path, door):
    room = RoomHarness(monkeypatch, tmp_path, door=door)
    await room.say("sam", "did anyone check the server?", first_pass="stay_quiet")
    await room.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    ctx = room.last_run_context
    assert ROOM in ctx and "Recently here, newest first:" in ctx
    room_line = ('a group chat on Discord: #general, in the server "the den"' if door == "discord"
                 else "This is a Telegram group.")
    assert room_line in ctx
    assert ACCOUNTS in ctx
    assert f"{OWNER_DISCORD_ID}." in ctx and f"{OWNER_TELEGRAM_ID}." in ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_a_guests_turn_gets_the_room_and_nothing_private(monkeypatch, tmp_path, door):
    room = RoomHarness(monkeypatch, tmp_path, door=door)
    await room.say("owner", "morning", model_chooses="answer", answer_text="morning!")
    await room.say("sam", "palli, what's the weather?", first_pass="answer",
                   model_chooses="answer", answer_text="Sunny.")
    ctx = room.last_run_context
    assert ROOM in ctx and "Recently here, newest first:" in ctx
    assert ACCOUNTS not in ctx and "## Where I Am" not in ctx
    assert str(OWNER_DISCORD_ID) not in ctx and str(OWNER_TELEGRAM_ID) not in ctx


@pytest.mark.asyncio
async def test_a_dm_gets_the_owners_accounts_but_no_room_card(monkeypatch, tmp_path):
    dm = DmHarness(monkeypatch, tmp_path)
    dm.model_answers("12%.")
    await dm.says("send me a DM on discord")
    kw = dm.calls[-1]
    ctx = str(kw.get("system") or "") + str(kw.get("system_tail") or "")
    assert ACCOUNTS in ctx and f"id {OWNER_DISCORD_ID}" in ctx
    assert ROOM not in ctx


@pytest.mark.asyncio
async def test_with_the_switch_off_there_is_no_room_card(monkeypatch, tmp_path):
    room = RoomHarness(monkeypatch, tmp_path, switch_on=False)
    await room.say("owner", "palli, check the disk", model_chooses="answer", answer_text="12%.")
    assert ROOM not in room.last_run_context
