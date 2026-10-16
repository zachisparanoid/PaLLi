"""The room-reading fix, in a room on both doors (the owner's design, 2026-10-12).

The standing set of seven kinds of message, each checked where it matters: the
facts reach the quick vote (in the third person, with sentence C) and her reply
call (in her own words). What she then decides is the model's: the replay with
her real model measures that; these tests prove the facts arrive, and that a DM
sees none of it.

The set runs under both switch states (R14, T8; plan §2.9): with
``companion.first_look`` off the facts reach the quick vote as above; with it
on they reach her first look instead, in her own first person ("me", not
"her") beside the look's room rules (Draft 1), and the reply call's facts are
the same words either way. Friends' messages with the look on are also
pinned one by one in tests/bots/test_first_look_rooms.py.

Stand-in names only: Robin is the owner; Sam, Alex and Jo are guests.
"""
from __future__ import annotations

import pytest

from core.agent import first_look as _first_look
from core.agent.cadence import ROOM_READING_VOTE
from core.agent.system_prompt import ROOM_READING_INSTRUCTION
from tests.agent.she_decides_harness import DmHarness
from tests.bots.room_harness import RoomHarness

DOORS = ["discord", "telegram"]
C_START = " ".join(ROOM_READING_VOTE.split())[:40]
RULES_START = " ".join(_first_look.ROOM_RULES.split())[:40]
LOOK = [False, True]

# The door builds its Turn without a received_at; that `datetime.utcnow()` is
# production debt pytest.ini leaves unfiltered on purpose, not this file's.
pytestmark = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


def _flat(text: str) -> str:
    return " ".join(str(text).split())


@pytest.fixture(autouse=True)
def _people_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_ROOM_PEOPLE_PATH", str(tmp_path / "room_people.json"))


def _room(monkeypatch, tmp_path, door: str, look_on: bool = False) -> RoomHarness:
    """The room harness, with each Telegram sender showing their own name (the
    shared fake shows one name for everyone)."""
    room = RoomHarness(monkeypatch, tmp_path, door=door, first_look=look_on)
    if door == "telegram":
        build = room._telegram_update

        def named(who: str, text: str):
            update = build(who, text)
            update.message.from_user.full_name = "Robin" if who == "owner" else who.capitalize()
            return update

        room._telegram_update = named
    return room


async def _sam_has_talked(room: RoomHarness) -> None:
    await room.say("sam", "evening all", first_pass="stay_quiet", look="stay_quiet")


def _first_read(room: RoomHarness, look_on: bool) -> str:
    """What read the message first: the quick vote, or her first look."""
    if look_on:
        assert room.cadence_prompts == []                # one look in place of the vote
        return room.look_prompts[-1]
    return room.cadence_prompts[-1]


def _hers(line: str, look_on: bool) -> str:
    """The vote's names line in the third person; the look's in her first."""
    return line.replace("(her)", "(me)").replace("not her)", "not me)") if look_on else line


def _rules(look_on: bool) -> str:
    return RULES_START if look_on else C_START


# ── 1. a name for someone else (the 10-11 message) ──────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("look_on", LOOK)
@pytest.mark.parametrize("door", DOORS)
async def test_the_owner_calls_someone_else_by_name(monkeypatch, tmp_path, door, look_on):
    room = _room(monkeypatch, tmp_path, door, look_on)
    await _sam_has_talked(room)
    await room.say("owner", "hey Sam, you around?", model_chooses="stay_quiet",
                   reason="it's for Sam")
    vote = _flat(_first_read(room, look_on))
    assert _hers('Names in this message: "Sam" (someone in this room, not her)', look_on) in vote
    assert _rules(look_on) in vote
    assert 'Names in this message: "Sam" (someone in this room, not me)' in room.last_run_context
    assert _flat(ROOM_READING_INSTRUCTION) in _flat(room.last_run_context)
    assert room.sent == []


# ── 2. a nickname for her ───────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("look_on", LOOK)
@pytest.mark.parametrize("door", DOORS)
async def test_a_nickname_the_owner_taught_is_hers(monkeypatch, tmp_path, door, look_on):
    from core import room_people

    room = _room(monkeypatch, tmp_path, door, look_on)
    room_people.teach(room.room_key(), "Pol", means_me=True)
    await room.say("owner", "hey Pol, what time is it?", model_chooses="answer")
    assert _hers('Names in this message: "Pol" (her)', look_on) in _first_read(room, look_on)
    assert 'Names in this message: "Pol" (me)' in room.last_run_context
    assert "The owner told me I also go by Pol here." in room.last_run_context


# ── 3. a question to the room, 4. a joke aimed at nobody ────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("text", ["anyone know a good pizza place?",
                                  "why did the chicken cross the road lol"])
@pytest.mark.parametrize("look_on", LOOK)
async def test_a_guest_says_something_to_nobody_in_particular(monkeypatch, tmp_path, door, text,
                                                              look_on):
    room = _room(monkeypatch, tmp_path, door, look_on)
    await room.say("sam", text, first_pass="stay_quiet", look="stay_quiet")
    first_read = _flat(_first_read(room, look_on))
    assert "Names in this message: none" in first_read
    assert _rules(look_on) in first_read
    assert room.full_runs == 0


# ── 5. an echo of her own words ─────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("look_on", LOOK)
@pytest.mark.parametrize("door", DOORS)
async def test_a_guest_echoes_her_words(monkeypatch, tmp_path, door, look_on):
    room = _room(monkeypatch, tmp_path, door, look_on)
    await room.say("sam", 'palli said "see you at 9" lmao', first_pass="stay_quiet",
                   look="stay_quiet")
    assert _hers('Names in this message: "palli" (her)', look_on) in _first_read(room, look_on)
    assert room.full_runs == 0


# ── 6. two people talking about her ─────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("look_on", LOOK)
@pytest.mark.parametrize("door", DOORS)
async def test_two_guests_talk_about_her(monkeypatch, tmp_path, door, look_on):
    room = _room(monkeypatch, tmp_path, door, look_on)
    await room.say("jo", "my bot thing is broken again", first_pass="stay_quiet",
                   look="stay_quiet")
    await room.say("sam", "Jo, did polly fix it last time?", first_pass="answer",
                   look="answer", model_chooses="answer")
    first_read = _first_read(room, look_on)
    assert _hers('Names in this message: "Jo" (someone in this room, not her) · '
                 '"polly" (her)', look_on) in first_read
    # The guest's reply call sees the same facts, in her own words.
    assert ('Names in this message: "Jo" (someone in this room, not me) · '
            '"polly" (me)') in room.last_run_context


# ── 7. the owner goes on talking with her without saying her name ───────────

@pytest.mark.asyncio
@pytest.mark.parametrize("look_on", LOOK)
@pytest.mark.parametrize("door", DOORS)
async def test_the_owner_goes_on_without_her_name(monkeypatch, tmp_path, door, look_on):
    room = _room(monkeypatch, tmp_path, door, look_on)
    await room.say("owner", "palli, what's 12 times 4?", model_chooses="answer",
                   answer_text="48")
    room.client._coalescer.mark_replied(room.room)
    await room.say("owner", "and times 3?", model_chooses="answer", answer_text="144")
    assert "Names in this message: none" in _first_read(room, look_on)
    assert "Names in this message: none" in room.last_run_context
    assert room.sent[-1] == "144"


# ── a DM is today's, word for word ──────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("look_on", LOOK)
async def test_a_dm_sees_none_of_it(monkeypatch, tmp_path, look_on):
    dm = DmHarness(monkeypatch, tmp_path, first_look=look_on)
    dm.model_answers("not sure who Alex is")
    await dm.says("hey Alex, you around?")
    kw = dm.calls[-1]
    reads = dm.look_prompts if look_on else dm.cadence_prompts
    everything = " ".join([*dm.cadence_prompts, *dm.look_prompts, str(kw.get("system") or ""),
                           str(kw.get("system_tail") or ""),
                           str(kw.get("trailing_system_note") or "")])
    assert reads and "hey Alex" in reads[-1]
    assert "Names in this message" not in everything
    assert C_START not in _flat(everything) and RULES_START not in _flat(everything)
    assert _flat(ROOM_READING_INSTRUCTION) not in _flat(everything)


# ── her first read gets the same facts, in a room only ──────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("look_on", LOOK)
@pytest.mark.parametrize("door", DOORS)
async def test_her_first_read_is_handed_the_names_line_in_a_room(monkeypatch, tmp_path, door,
                                                                 look_on):
    """Recall is handed the names line either way; with the look on, recall's
    own first read stands down (ai.turn_read.first_look_in_charge), and the
    names reach the look itself (above)."""
    from core.agent import room_tail

    room = _room(monkeypatch, tmp_path, door, look_on)
    seen: list[str] = []
    import core.turn.recall as recall_mod

    stand_in = recall_mod.build_turn_context

    async def recall(turn):
        seen.append(room_tail.current_room_names.get())
        return await stand_in(turn)

    monkeypatch.setattr("core.turn.recall.build_turn_context", recall)
    await _sam_has_talked(room)
    await room.say("owner", "hey Sam, you around?", model_chooses="stay_quiet")
    assert seen[-1] == 'Names in this message: "Sam" (someone in this room, not me)'
    assert room_tail.current_room_names.get() == ""          # reset after recall


@pytest.mark.asyncio
async def test_a_dms_first_read_gets_no_names_line(monkeypatch, tmp_path):
    from core.agent import room_tail

    dm = DmHarness(monkeypatch, tmp_path)
    seen: list[str] = []
    import core.turn.recall as recall_mod

    stand_in = recall_mod.build_turn_context

    async def recall(turn):
        seen.append(room_tail.current_room_names.get())
        return await stand_in(turn)

    monkeypatch.setattr("core.turn.recall.build_turn_context", recall)
    dm.model_answers("hi")
    await dm.says("hey Sam, you around?")
    assert seen == [""]
