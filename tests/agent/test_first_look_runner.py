"""Her first look on human turns, in the runner (R14, T5; plan
docs/2026-10-12-one-first-look-plan.md §2.3, §2.6, §2.7; Drafts 2, 3, 6).

With ``companion.first_look`` on, a human turn with no carry from the room
pass — the owner's message in a DM or a room (Q7: everywhere "she decides"
applies) — takes ONE first look, beside recall, in fix 6's slot: no quick
vote and no first read in recall (``ai.turn_read.first_look_in_charge``).
The look gives the reflection its mood read (``as_turn_read``), the reply its
read section (Draft 2's body under today's heading and framing, Q4), and the
shape (``as_cadence_config``: no vote observation). When the look the runner
took says let it pass or wait — the owner's turn, or a friend's DM (the
coordinator's ruling after T5) — the length note is conditional and the
very last line of the trailing note is the look's own (Draft 3, variant A) —
the R14 gap behind 10-12 17:56's "Standing by.". Her reply still decides
(T2a-2): only the stay_quiet control call is quiet.

A friend's room turn takes no second look: the room pass's carry gives the
friend's reply its read section, blank while the owner's room note stands.

Off, or with she_decides on and the look off, every call and prompt is
today's. Stand-in names only: Robin is the owner; Sam and Jo are guests.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent import first_look, reply_governor
from tests.agent.she_decides_harness import DmHarness
from tests.bots.room_harness import GUEST_IDS, RoomHarness, room_look

pytestmark = pytest.mark.filterwarnings(
    r"ignore:datetime\.datetime\.utcnow\(\) is deprecated:DeprecationWarning"
)

READ_HEADING = "## Your read of this moment"
QUIET_FRAMING = ("Private awareness, not instructions — let it quietly inform "
                 "how you show up. Most turns it changes nothing visible.")
VOTE_OBSERVATION = "a plain message"            # the DM harness's vote says this


def look(decision: str = "answer", reason: str = "", *, length: str = "short",
         how: str = "HOW-WORDS relaxed, chatting", for_me: str = "FOR-WORDS a line to me",
         my_own: str = "", weight: float = 0.3, needs_support: float = 0.1,
         carries: str = "CARRY-WORDS the survey", worthy: str = "WORTHY-WORDS a new job") -> str:
    return json.dumps({
        "how_they_seem": how, "weight": weight, "needs_support": needs_support,
        "carries_forward": carries, "memory_worthy": worthy,
        "for_me": for_me, "my_own": my_own, "decision": decision,
        "reason": reason or {"answer": "they asked me", "wait": "they trailed off",
                             "stay_quiet": "it's for Sam"}.get(decision, ""),
        "length": length, "register": "neutral", "phase": "settled_conversation",
        "target_beats": 1, "emotional_context": "neutral",
    })


@pytest.fixture(autouse=True)
def _people_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PALLI_ROOM_PEOPLE_PATH", str(tmp_path / "room_people.json"))


class Mind:
    """What the look, recall's read and the reflection saw: recall is a
    stand-in that keeps recall's own gate (``turn_read.read_applies``) and
    returns a real TurnContext, so the adapter, the read section and the
    reflection's input are the real ones."""

    def __init__(self, monkeypatch, *, look_on: bool = True, room: Any = None) -> None:
        import ai.turn_read as turn_read
        from ai.turn_read import TurnRead
        from core.turn.types import TurnContext

        self.looks: list[Any] = []
        self.look_prompts: list[str] = []
        self.reads = 0
        self.in_charge: list[bool] = []
        self.slices: list[bool] = []
        self.reflected: list[Any] = []
        if look_on:
            monkeypatch.setattr("core.agent.first_look.enabled", lambda: True)

        async def look_llm(prompt: str) -> str:
            self.look_prompts.append(prompt)
            step = self.looks.pop(0) if self.looks else look("answer")
            if isinstance(step, BaseException):
                raise step
            return step

        if room is not None:
            # A room: one look queue for the room pass and the runner alike
            # (the room harness's own), so the order of looks is the room's.
            self.looks, self.look_prompts = room.look_plan, room.look_prompts
        else:
            monkeypatch.setattr("core.agent.first_look.build_llm", lambda: look_llm)

        async def gather_slice(turn: Any, *, owner: bool, shown: Any = None) -> dict[str, str]:
            self.slices.append(owner)
            if not owner:
                return {"relationship_posture": "", "emotional_arc": "", "stakes": "", "tonight": ""}
            return {"relationship_posture": "POSTURE-WORDS close", "emotional_arc": "ARC-WORDS a calm week",
                    "stakes": "", "tonight": ""}

        monkeypatch.setattr("core.agent.first_look.gather_slice", gather_slice)

        async def take_turn_read(**kw: Any) -> Any:
            self.reads += 1
            return TurnRead(read="READ-WORDS recall's own read", weight=0.2, needs_support=0.0,
                            carries_forward="", memory_worthy="", created_at=0.0)

        monkeypatch.setattr(turn_read, "take_turn_read", take_turn_read)

        async def recall(turn: Any) -> Any:
            self.in_charge.append(bool(getattr(turn_read, "first_look_in_charge").get()))
            read = await turn_read.take_turn_read(message=str(turn.content)) \
                if turn_read.read_applies(turn) else None
            return TurnContext(turn=turn, turn_read=read,
                               emotional_arc_text="RECALL-ARC" if read is not None else "")

        monkeypatch.setattr("core.turn.recall.build_turn_context", recall)

        async def reflect(ctx: Any, response_text: str, decision: Any = None) -> None:
            self.reflected.append(ctx)

        monkeypatch.setattr("core.turn.reflect.apply_reflection", reflect)


def _note(call: dict[str, Any]) -> str:
    return str(call.get("trailing_system_note") or "")


def _tail(call: dict[str, Any]) -> str:
    return str(call.get("system_tail") or "")


async def _flush() -> None:
    from core.agent.runner import flush_detached_tasks

    await flush_detached_tasks()


def _dm(monkeypatch, tmp_path, *, look_on: bool = True,
        guest_search: bool = False) -> tuple[DmHarness, Mind]:
    harness = DmHarness(monkeypatch, tmp_path, guest_search=guest_search)
    return harness, Mind(monkeypatch, look_on=look_on)


def _room(monkeypatch, tmp_path, door: str = "discord", *,
          look_on: bool = True) -> tuple[RoomHarness, Mind]:
    room = RoomHarness(monkeypatch, tmp_path, door=door, first_look=look_on)
    mind = Mind(monkeypatch, look_on=look_on, room=room)
    if door == "telegram":
        build = room._telegram_update

        def named(who: str, text: str):
            update = build(who, text)
            update.message.from_user.full_name = "Robin" if who == "owner" else who.capitalize()
            return update

        room._telegram_update = named
    return room, mind


# ── one call, not three ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_with_the_look_on_the_owners_dm_takes_one_look_and_no_vote_or_read(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)
    dm.model_answers("morning!")
    await dm.says("morning palli")

    assert len(mind.look_prompts) == 1
    assert dm.cadence_prompts == []                 # the quick vote never ran
    assert mind.reads == 0                           # recall's first read stood down
    assert mind.in_charge == [True]                  # the switch was on while recall ran
    assert mind.slices == [True]                     # the owner's slice
    assert dm.sent == ["morning!"]
    # A DM's look has no room lines (Draft 1).
    assert first_look.ROOM_CONVERSATION_LABEL not in mind.look_prompts[0]
    assert "morning palli" in mind.look_prompts[0].split(first_look._DM_LABEL, 1)[1]


@pytest.mark.asyncio
async def test_the_switch_is_down_again_after_the_turn(monkeypatch, tmp_path):
    import ai.turn_read as turn_read

    dm, mind = _dm(monkeypatch, tmp_path)
    await dm.says("hi")
    assert turn_read.first_look_in_charge.get() is False


# ── the reflection's read, the arc ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_reflection_gets_the_looks_mood_read_and_the_arc(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("answer", my_own="MINE-WORDS the boat", weight=0.55, needs_support=0.2))
    dm.model_answers("glad to hear it")
    await dm.says("got the job!")
    await _flush()

    ctx = mind.reflected[-1]
    read = ctx.turn_read
    # how they seem only (9e2e745): the arc stays a mood record.
    assert read.read == "HOW-WORDS relaxed, chatting"
    assert (read.weight, read.needs_support) == (0.55, 0.2)
    assert (read.carries_forward, read.memory_worthy) == ("CARRY-WORDS the survey", "WORTHY-WORDS a new job")
    assert ctx.emotional_arc_text == "ARC-WORDS a calm week"
    # The reply's arc section reads the same arc (today's section, today's words).
    assert "ARC-WORDS a calm week" in _tail(dm.calls[-1])


# ── the reply's read section (Draft 2, Q4) ─────────────────────────────────

@pytest.mark.asyncio
async def test_the_owners_reply_has_draft_twos_body_under_todays_heading(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("answer", my_own="MINE-WORDS the boat"))
    dm.model_answers("ok")
    await dm.says("what's up")

    tail = _tail(dm.calls[-1])
    section = (f"{READ_HEADING}\n{QUIET_FRAMING}\n"
               "HOW-WORDS relaxed, chatting\n"
               f"{first_look.FOR_ME_LABEL} FOR-WORDS a line to me\n"
               f"{first_look.MY_OWN_LABEL} MINE-WORDS the boat")
    assert section in tail
    assert tail.count(READ_HEADING) == 1
    # No vote ran, so no vote observation; "## This turn" says nothing for a
    # short one-beat look.
    assert VOTE_OBSERVATION not in tail and "## This turn" not in tail


@pytest.mark.asyncio
async def test_no_my_own_line_when_she_has_nothing_of_her_own(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)
    dm.model_answers("ok")
    await dm.says("what's up")
    tail = _tail(dm.calls[-1])
    assert f"{first_look.FOR_ME_LABEL} FOR-WORDS a line to me" in tail
    assert first_look.MY_OWN_LABEL not in tail


@pytest.mark.asyncio
async def test_the_shape_comes_from_the_look(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("answer", length="medium"))
    dm.model_answers("ok")
    await dm.says("tell me about your day")
    assert _note(dm.calls[-1]) == reply_governor.render_shape_note("medium", tools=True)


# ── answer: the trailing note is today's, byte for byte ────────────────────

@pytest.mark.asyncio
async def test_on_answer_the_dm_trailing_note_is_todays(monkeypatch, tmp_path):
    today, _ = _dm(monkeypatch, tmp_path / "today", look_on=False)
    today.model_answers("ok")
    await today.says("how's it going?")
    before = _note(today.calls[-1])

    dm, mind = _dm(monkeypatch, tmp_path / "look")
    mind.looks.append(look("answer", length="short"))
    dm.model_answers("ok")
    await dm.says("how's it going?")
    assert before and _note(dm.calls[-1]) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_on_answer_the_room_trailing_note_is_todays(monkeypatch, tmp_path, door):
    async def owner_turn(room: RoomHarness) -> str:
        await room.say("sam", "evening all", look="stay_quiet", first_pass="stay_quiet")
        room.plan(first_pass="answer", look="answer", model_chooses="answer")
        await room.arrive("owner", "hey Sam, you around?")
        await room.settle()
        return _note(room.calls[-1])

    today, _ = _room(monkeypatch, tmp_path / "today", door, look_on=False)
    before = await owner_turn(today)
    assert 'Names in this message: "Sam"' in before

    room, mind = _room(monkeypatch, tmp_path / "look", door)
    assert await owner_turn(room) == before


# ── stay_quiet / wait on a turn the runner reads: Draft 3 ──────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("decision,line", [
    ("stay_quiet", "(My first look: let it pass — it's for Sam.)"),
    ("wait", "(My first look: this looks unfinished, more may be coming — they trailed off.)"),
])
async def test_when_the_look_holds_back_the_last_line_is_hers(monkeypatch, tmp_path, decision, line):
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look(decision, length="short"))
    dm.model_answers("ok")
    result = await dm.says("so anyway")

    note = _note(dm.calls[-1])
    assert note.splitlines()[-1] == line
    assert note.splitlines()[0] == reply_governor.render_shape_note("short", tools=True, look=decision)
    assert note.splitlines()[0].startswith("(If I do answer, it's short:")
    assert "This one wants a short answer" not in note
    # T2a-2 stands: the look never stops the owner's reply.
    assert result.responded and dm.sent == ["ok"]


@pytest.mark.asyncio
async def test_the_medium_form_is_conditional_too(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("stay_quiet", length="medium"))
    dm.model_answers("ok")
    await dm.says("so anyway")
    first = _note(dm.calls[-1]).splitlines()[0]
    assert first.startswith("(If I do answer, a few sentences fit:")
    assert first == reply_governor.render_shape_note("medium", tools=True, look="stay_quiet")


@pytest.mark.asyncio
async def test_a_long_look_that_holds_back_still_ends_with_her_line(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("stay_quiet", length="long"))
    dm.model_answers("ok")
    await dm.says("so anyway")
    assert _note(dm.calls[-1]) == "(My first look: let it pass — it's for Sam.)"


@pytest.mark.asyncio
async def test_her_reply_can_still_choose_quiet(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("stay_quiet"))
    dm.model_chooses_quiet("it's for Sam")
    result = await dm.says("so anyway")
    assert dm.sent == [] and not result.responded


@pytest.mark.asyncio
async def test_the_runner_says_what_the_look_said(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="core.agent.runner")
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("stay_quiet"))
    await dm.says("so anyway")
    assert "runner: first look said stay_quiet on a human turn (origin=user); her reply decides" in caplog.text
    assert "the quick vote said" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_names_and_voice_lines_keep_their_places_before_her_line(monkeypatch, tmp_path, door):
    room, mind = _room(monkeypatch, tmp_path, door)
    monkeypatch.setattr("core.voice_replies.status_line_for",
                        lambda turn, *, owner: "VOICE-LINE voice replies are on here")
    await room.say("sam", "evening all", look="stay_quiet")
    mind.looks.append(look("stay_quiet", length="short"))
    room.model_answers("ok")
    await room.arrive("owner", "hey Sam, you around?")
    await room.settle()

    lines = _note(room.calls[-1]).splitlines()
    assert lines[0] == reply_governor.render_shape_note("short", tools=True, look="stay_quiet")
    assert lines[1].startswith('Names in this message: "Sam"')
    assert lines[2] == "VOICE-LINE voice replies are on here"
    assert lines[3] == "(My first look: let it pass — it's for Sam.)" and len(lines) == 4


# ── the owner's room turn: the look reads the room ─────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_the_owners_room_look_reads_the_room_as_the_vote_did(monkeypatch, tmp_path, door):
    room, mind = _room(monkeypatch, tmp_path, door)
    await room.say("sam", "evening all", look="stay_quiet")
    room_looks = len(mind.look_prompts)
    room.model_answers("ok")
    await room.arrive("owner", "hey Sam, you around?")
    await room.settle()

    assert len(mind.look_prompts) == room_looks + 1         # one look, the runner's
    assert room.cadence_prompts == [] and mind.reads == 0
    prompt = mind.look_prompts[-1]
    assert first_look.ROOM_CONVERSATION_LABEL in prompt and first_look._ROOM_LABEL in prompt
    assert 'Names in this message: "Sam" (someone in this room, not me)' in prompt
    assert ("This is a group chat on Discord: #general" if door == "discord"
            else "This is a Telegram group") in prompt
    assert "POSTURE-WORDS close" in prompt                   # the owner's slice
    # The room as anyone there reads it holds Sam's line; the message is the owner's.
    conversation, message = prompt.split(first_look._ROOM_LABEL, 1)
    assert "evening all" in conversation.split(first_look.ROOM_CONVERSATION_LABEL, 1)[1]
    assert "hey Sam, you around?" in message and "evening all" not in message


@pytest.mark.asyncio
async def test_the_1756_shape_a_mention_then_a_follow_up_in_one_burst(monkeypatch, tmp_path):
    """10-12 17:56, stand-ins only: Robin @mentions Sam with a status update,
    then a follow-up with no mention 13 s later, coalesced into one turn. Her
    look lets it pass: the trailing note ends with her line, and the length
    note is the conditional one — not "This one wants a short answer"."""
    room, mind = _room(monkeypatch, tmp_path, "discord")
    await room.say("sam", "evening all", look="stay_quiet")
    mind.looks.append(look("stay_quiet", "a status update to Sam, not for me", length="short",
                           for_me="the first line calls Sam; the second goes on to him"))
    room.model_answers("Standing by.")

    first = room._discord_message("owner", f"<@{GUEST_IDS['sam']}> the build is green now")
    first.mentions = [SimpleNamespace(id=GUEST_IDS["sam"], display_name="Sam",
                                      name="sam_example", bot=False)]
    await room.client.on_message(first)
    await room.client.on_message(room._discord_message("owner", "merging it after dinner"))
    await room.client._fire_burst(room.room)
    room.client._coalescer.mark_replied(room.room)
    await room.settle()

    assert room.full_runs == 1                       # Sam's line was let pass; one owner's turn
    turn = room.dispatched[-1]
    assert "the build is green now" in turn.content and "merging it after dinner" in turn.content
    message = mind.look_prompts[-1].split(first_look._ROOM_LABEL, 1)[1]
    # Each fragment on its own line, each with the owner's label.
    green = next(line for line in message.splitlines() if "the build is green now" in line)
    after = next(line for line in message.splitlines() if "merging it after dinner" in line)
    assert green != after and "Robin" in green and "Robin" in after
    lines = _note(room.calls[-1]).splitlines()
    assert lines[0].startswith("(If I do answer, it's short:")
    assert lines[-1] == "(My first look: let it pass — a status update to Sam, not for me.)"
    assert "This one wants a short answer" not in _note(room.calls[-1])


# ── the look fails: today's defaults, no read ──────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("the call broke"), "not a look at all"])
async def test_a_failed_look_gives_todays_defaults_and_no_read(monkeypatch, tmp_path, failure, caplog):
    caplog.set_level(logging.INFO)
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(failure)
    dm.model_answers("hi")
    await dm.says("hi palli")
    await _flush()

    assert dm.sent == ["hi"]
    assert dm.cadence_prompts == [] and mind.reads == 0
    tail, note = _tail(dm.calls[-1]), _note(dm.calls[-1])
    assert READ_HEADING not in tail
    assert note == reply_governor.render_shape_note("medium", tools=True)   # the default shape
    assert mind.reflected[-1].turn_read is None
    assert mind.reflected[-1].emotional_arc_text == "ARC-WORDS a calm week"   # the arc stands
    assert "first look: no look (lane first_look)" in caplog.text
    assert "runner: no first look on a human turn (origin=user)" in caplog.text


# ── she_decides on, the look off: today's calls and prompts ────────────────

@pytest.mark.asyncio
async def test_with_the_look_off_the_vote_and_the_read_run_as_today(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path, look_on=False)
    dm.model_answers("ok")
    await dm.says("how's it going?")
    await _flush()

    assert mind.look_prompts == [] and mind.slices == []
    assert len(dm.cadence_prompts) == 1 and mind.reads == 1
    assert mind.in_charge == [False]
    tail, note = _tail(dm.calls[-1]), _note(dm.calls[-1])
    assert f"{READ_HEADING}\n{QUIET_FRAMING}\nREAD-WORDS recall's own read" in tail
    assert first_look.FOR_ME_LABEL not in tail
    assert f"## This turn\n{VOTE_OBSERVATION}" in tail
    assert note == reply_governor.render_shape_note("short", tools=True)
    assert mind.reflected[-1].turn_read.read == "READ-WORDS recall's own read"
    assert mind.reflected[-1].emotional_arc_text == "RECALL-ARC"


# ── a friend's turn: the room pass's carry, no second look ─────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_a_friends_reply_reads_the_carry_as_its_read_section(monkeypatch, tmp_path, door):
    room, mind = _room(monkeypatch, tmp_path, door)
    room.look_plan.append(room_look("answer", "Sam asks me", how_they_seem="HOW-WORDS curious",
                                    for_me="FOR-WORDS me, by name", my_own="MINE-WORDS a thought"))
    room.model_answers("sure")
    await room.arrive("sam", "polly, quick one?")
    await room.settle()

    assert len(mind.look_prompts) == 1                  # the room pass's look only: no second look
    assert mind.slices == []                            # (the room pass gathers no slice for a friend)
    tail = _tail(room.calls[-1])
    assert (f"{READ_HEADING}\n{QUIET_FRAMING}\nHOW-WORDS curious\n"
            f"{first_look.FOR_ME_LABEL} FOR-WORDS me, by name\n"
            f"{first_look.MY_OWN_LABEL} MINE-WORDS a thought") in tail
    # A friend's look never holds back in the reply (its quiet is final), and
    # the length note is today's no-tools form.
    assert _note(room.calls[-1]).splitlines() == [
        reply_governor.render_shape_note("short", tools=False), 'Names in this message: "polly" (me)']
    assert room.sent == ["sure"]


@pytest.mark.asyncio
async def test_a_friends_read_section_is_blank_while_a_note_stands(monkeypatch, tmp_path):
    from core import room_notes

    room, mind = _room(monkeypatch, tmp_path, "discord")
    room_notes.set_note(room.room_key(), "NOTE-WORDS keep it short in here",
                        datetime.now() + timedelta(hours=20))
    room.look_plan.append(room_look("answer", "Sam asks me", how_they_seem="HOW-WORDS curious"))
    room.model_answers("sure")
    await room.arrive("sam", "polly, quick one?")
    await room.settle()

    everything = json.dumps({k: str(v) for k, v in room.calls[-1].items()})
    assert READ_HEADING not in everything and "HOW-WORDS" not in everything
    assert "NOTE-WORDS" not in everything


@pytest.mark.asyncio
async def test_with_the_look_off_a_friends_reply_has_no_read_section(monkeypatch, tmp_path):
    room, mind = _room(monkeypatch, tmp_path, "discord", look_on=False)
    await room.say("sam", "polly, quick one?", first_pass="answer", model_chooses="answer")
    assert READ_HEADING not in _tail(room.calls[-1])


# ── Draft 3's texts, one constant each ─────────────────────────────────────

def test_draft_three_lives_in_single_constants():
    assert reply_governor.IF_I_ANSWER_SHORT.startswith("(If I do answer, it's short:")
    assert reply_governor.IF_I_ANSWER_MEDIUM.startswith("(If I do answer, a few sentences fit:")
    assert reply_governor.LOOK_LINES == {
        "stay_quiet": "(My first look: let it pass — {reason}.)",
        "wait": "(My first look: this looks unfinished, more may be coming — {reason}.)",
    }
    assert reply_governor.render_shape_note("short", tools=False, look="wait") == (
        "(If I do answer, it's short: a line or two, under 50 words. The limit is on my words.)")
    assert reply_governor.render_shape_note("short", look="answer") == reply_governor.render_shape_note("short")
    assert reply_governor.render_look_line("answer", "x") == ""
    assert reply_governor.render_look_line("stay_quiet", "") == "(My first look: let it pass.)"
    assert reply_governor.render_look_line("stay_quiet", "it's theirs.") == (
        "(My first look: let it pass — it's theirs.)")


# ── a friend's turn the runner reads itself (no carry: a DM) ───────────────

@pytest.mark.asyncio
async def test_a_friends_dm_takes_the_look_with_no_slice(monkeypatch, tmp_path):
    """Q7: the look runs wherever "she decides" applies. A friend's DM has no
    room pass, so the runner takes it: no slice of her mind (Q2)."""
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("answer", how="HOW-WORDS idle chatter"))
    dm.model_answers("hey Sam")
    await dm.says("hey", who="sam")

    assert len(mind.look_prompts) == 1 and dm.cadence_prompts == []
    assert mind.slices == [False] and "POSTURE-WORDS" not in mind.look_prompts[0]
    assert f"{READ_HEADING}\n{QUIET_FRAMING}\nHOW-WORDS idle chatter" in _tail(dm.calls[-1])
    # On answer a friend's length note is today's, byte for byte.
    assert _note(dm.calls[-1]) == reply_governor.render_shape_note("short", tools=False)


# The coordinator's ruling (R14, after T5): the trailing-note rule holds on
# EVERY human turn whose look the runner took, a friend's DM too — the
# owner's Q7 put the look in DMs, and a friend's DM has no room pass, so the
# runner's look is the only one. This moves T5's pin (which kept a friend's
# DM on today's note) deliberately. A friend's room turn is unchanged: its
# quiet was final in the room pass, and an "answer" carry never holds back.
@pytest.mark.asyncio
@pytest.mark.parametrize("decision,line", [
    ("stay_quiet", "(My first look: let it pass — it's for Sam.)"),
    ("wait", "(My first look: this looks unfinished, more may be coming — they trailed off.)"),
])
async def test_when_a_friends_dm_look_holds_back_the_last_line_is_hers(monkeypatch, tmp_path,
                                                                       decision, line):
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look(decision, length="short"))
    dm.model_answers("hey Sam")
    result = await dm.says("hey", who="sam")

    note = _note(dm.calls[-1]).splitlines()
    assert note[-1] == line
    # Draft 3's no-tools form: a friend's turn with no registry is never
    # told tools are allowed.
    assert note[0] == reply_governor.render_shape_note("short", tools=False, look=decision)
    assert note[0] == ("(If I do answer, it's short: a line or two, under 50 words. "
                       "The limit is on my words.)")
    # T2a-2 stands on a friend's DM too: the look never stops the reply.
    assert result.responded and dm.sent == ["hey Sam"]


@pytest.mark.asyncio
@pytest.mark.parametrize("guest_search", [False, True])
async def test_a_friends_dm_tools_clause_follows_the_registry(monkeypatch, tmp_path, guest_search):
    """With features.guest_web_search on (T7) a friend's turn offers
    web_search, so Draft 3's note says tools are allowed, as the length note
    already does; off, the no-tools form."""
    dm, mind = _dm(monkeypatch, tmp_path, guest_search=guest_search)
    mind.looks.append(look("stay_quiet", length="medium"))
    dm.model_answers("hey Sam")
    await dm.says("hey", who="sam")

    tools = sorted(str(t.get("name")) for t in (dm.calls[-1].get("tools") or []))
    assert tools == (["stay_quiet", "web_search"] if guest_search else ["stay_quiet"])
    first = _note(dm.calls[-1]).splitlines()[0]
    assert first == reply_governor.render_shape_note("medium", tools=guest_search, look="stay_quiet")
    assert first.startswith("(If I do answer, a few sentences fit:")
    assert ("tools are always allowed" in first) is guest_search


@pytest.mark.asyncio
async def test_a_friends_own_look_never_reaches_the_reply_while_a_note_stands(monkeypatch, tmp_path):
    from core import room_notes
    from core.agent.runner import _derive_session_key

    dm, mind = _dm(monkeypatch, tmp_path)
    turn = dm.turn("hey", who="sam")
    room_notes.set_note(_derive_session_key(turn), "NOTE-WORDS keep it short",
                        datetime.now() + timedelta(hours=20))
    mind.looks.append(look("answer", how="HOW-WORDS curious about NOTE-WORDS"))
    dm.model_answers("hey Sam")
    await dm.deliver(await dm.dispatch(turn))

    assert len(mind.look_prompts) == 1
    everything = json.dumps({k: str(v) for k, v in dm.calls[-1].items()})
    assert READ_HEADING not in everything and "HOW-WORDS" not in everything


# ── a DM's look reads the early history (fix 6's fetch) ────────────────────

@pytest.mark.asyncio
async def test_a_dms_look_reads_the_recent_conversation(monkeypatch, tmp_path):
    from core.turn.types import HistoryEntry

    dm, mind = _dm(monkeypatch, tmp_path)
    when = datetime(2026, 10, 1, 8, 0, 0)
    past = [HistoryEntry(role="user" if i % 2 == 0 else "assistant", content=f"line {i}", at=when)
            for i in range(12)]

    async def fetch(turn: Any) -> list[Any]:
        return past

    monkeypatch.setattr("core.turn.recall.fetch_recent_history", fetch)
    await dm.says("and then?")
    conversation = mind.look_prompts[0].split(first_look.DM_CONVERSATION_LABEL, 1)[1]
    conversation = conversation.split(first_look._DM_LABEL, 1)[0]
    # The last eight, as her first read read them.
    assert "user: line 4\nassistant: line 5" in conversation and "line 3" not in conversation
    assert "assistant: line 11" in conversation


@pytest.mark.asyncio
async def test_a_dms_look_goes_on_when_the_history_fetch_breaks(monkeypatch, tmp_path):
    dm, mind = _dm(monkeypatch, tmp_path)

    async def fetch(turn: Any) -> list[Any]:
        raise RuntimeError("the database is down")

    monkeypatch.setattr("core.turn.recall.fetch_recent_history", fetch)
    mind.looks.append(look("answer", my_own="MINE-WORDS the boat"))
    dm.model_answers("ok")
    await dm.says("and then?")
    assert len(mind.look_prompts) == 1 and dm.sent == ["ok"]
    assert f"{first_look.MY_OWN_LABEL} MINE-WORDS the boat" in _tail(dm.calls[-1])


# ── Fable's review (10-13): her line keeps the reason's own end mark ───────

def test_her_line_keeps_the_reasons_own_end_mark():
    """A reason that ends in its own mark ("?", "!", "…") keeps it; the line
    adds a full stop only to a reason that has none — never "?." or "!."."""
    assert reply_governor.render_look_line("stay_quiet", "why would I?") == (
        "(My first look: let it pass — why would I?)")
    assert reply_governor.render_look_line("wait", "hang on!") == (
        "(My first look: this looks unfinished, more may be coming — hang on!)")
    assert reply_governor.render_look_line("stay_quiet", "it's theirs…") == (
        "(My first look: let it pass — it's theirs…)")
    assert reply_governor.render_look_line("stay_quiet", "it's theirs...") == (
        "(My first look: let it pass — it's theirs.)")
    assert reply_governor.render_look_line("stay_quiet", "it's theirs.") == (
        "(My first look: let it pass — it's theirs.)")


# ── Fable's review (10-13): caring_memory gates the read the same way twice ─

def _caring_off(monkeypatch) -> None:
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: SimpleNamespace(caring_memory=False, she_decides=True, first_look=True))


@pytest.mark.asyncio
async def test_with_caring_memory_off_neither_the_reply_nor_the_reflection_gets_the_read(monkeypatch, tmp_path):
    """companion.caring_memory off: recall gives no read today, so the look's
    body reaches neither the reflection nor the reply's read section (one
    gate for both). The decision and her trailing line are the look's still."""
    dm, mind = _dm(monkeypatch, tmp_path)
    _caring_off(monkeypatch)
    mind.looks.append(look("stay_quiet", my_own="MINE-WORDS the boat"))
    dm.model_answers("ok")
    await dm.says("so anyway")
    await _flush()

    tail = _tail(dm.calls[-1])
    assert READ_HEADING not in tail and "HOW-WORDS" not in tail and "MINE-WORDS" not in tail
    assert mind.reflected[-1].turn_read is None
    assert _note(dm.calls[-1]).splitlines()[-1] == "(My first look: let it pass — it's for Sam.)"
    assert dm.sent == ["ok"]


@pytest.mark.asyncio
async def test_with_caring_memory_off_a_friends_carry_gives_no_read_section(monkeypatch, tmp_path):
    room, mind = _room(monkeypatch, tmp_path, "discord")
    _caring_off(monkeypatch)
    room.look_plan.append(room_look("answer", "Sam asks me", how_they_seem="HOW-WORDS curious",
                                    for_me="FOR-WORDS me, by name"))
    room.model_answers("sure")
    await room.arrive("sam", "polly, quick one?")
    await room.settle()

    tail = _tail(room.calls[-1])
    assert READ_HEADING not in tail and "HOW-WORDS" not in tail and "FOR-WORDS" not in tail
    assert room.sent == ["sure"]


# ── Fable's review (10-13): one log line per look the runner took ──────────

@pytest.mark.asyncio
async def test_the_runner_says_what_the_look_said_on_answer_too(monkeypatch, tmp_path, caplog):
    """Draft 6's line on every look the runner took, not only one that held
    back, so a week's `first look` lines count them all; the reason rides
    on it, as on the room pass's line."""
    caplog.set_level(logging.INFO, logger="core.agent.runner")
    dm, mind = _dm(monkeypatch, tmp_path)
    mind.looks.append(look("answer", "they asked me"))
    dm.model_answers("ok")
    await dm.says("hi palli")

    assert ("runner: first look said answer on a human turn (origin=user); "
            "her reply decides") in caplog.text
    assert "— they asked me" in caplog.text
    assert caplog.text.count("runner: first look said") == 1


# ── the look read the owner's note and said answer (the owner, 10-13) ──────
# A friend's reply call, while the owner's room note stands and her first
# look said answer, ends with one line saying so — the look read the note
# with its words; her reply sees only the neutral line. Never after a look
# that failed or timed out (the room pass then answers by default), never
# with no note, never on the owner's turn, never after stay_quiet or wait.

def _set_note(key: str, words: str = "NOTE-WORDS keep it short in here") -> None:
    from core import room_notes

    room_notes.set_note(key, words, datetime.now() + timedelta(hours=20))


def _everything(call: dict[str, Any]) -> str:
    return json.dumps({k: str(v) for k, v in call.items()})


def test_the_note_read_line_lives_in_one_constant():
    assert reply_governor.LOOK_READ_THE_NOTE == "(My first look read the owner's note and said answer.)"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["discord", "telegram"])
async def test_a_friends_room_turn_ends_with_the_note_read_line(monkeypatch, tmp_path, door):
    room, mind = _room(monkeypatch, tmp_path, door)
    _set_note(room.room_key())
    room.look_plan.append(room_look("answer", "NOTE-WORDS say Sam may ask"))
    room.model_answers("sure")
    await room.arrive("sam", "polly, quick one?")
    await room.settle()

    note = _note(room.calls[-1]).splitlines()
    assert note[-1] == reply_governor.LOOK_READ_THE_NOTE
    assert note.count(reply_governor.LOOK_READ_THE_NOTE) == 1
    assert any(line.startswith("The owner has a standing note for this room until") for line in note)
    assert "NOTE-WORDS" not in _everything(room.calls[-1])
    assert room.sent == ["sure"]


@pytest.mark.asyncio
async def test_a_friends_dm_ends_with_the_note_read_line(monkeypatch, tmp_path):
    from core.agent.runner import _derive_session_key

    dm, mind = _dm(monkeypatch, tmp_path)
    turn = dm.turn("hey", who="sam")
    _set_note(_derive_session_key(turn))
    mind.looks.append(look("answer", "NOTE-WORDS let Sam in"))
    dm.model_answers("hey Sam")
    await dm.deliver(await dm.dispatch(turn))

    assert _note(dm.calls[-1]).splitlines()[-1] == reply_governor.LOOK_READ_THE_NOTE
    assert "NOTE-WORDS" not in _everything(dm.calls[-1])


@pytest.mark.asyncio
async def test_no_note_read_line_without_a_note(monkeypatch, tmp_path):
    room, mind = _room(monkeypatch, tmp_path, "discord")
    room.look_plan.append(room_look("answer"))
    room.model_answers("sure")
    await room.arrive("sam", "polly, quick one?")
    await room.settle()
    assert reply_governor.LOOK_READ_THE_NOTE not in _note(room.calls[-1])


@pytest.mark.asyncio
async def test_no_note_read_line_on_the_owners_turn(monkeypatch, tmp_path):
    room, mind = _room(monkeypatch, tmp_path, "discord")
    _set_note(room.room_key())
    room.look_plan.append(room_look("answer"))
    room.model_answers("sure")
    await room.arrive("owner", "polly, quick one?")
    await room.settle()
    assert room.calls and reply_governor.LOOK_READ_THE_NOTE not in _note(room.calls[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.TimeoutError(), RuntimeError("the call broke")])
async def test_no_note_read_line_after_a_room_look_that_failed(monkeypatch, tmp_path, failure):
    """The room pass answers by default when its look times out or fails: that
    "answer" is code's, not her look's, so the line never shows."""
    room, mind = _room(monkeypatch, tmp_path, "discord")
    _set_note(room.room_key())
    room.look_plan.append(failure)
    room.model_answers("sure")
    await room.arrive("sam", "polly, quick one?")
    await room.settle()
    assert room.calls and reply_governor.LOOK_READ_THE_NOTE not in _note(room.calls[-1])


@pytest.mark.asyncio
async def test_no_note_read_line_after_a_dm_look_that_failed(monkeypatch, tmp_path):
    from core.agent.runner import _derive_session_key

    dm, mind = _dm(monkeypatch, tmp_path)
    turn = dm.turn("hey", who="sam")
    _set_note(_derive_session_key(turn))
    mind.looks.append(asyncio.TimeoutError())
    dm.model_answers("hey Sam")
    await dm.deliver(await dm.dispatch(turn))
    assert reply_governor.LOOK_READ_THE_NOTE not in _note(dm.calls[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["stay_quiet", "wait"])
async def test_no_note_read_line_after_a_look_that_held_back(monkeypatch, tmp_path, decision):
    """Its look line is the last line then (the R14 rule): the two never meet."""
    from core.agent.runner import _derive_session_key

    dm, mind = _dm(monkeypatch, tmp_path)
    turn = dm.turn("hey", who="sam")
    _set_note(_derive_session_key(turn))
    mind.looks.append(look(decision))
    dm.model_answers("hey Sam")
    await dm.deliver(await dm.dispatch(turn))
    note = _note(dm.calls[-1]).splitlines()
    assert reply_governor.LOOK_READ_THE_NOTE not in note
    assert note[-1].startswith("(My first look: ")


# (c) While a note stands, a friend's DM look that held back shows its line
# without its reason: the reason could say the note's words back, as the read
# body could (the carry rule).
@pytest.mark.asyncio
@pytest.mark.parametrize("decision,line", [
    ("stay_quiet", "(My first look: let it pass.)"),
    ("wait", "(My first look: this looks unfinished, more may be coming.)"),
])
async def test_a_friends_dm_look_line_has_no_reason_while_a_note_stands(monkeypatch, tmp_path,
                                                                        decision, line):
    from core.agent.runner import _derive_session_key

    dm, mind = _dm(monkeypatch, tmp_path)
    turn = dm.turn("hey", who="sam")
    _set_note(_derive_session_key(turn))
    mind.looks.append(look(decision, "the NOTE-WORDS say keep it short"))
    dm.model_answers("hey Sam")
    await dm.deliver(await dm.dispatch(turn))

    note = _note(dm.calls[-1]).splitlines()
    assert note[-1] == line
    assert reply_governor.LOOK_READ_THE_NOTE not in note
    assert "NOTE-WORDS" not in _everything(dm.calls[-1])


@pytest.mark.asyncio
async def test_the_owners_look_line_keeps_its_reason_while_a_note_stands(monkeypatch, tmp_path):
    """The note's words are the owner's own: his reply call has them anyway."""
    from core.agent.runner import _derive_session_key

    dm, mind = _dm(monkeypatch, tmp_path)
    turn = dm.turn("hey")
    _set_note(_derive_session_key(turn))
    mind.looks.append(look("stay_quiet", "it's for Sam"))
    dm.model_answers("ok")
    await dm.deliver(await dm.dispatch(turn))
    assert _note(dm.calls[-1]).splitlines()[-1] == "(My first look: let it pass — it's for Sam.)"
