"""Her first look on friends' room messages, on both doors (R14, T4; plan
docs/2026-10-12-one-first-look-plan.md §2.3, §2.4, §2.6, §2.9, §2.10).

With ``companion.first_look`` on, a guest's room message gets her own first
look in place of the quick vote: the same facts the vote had (the room, the
names, the signals, the owner's room note), now in her first person, plus who
is in the room. Its decision feeds the room pass as the vote's did: a guest's
quiet is final and on the record with her reason, a wait pauses and looks
once more (Draft 4), an answer carries the look on the turn so nothing asks
again. The owner's messages still skip the room pass (ruling T2b-1 → B).

The standing seven kinds of message (tests/bots/test_room_reading_scenarios.py)
run here with the look on, each as a friend's message (the owner's go to the
runner, T5). What she then decides is the model's: these tests prove the
facts arrive. Stand-in names only: Robin is the owner; Sam, Alex and Jo are
guests.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import pytest

from core.agent import first_look
from tests.bots.room_harness import GUEST_IDS, RoomHarness, room_look

DOORS = ["discord", "telegram"]
RULES_START = " ".join(first_look.ROOM_RULES.split())[:40]

pytestmark = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)


def _flat(text: str) -> str:
    return " ".join(str(text).split())


@pytest.fixture(autouse=True)
def _people_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_ROOM_PEOPLE_PATH", str(tmp_path / "room_people.json"))


def _room(monkeypatch, tmp_path, door: str, *, look_on: bool = True) -> RoomHarness:
    """The room harness with her first look on, each Telegram sender showing
    their own name (the shared fake shows one name for everyone)."""
    room = RoomHarness(monkeypatch, tmp_path, door=door, first_look=look_on)
    if door == "telegram":
        build = room._telegram_update

        def named(who: str, text: str):
            update = build(who, text)
            update.message.from_user.full_name = "Robin" if who == "owner" else who.capitalize()
            return update

        room._telegram_update = named
    return room


def _where(door: str) -> str:
    return "This is a group chat on Discord: #general" if door == "discord" else "This is a Telegram group"


async def _sam_has_talked(room: RoomHarness) -> None:
    await room.say("sam", "evening all", look="stay_quiet")


# ── the seven kinds, each a friend's message, with the look on ─────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_1_a_friend_calls_someone_else_by_name(monkeypatch, tmp_path, door):
    room = _room(monkeypatch, tmp_path, door)
    await _sam_has_talked(room)
    await room.say("jo", "hey Sam, you around?", look="stay_quiet", reason="Jo is calling Sam")

    look = room.look_prompts[-1]
    assert 'Names in this message: "Sam" (someone in this room, not me)' in look
    # Who's who: the room card's lines, without its heading.
    assert _where(door) in look and "Recently here, newest first: Jo, Sam." in look
    assert "## This Room" not in look
    assert RULES_START in _flat(look)
    # In place of the vote: the quick vote never ran, nothing was dispatched.
    assert room.cadence_prompts == [] and room.full_runs == 0 and room.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_2_a_nickname_the_owner_taught_is_hers_on_a_friends_line_too(monkeypatch, tmp_path, door):
    from core import room_people

    room = _room(monkeypatch, tmp_path, door)
    room_people.teach(room.room_key(), "Pol", means_me=True)
    await room.say("sam", "hey Pol, what time is it?", look="answer", model_chooses="answer")

    look = room.look_prompts[-1]
    assert 'Names in this message: "Pol" (me)' in look
    # The owner-taught nicknames line is the owner's turns' only (_whos_who).
    assert "The owner told me I also go by" not in look
    assert room.full_runs == 1 and room.cadence_prompts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("text", ["anyone know a good pizza place?",
                                  "why did the chicken cross the road lol"])
async def test_3_4_a_friend_says_something_to_nobody_in_particular(monkeypatch, tmp_path, door, text):
    room = _room(monkeypatch, tmp_path, door)
    await room.say("sam", text, look="stay_quiet")

    look = _flat(room.look_prompts[-1])
    assert "Names in this message: none" in look
    assert "About the latest message (signals to weigh, never rules): said in a shared room." in look
    assert RULES_START in look
    assert room.full_runs == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_5_a_friend_echoes_her_words(monkeypatch, tmp_path, door):
    room = _room(monkeypatch, tmp_path, door)
    await room.say("sam", 'palli said "see you at 9" lmao', look="stay_quiet")

    look = room.look_prompts[-1]
    assert 'Names in this message: "palli" (me)' in look
    # The signals in her first person (Draft 5), never the vote's "her".
    assert "it says my name" in look and "it says her name" not in look


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_6_two_friends_talk_about_her(monkeypatch, tmp_path, door):
    room = _room(monkeypatch, tmp_path, door)
    await room.say("jo", "my bot thing is broken again", look="stay_quiet")
    await room.say("sam", "Jo, did polly fix it last time?", look="answer", model_chooses="answer")

    look = room.look_prompts[-1]
    assert 'Names in this message: "Jo" (someone in this room, not me) · "polly" (me)' in look
    assert "Recently here, newest first: Sam, Jo" in look
    # The friend's reply call sees the same facts, in her own words (today's).
    assert ('Names in this message: "Jo" (someone in this room, not me) · '
            '"polly" (me)') in room.last_run_context


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_7_a_friend_goes_on_without_her_name(monkeypatch, tmp_path, door):
    room = _room(monkeypatch, tmp_path, door)
    await room.say("sam", "palli, what's 12 times 4?", look="answer", model_chooses="answer",
                   answer_text="48")
    await room.say("sam", "and times 3?", look="answer", model_chooses="answer",
                   answer_text="144")

    look = room.look_prompts[-1]
    assert "Names in this message: none" in look
    # The room as anyone there reads it: her last answer is in the tail, the
    # new line is the message, once.
    conversation, message = look.split(first_look._ROOM_LABEL, 1)
    assert first_look.ROOM_CONVERSATION_LABEL in conversation
    assert "PaLLi: 48" in conversation and "and times 3?" not in conversation
    assert "[Sam]: and times 3?" in message
    assert room.sent[-1] == "144" and room.cadence_prompts == []


# ── a friend's look sees no slice of her mind about the owner ─────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_friends_look_has_no_slice(monkeypatch, tmp_path, door):
    room = _room(monkeypatch, tmp_path, door)
    await room.say("sam", "hey polly", look="answer", model_chooses="answer")
    look = room.look_prompts[-1]
    assert "HOW THINGS ARE BETWEEN US" not in look
    assert "WHAT'S ON MY MIND" not in look
    assert "MY OWN LIFE LATELY" not in look


# ── a friend's quiet is final, and on the record with her reason ───────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_friends_quiet_is_final_and_recorded_with_the_looks_reason(monkeypatch, tmp_path, door, caplog):
    room = _room(monkeypatch, tmp_path, door)
    caplog.set_level(logging.INFO, logger="bots.common.room_pass")
    await room.say("sam", "did you see the game last night?", look="stay_quiet",
                   reason="Sam is talking to Alex, not me")

    assert room.sent == [] and room.full_runs == 0 and room.calls == []
    assert len(room.look_prompts) == 1 and room.cadence_prompts == []
    assert await room.file_has("did you see the game")
    row = room.quiet_record()[-1]
    assert row["source"] == "first_pass"
    assert row["reason"] == "Sam is talking to Alex, not me"
    assert row["who"]["id"] == str(GUEST_IDS["sam"]) and row["who"]["owner"] is False
    # Draft 6's log line.
    assert (f"first look: stay_quiet in {room.room_key()} (1 message) — "
            "Sam is talking to Alex, not me") in caplog.text


# ── an answer carries the look; nothing asks again ─────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_an_answer_carries_the_look_on_the_turn(monkeypatch, tmp_path, door):
    from core.agent import reply_governor

    room = _room(monkeypatch, tmp_path, door)
    room.look_plan.append(room_look("answer", "Sam asks me", length="short",
                                    how_they_seem="HOW-WORDS curious", for_me="FOR-WORDS me",
                                    my_own="MINE-WORDS a thought"))
    room.model_answers("sure")
    await room.arrive("sam", "polly, quick one?")
    await room.settle()

    carried = room.dispatched[-1].metadata.extra["first_pass"]
    assert (carried["decision"], carried["reason"], carried["length"]) == ("answer", "Sam asks me", "short")
    # No room note stands: her three texts ride on the turn (the reply's read
    # section reads them, T5).
    assert (carried["how_they_seem"], carried["for_me"], carried["my_own"]) == (
        "HOW-WORDS curious", "FOR-WORDS me", "MINE-WORDS a thought")
    assert carried["observation_text"] == ""
    assert room.cadence_prompts == []                       # the runner did not ask again
    note = reply_governor.render_shape_note("short", tools=False)
    assert note and note in (room.calls[-1].get("trailing_system_note") or "")
    assert room.sent == ["sure"]


# ── the room note: the look reads its words; a friend's reply never does ──

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_friends_look_texts_never_reach_the_reply_while_a_note_stands(monkeypatch, tmp_path, door):
    from core import room_notes

    room = _room(monkeypatch, tmp_path, door)
    room_notes.set_note(room.room_key(), "NOTE-WORDS stay quiet in here until tomorrow",
                        datetime.now() + timedelta(hours=20))
    room.look_plan.append(room_look(
        "answer", "REASON-WORDS the owner said NOTE-WORDS, but Sam asks me directly",
        how_they_seem="HOW-WORDS Sam is curious despite NOTE-WORDS",
        for_me="FOR-WORDS me, by name", my_own="MINE-WORDS about NOTE-WORDS"))
    room.model_answers("I'm here")
    await room.arrive("sam", "polly, you around?")
    await room.settle()

    # The look is a first read (ruling T2b-5b): it sees the note's words.
    look = room.look_prompts[-1]
    assert '"NOTE-WORDS stay quiet in here until tomorrow"' in look
    assert look.index("Names in this message:") < look.index("The owner's note for this room")
    carried = room.dispatched[-1].metadata.extra["first_pass"]
    assert all(carried[k] == "" for k in ("how_they_seem", "for_me", "my_own", "reason",
                                          "observation_text"))
    assert carried["decision"] == "answer"
    everything = json.dumps({k: str(v) for k, v in room.calls[-1].items()})
    for marker in ("NOTE-WORDS", "HOW-WORDS", "FOR-WORDS", "MINE-WORDS", "REASON-WORDS"):
        assert marker not in everything
    assert room.sent == ["I'm here"]


# ── wait, the pause, the second look (Draft 4) ─────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_wait_pauses_and_the_second_look_carries_draft_four(monkeypatch, tmp_path, door, caplog):
    room = _room(monkeypatch, tmp_path, door)
    caplog.set_level(logging.INFO, logger="bots.common.room_pass")
    await room.say("sam", "so anyway...", look="wait")
    assert room.full_runs == 0 and await room.file_has("so anyway...")
    assert first_look.SECOND_LOOK_LINE not in room.look_prompts[-1]

    room.plan(look="stay_quiet", reason="Sam trailed off; nothing for me")
    await room.pass_the_pause()

    second = room.look_prompts[-1]
    assert first_look.SECOND_LOOK_LINE in second
    assert "This is your second look" not in second          # the vote's line, retired here
    assert len(room.look_prompts) == 2 and room.cadence_prompts == []
    assert room.full_runs == 0 and room.sent == []
    row = room.quiet_record()[-1]
    assert row["reason"] == "Sam trailed off; nothing for me" and row["source"] == "first_pass"
    assert (f"first look: stay_quiet in {room.room_key()} (1 message, after a pause) — "
            "Sam trailed off; nothing for me") in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_wait_at_the_second_look_goes_to_her_full_turn(monkeypatch, tmp_path, door):
    room = _room(monkeypatch, tmp_path, door)
    await room.say("sam", "so anyway...", look="wait")
    room.plan(look="wait", model_chooses="answer", answer_text="go on, Sam")
    await room.pass_the_pause()

    assert room.full_runs == 1 and room.sent == ["go on, Sam"]
    assert room.client._room_pass.last_outcome.decision == "answer"
    assert "her full turn decides" in room.client._room_pass.last_outcome.reason
    assert len(room.look_prompts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_wait_is_read_with_the_next_message_each_on_its_own_line(monkeypatch, tmp_path, door, caplog):
    room = _room(monkeypatch, tmp_path, door)
    caplog.set_level(logging.INFO, logger="bots.common.room_pass")
    await room.say("sam", "you won't believe this", look="wait")
    await room.say("sam", "my car got towed", look="answer", model_chooses="answer")

    message = room.look_prompts[-1].split(first_look._ROOM_LABEL, 1)[1]
    assert "[Sam]: you won't believe this\n[Sam]: my car got towed" in message
    assert room.full_runs == 1
    content = room.dispatched[-1].content                 # one turn, both lines, in order
    assert content.index("you won't believe this") < content.index("my car got towed")
    assert f"first look: answer in {room.room_key()} (2 messages) — " in caplog.text


# ── the owner's burst still skips the room pass ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_the_owners_burst_still_skips_the_room_pass(monkeypatch, tmp_path, door):
    room = _room(monkeypatch, tmp_path, door)
    await _sam_has_talked(room)
    looks_before = len(room.look_prompts)
    await room.say("owner", "hey Sam, you around?", model_chooses="stay_quiet",
                   reason="it's for Sam")

    # No first look in the room pass: the owner's one look is the runner's
    # (R14, T5; tests/agent/test_first_look_runner.py), read with her slice.
    assert len(room.look_prompts) == looks_before + 1
    assert "HOW THINGS ARE BETWEEN US" in room.look_prompts[-1]
    assert "first_pass" not in (room.dispatched[-1].metadata.extra or {})
    assert room.client._room_pass.last_outcome.decision == "stay_quiet"   # Sam's, not the owner's
    assert room.full_runs == 1 and room.sent == []


# ── a failed look answers (Q8) ─────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("failure", [RuntimeError("the call broke"), "not a look at all"])
async def test_a_failed_look_answers(monkeypatch, tmp_path, door, failure, caplog):
    room = _room(monkeypatch, tmp_path, door)
    caplog.set_level(logging.INFO)
    room.look_plan.append(failure)
    room.model_answers("hi Sam")
    await room.arrive("sam", "polly, you there?")
    await room.settle()

    assert room.full_runs == 1 and room.sent == ["hi Sam"]
    assert room.dispatched[-1].metadata.extra["first_pass"]["decision"] == "answer"
    assert room.dispatched[-1].metadata.extra["first_pass"]["observation_text"] == ""
    assert room.cadence_prompts == []                        # no vote behind the failed look
    assert "first look: no look (lane first_look)" in caplog.text
    assert f"first look: answer in {room.room_key()} (1 message) — no first look" in caplog.text


# ── with the look off, the room is today's vote ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_with_the_look_off_the_room_pass_takes_todays_vote(monkeypatch, tmp_path, door):
    room = _room(monkeypatch, tmp_path, door, look_on=False)
    await room.say("sam", "palli said hi lmao", first_pass="stay_quiet")
    assert room.look_prompts == []
    assert len(room.cadence_prompts) == 1
    vote = room.cadence_prompts[-1]
    assert 'Names in this message: "palli" (her)' in vote and "it says her name" in vote
    assert first_look.SECOND_LOOK_LINE not in vote


# ── the block itself ───────────────────────────────────────────────────────

def test_the_second_look_line_is_draft_four():
    assert first_look.SECOND_LOOK_LINE == (
        'This is my second look at these messages, after a pause with nothing more '
        'from them: I decide "answer" or "stay_quiet"; "wait" is not an option now.')
