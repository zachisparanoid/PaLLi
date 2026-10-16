"""Memory that reaches, in friends' rooms, on both doors (R15, W2; plan
docs/2026-10-13-memory-that-reaches-plan.md).

A friend's turn has no tools (the owner's rule), so before PaLLi replies to
a friend a code step searches that room's own history and her reply prompt
carries what it found, under items 18–19 (Draft 2, the owner's yes of
10-13). Never the owner's private chats, never another room; the owner's own
turns never get it (the search tool is theirs); with companion.memory_reach
off, nothing of it.

The REAL door (Discord or Telegram) and spine (``RoomHarness``), recall
returning a real TurnContext so the reply prompt is the adapter's, and a
fixture record built in the test's tmp path from hand-written rows (the
record's catch-up is a recorder; the search runs by words only). The same
words are said in this room, in the owner's DM and in another room. Stand-in
names only: Robin is the owner; Sam and Jo are guests.
"""
from __future__ import annotations

import pytest

import ai.memory_reach_index as mri
import ai.memory_reach_search as mrs
from core.agent.speakers import OWNER_MARK
from core.agent.system_prompt import ROOM_MEMORY_FRAMING, ROOM_MEMORY_HEADING
from core.turn.types import TurnContext
from tests.agent.she_decides_harness import DM_CHANNEL, OWNER_TELEGRAM_ID
from tests.bots.room_harness import ROOM, TG_GROUP, RoomHarness

DOORS = ["discord", "telegram"]
TS = 1_791_500_000.0                    # October 2026
HERE = "the boat trip leaves from the old harbour at eight"
OWNERS_DM = "the boat trip leaves from the east dock at nine"
OTHER_ROOM = "the boat trip leaves from the west dock at ten"
QUESTION = "palli, where does the boat trip leave from?"

pytestmark = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


@pytest.fixture(autouse=True)
def _people_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_ROOM_PEOPLE_PATH", str(tmp_path / "room_people.json"))


def _keys(door: str) -> tuple[str, str, str]:
    """This room, the owner's DM and another room, as the record keys them."""
    if door == "discord":
        return f"discord:{ROOM}", f"discord:{DM_CHANNEL}", f"discord:{ROOM + 1}"
    return f"telegram:{TG_GROUP}", f"telegram:{OWNER_TELEGRAM_ID}", f"telegram:{TG_GROUP - 1}"


def _record(tmp_path, monkeypatch, door: str, *, owner_name: str = "Robin") -> list[dict]:
    """The fixture record at the suite's record path; returns the catch-ups.
    ``owner_name``: the name the record keeps for the owner's lines (for a
    database row, the account handle)."""
    here, owners_dm, other = _keys(door)
    lines = [
        ("db:1", owners_dm, TS, "owner", owner_name, OWNERS_DM),
        ("f2:1", other, TS + 60, "guest", "Jo", OTHER_ROOM),
        ("db:2", here, TS + 120, "owner", owner_name, HERE),
    ]
    path = tmp_path / "memory_reach.sqlite3"
    conn = mri.connect(path)
    try:
        mri.add_rows(conn, [mri.Row(source="db" if ref.startswith("db:") else "room_file",
                                    source_ref=ref, room=room, ts=ts, day=mri._day(ts),
                                    speaker=speaker, name=name, text=text)
                            for ref, room, ts, speaker, name, text in lines], vectors=None)
    finally:
        conn.close()
    monkeypatch.setenv("PALLI_MEMORY_REACH_PATH", str(path))
    monkeypatch.setattr(mri, "local_embeddings_on", lambda: False)     # words only
    caught: list[dict] = []

    async def catch_up(**kw):
        caught.append(kw)
        return {}

    monkeypatch.setattr(mri, "catch_up", catch_up)
    return caught


def _room(monkeypatch, tmp_path, door: str, *, reach: bool = True,
          owner_name: str = "Robin") -> tuple[RoomHarness, list, list]:
    """The room, recall giving a real TurnContext, the record, and a spy on
    the step: (room, the catch-ups, the steps taken)."""
    room = RoomHarness(monkeypatch, tmp_path, door=door)
    if door == "telegram":
        build = room._telegram_update

        def named(who: str, text: str):
            update = build(who, text)
            update.message.from_user.full_name = "Robin" if who == "owner" else who.capitalize()
            return update

        room._telegram_update = named

    async def recall(turn):
        return TurnContext(turn=turn)

    monkeypatch.setattr("core.turn.recall.build_turn_context", recall)
    caught = _record(tmp_path, monkeypatch, door, owner_name=owner_name)
    monkeypatch.setattr(mri, "enabled", lambda: reach)
    steps: list = []
    real = mrs.room_section_for

    async def spy(turn, **kw):
        steps.append(turn)
        return await real(turn, **kw)

    monkeypatch.setattr(mrs, "room_section_for", spy)
    return room, caught, steps


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_friend_gets_their_own_rooms_line_and_nothing_private(monkeypatch, tmp_path, door):
    room, caught, steps = _room(monkeypatch, tmp_path, door)
    await room.say("sam", QUESTION, first_pass="answer", model_chooses="answer",
                   answer_text="From the old harbour, at eight.")

    assert room.full_runs == 1 and room.sent_to("sam")
    ctx = room.last_run_context
    assert f"{ROOM_MEMORY_HEADING}\n{ROOM_MEMORY_FRAMING}\n" in ctx
    section = ctx[ctx.index(ROOM_MEMORY_HEADING):]
    # The room shows no line of the owner's on this reply: the mark alone (W1).
    assert f"] {OWNER_MARK}: {HERE}" in section and "Robin" not in section
    # The same words in the owner's DM and in another room never reach it.
    assert "east dock" not in ctx and "west dock" not in ctx
    assert len(steps) == 1 and len(caught) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_friends_section_names_the_owner_as_the_room_does(monkeypatch, tmp_path, door):
    """I2 / W1 (Fable's whole-branch review): the record keeps the owner's
    account handle for a database line; the room's labels show the owner's
    display name. The section names the owner as the room does."""
    room, _caught, _steps = _room(monkeypatch, tmp_path, door, owner_name="owner_example")
    await room.say("owner", "palli, morning everyone", model_chooses="answer",
                   answer_text="Morning!")
    await room.say("sam", QUESTION, first_pass="answer", model_chooses="answer",
                   answer_text="From the old harbour, at eight.")

    assert room.full_runs == 2 and room.sent_to("sam")
    ctx = room.last_run_context
    section = ctx[ctx.index(ROOM_MEMORY_HEADING):]
    assert f"] Robin {OWNER_MARK}: {HERE}" in section
    assert "owner_example" not in section


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_the_owners_turn_in_the_room_never_gets_the_section(monkeypatch, tmp_path, door):
    room, caught, steps = _room(monkeypatch, tmp_path, door)
    await room.say("owner", QUESTION, model_chooses="answer", answer_text="Let me look.")

    assert room.full_runs == 1
    assert ROOM_MEMORY_HEADING not in room.last_run_context
    assert HERE not in room.last_run_context
    assert steps == [] and caught == []                  # the search tool is theirs


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_with_the_switch_off_a_friend_gets_none_of_it(monkeypatch, tmp_path, door):
    room, caught, steps = _room(monkeypatch, tmp_path, door, reach=False)
    await room.say("sam", QUESTION, first_pass="answer", model_chooses="answer",
                   answer_text="No idea, sorry.")

    assert room.full_runs == 1 and room.sent_to("sam")
    ctx = room.last_run_context
    assert ROOM_MEMORY_HEADING not in ctx and HERE not in ctx
    assert steps == [] and caught == []
