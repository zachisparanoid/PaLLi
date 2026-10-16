"""The owner's corrections about names in a room, through the owner-only
``room_note`` tool (the room-reading fix, 2026-10-12): "Pol is a nickname for
you", "I was talking to Alex". Saved only from the owner's own words.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from core import room_people
from core.agent.builtin_tools.room_note import RoomNoteTool
from core.agent.tool_outcome import ToolFailure

KEY = "discord:8800002"


@pytest.fixture(autouse=True)
def _stores(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_ROOM_PEOPLE_PATH", str(tmp_path / "room_people.json"))
    monkeypatch.setenv("PALLI_ROOM_NOTES_PATH", str(tmp_path / "room_notes.json"))


def _ctx(*owner_words: str):
    return NS(surface="discord_text", channel_id=8800002, owner_words=list(owner_words),
              turn_owner_id=1)


@pytest.mark.asyncio
async def test_a_nickname_from_the_owners_own_words_is_saved():
    out = await RoomNoteTool().execute({"action": "name_me", "name": "Pol"},
                                       _ctx("Pol is a nickname for you"))
    assert not isinstance(out, ToolFailure)
    assert room_people.taught(KEY) == {"for_me": ["Pol"], "people": []}


@pytest.mark.asyncio
async def test_someone_else_from_the_owners_own_words_is_saved():
    out = await RoomNoteTool().execute({"action": "name_someone", "name": "Alex"},
                                       _ctx("I was talking to Alex, not you"))
    assert not isinstance(out, ToolFailure)
    assert room_people.taught(KEY)["people"] == ["Alex"]


@pytest.mark.asyncio
async def test_a_name_the_owner_never_wrote_is_not_saved():
    out = await RoomNoteTool().execute({"action": "name_me", "name": "Pol"},
                                       _ctx("you can call yourself whatever"))
    assert isinstance(out, ToolFailure)
    assert room_people.taught(KEY) == {"for_me": [], "people": []}


@pytest.mark.asyncio
async def test_her_own_name_cannot_be_made_someone_elses():
    out = await RoomNoteTool().execute({"action": "name_someone", "name": "Polly"},
                                       _ctx("Polly is someone else"))
    assert isinstance(out, ToolFailure)


@pytest.mark.asyncio
async def test_forget_and_list():
    tool = RoomNoteTool()
    await tool.execute({"action": "name_me", "name": "Pol"}, _ctx("call me Pol? no, YOU are Pol"))
    listed = await tool.execute({"action": "list"}, _ctx())
    assert '"Pol" means you' in listed
    out = await tool.execute({"action": "forget_name", "name": "pol"}, _ctx())
    assert not isinstance(out, ToolFailure)
    assert room_people.taught(KEY) == {"for_me": [], "people": []}


def test_the_tool_offers_the_name_actions():
    tool = RoomNoteTool()
    assert {"name_me", "name_someone", "forget_name"} <= set(
        tool.input_schema["properties"]["action"]["enum"])
    assert "name" in tool.input_schema["properties"]
