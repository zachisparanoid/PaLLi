"""The room card's part of the room-reading fix (2026-10-12): the owner listed
once, who else is in the room, and the names the owner taught her; and a
burst's signals taken from every fragment, not only the last one.

Stand-in names only: Robin is the owner; Sam, Alex and Jo are in the room.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from core.agent import room_card
from core.agent.session import AgentMessage, TextBlock

KEY = "discord:8800002"
OWNER = {"kind": "owner", "platform": "discord", "name": "Robin"}
OWNER_UNNAMED = {"kind": "owner", "platform": "discord"}
SAM = {"kind": "guest", "platform": "discord", "name": "Sam"}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_ROOM_PEOPLE_PATH", str(tmp_path / "room_people.json"))
    room_card.reset_for_tests()
    yield
    room_card.reset_for_tests()


def _u(text, speaker):
    return AgentMessage(role="user", content=[TextBlock(text=text)], speaker=speaker)


def _turn(*, guest=False):
    md = NS(channel_name="general", guild_name="the den", extra={}, is_dm=False,
            origin="guest" if guest else "user", channel_id=8800002)
    return NS(surface="discord_text", metadata=md, content="hi", channel_id=8800002)


# ── the owner once ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("rows", [
    [_u("old", OWNER_UNNAMED), _u("[Sam]: yo", SAM), _u("new", OWNER)],
    [_u("old", OWNER), _u("[Sam]: yo", SAM), _u("new", OWNER_UNNAMED)],
])
def test_the_owner_is_listed_once_by_name(rows):
    from core.agent.room_tail import recent_speakers

    assert recent_speakers(rows) == ["Robin ★owner", "Sam"]


def test_an_owner_never_named_is_still_listed_once():
    from core.agent.room_tail import recent_speakers

    rows = [_u("a", OWNER_UNNAMED), _u("b", OWNER_UNNAMED)]
    assert recent_speakers(rows) == ["★owner"]


# ── who's who on the card ───────────────────────────────────────────────────

def _members(monkeypatch, names, *, scoped=True):
    from core.agent import hands_runtime as hr

    members = [{"display_name": n, "name": n.lower() + "_x", "bot": False} for n in names]
    members.append({"display_name": "PaLLi", "name": "palli_bot", "bot": True})
    monkeypatch.setattr(hr, "members_here",
                        lambda query="", *, channel_id=None: (members, scoped))


def test_the_card_lists_who_else_is_here_without_the_owner_or_recent_speakers(monkeypatch):
    _members(monkeypatch, ["Robin", "Sam", "Alex", "Jo"])
    room_card.remember_owner(NS(metadata=NS(speaker={**OWNER, "handle": "robin_x"})))
    card = room_card.room_section(_turn(), [_u("[Sam]: yo", SAM)], KEY)
    assert "Recently here, newest first: Sam." in card
    assert "Also in this room: Alex, Jo." in card
    assert "PaLLi" not in card.split("Also in this room:")[1]


def test_a_long_list_is_cut_short(monkeypatch):
    _members(monkeypatch, [f"P{i}" for i in range(12)])
    card = room_card.room_section(_turn(), [], KEY)
    assert "Also in this room: P0, P1, P2, P3, P4, P5, P6, P7, and 4 more." in card


def test_no_key_means_todays_card(monkeypatch):
    _members(monkeypatch, ["Alex"])
    assert "Also in this room" not in room_card.room_section(_turn(), [])


def test_the_names_the_owner_taught_show_on_the_owners_turns_only(monkeypatch):
    from core import room_people

    _members(monkeypatch, [], scoped=False)
    room_people.teach(KEY, "Pol", means_me=True)
    room_people.teach(KEY, "Alex", means_me=False)
    owner_card = room_card.room_section(_turn(), [], KEY)
    assert "The owner told me I also go by Pol here." in owner_card
    assert "Also in this room: Alex." in owner_card
    guest_card = room_card.room_section(_turn(guest=True), [], KEY)
    assert "Pol" not in guest_card
    assert "Also in this room: Alex." in guest_card


def test_the_names_lines_for_the_vote_and_her_reply():
    rows = [_u("[Sam]: yo", SAM)]
    turn = NS(**{**vars(_turn()), "content": "hey Sam, polly says hi"})
    vote, reply = room_card.names_lines(turn, rows, KEY)
    assert vote == ('Names in this message: "Sam" (someone in this room, not her) · '
                    '"polly" (her)')
    assert reply == ('Names in this message: "Sam" (someone in this room, not me) · '
                     '"polly" (me)')


# ── a burst's signals are every fragment's ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_discord_burst_keeps_her_name_and_an_at_mention_from_an_early_fragment(
        monkeypatch, tmp_path):
    from tests.bots.room_harness import BOT_ID, RoomHarness

    room = RoomHarness(monkeypatch, tmp_path)
    first = room._discord_message("owner", f"<@{BOT_ID}> polly, quick one")
    first.mentions = [NS(id=BOT_ID, display_name="PaLLi", name="palli_bot", bot=True)]
    room.model_answers("sure")
    await room.client.on_message(first)
    await room.client.on_message(room._discord_message("owner", "what's the weather like"))
    await room.client._fire_burst(room.room)
    room.client._coalescer.mark_replied(room.room)
    await room.settle()
    vote = room.cadence_prompts[-1]
    assert "it @mentions her" in vote and "it says her name" in vote


@pytest.mark.asyncio
async def test_a_telegram_burst_keeps_her_name_from_an_early_fragment(monkeypatch, tmp_path):
    from tests.bots.room_harness import RoomHarness

    room = RoomHarness(monkeypatch, tmp_path, door="telegram")
    room.model_answers("sure")
    await room.client.on_message(room._telegram_update("owner", "polly, quick one"))
    await room.client.on_message(room._telegram_update("owner", "what's the weather like"))
    await room.client._fire_burst(room.room)
    room.client._coalescer.mark_replied(room.room)
    await room.settle()
    assert "it says her name" in room.cadence_prompts[-1]


@pytest.mark.asyncio
async def test_a_dm_burst_is_todays(monkeypatch, tmp_path):
    """The owner's word: private chats stay exactly as they are. A DM burst
    still reads only its last fragment's signals, as before the fix."""
    from tests.bots.room_harness import OWNER_TELEGRAM_ID, RoomHarness
    from tests.bots.test_telegram_client import FakeMessage, FakeUpdate

    room = RoomHarness(monkeypatch, tmp_path, door="telegram")
    room.model_answers("sure")

    def dm(text):
        return FakeUpdate(FakeMessage(text=text, author_id=OWNER_TELEGRAM_ID,
                                      chat_id=OWNER_TELEGRAM_ID, chat_type="private"))

    await room.client.on_message(dm("polly, quick one"))
    await room.client.on_message(dm("what's the weather like"))
    await room.client._fire_burst(OWNER_TELEGRAM_ID)
    await room.settle()
    vote = room.cadence_prompts[-1]
    assert "a direct message to her" in vote
    assert "it says her name" not in vote
    assert "Names in this message" not in vote
