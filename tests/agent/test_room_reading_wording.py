"""The room-reading fix's words, exactly as the owner approved them on
2026-10-12 (Fable's drafts, revised after Fable's read): A and B in her own
voice, C for the quick vote, D for her first read, and E, the names line.
All of it in group chats only: a DM's prompts are today's, word for word.
"""
from __future__ import annotations

import pytest

A = ("I read whether a message is for me the way anyone there would: the name it "
     "calls, who it answers, where the talk was already going. When I can't tell, I "
     "stay out, since whoever means me will say my name or answer me; a name I "
     "haven't learned belongs to someone else.")
B = ("When people are talking to each other and I have something real to add, I "
     "join in the way a friend in the room would, as often as that's true, and not "
     "just to be in it.")
C = ('Who a message is for: say what the message does ("calls someone named Casey"), '
     'never what it might mean ("a nickname or a slip"). A name that isn\'t hers and '
     "isn't a known alias of hers belongs to someone else. When it's unclear whether "
     'she\'s meant, the decision is "stay_quiet".')
D_OPENING = "Before I decide anything, I take a quiet read of them."
D_LABEL = "WHAT WAS JUST SAID (in a group, it may be meant for someone else):"


def _flat(text: str) -> str:
    return " ".join(str(text).split())


# ── A and B ──────────────────────────────────────────────────────────────────

def test_a_and_b_are_the_approved_words():
    from core.agent.system_prompt import ROOM_READING_INSTRUCTION

    assert ROOM_READING_INSTRUCTION == A + " " + B


def test_a_and_b_follow_piece_1_in_a_group_only():
    from core.agent.system_prompt import (
        QUIET_CHOICE_INSTRUCTION, ROOM_READING_INSTRUCTION, WHO_IS_TALKING_INSTRUCTION,
        _texting_voice_block)

    group = _texting_voice_block(True, group_chat=True)
    assert group.endswith(QUIET_CHOICE_INSTRUCTION + " " + WHO_IS_TALKING_INSTRUCTION
                          + " " + ROOM_READING_INSTRUCTION)
    assert ROOM_READING_INSTRUCTION not in _texting_voice_block(True)          # a DM
    assert ROOM_READING_INSTRUCTION not in _texting_voice_block(False, group_chat=True)


def test_a_and_b_add_no_prohibition_and_no_second_person():
    import re

    from scripts.prompt_size_report import PROHIBITION_RE

    assert PROHIBITION_RE.findall(A + " " + B) == []
    assert not re.search(r"\byou\b|\byour\b", A + " " + B, re.IGNORECASE)
    assert not re.search(r"\ba person\b|\bhuman\b", A + " " + B, re.IGNORECASE)


# ── C ────────────────────────────────────────────────────────────────────────

def test_c_is_the_approved_words_in_the_room_vote_only():
    from core.agent.cadence import PARTICIPANT_ROOM_PROMPT, PARTICIPANT_SHAPE_PROMPT

    assert C in _flat(PARTICIPANT_ROOM_PROMPT)
    assert "Who a message is for" not in PARTICIPANT_SHAPE_PROMPT
    room = _flat(PARTICIPANT_ROOM_PROMPT)
    assert room.index('Whose "don\'t reply" counts') < room.index(C) < room.index("reason: one short line")
    assert PARTICIPANT_ROOM_PROMPT.replace(
        PARTICIPANT_ROOM_PROMPT[PARTICIPANT_ROOM_PROMPT.index("   Who a message is for"):
                                PARTICIPANT_ROOM_PROMPT.index("   reason: one short line")],
        "") == PARTICIPANT_SHAPE_PROMPT


async def _vote_prompt(**kw) -> str:
    from types import SimpleNamespace

    from core.agent import cadence

    seen: list[str] = []

    async def llm(prompt: str) -> str:
        seen.append(prompt)
        return "{}"

    await cadence.shape_response(SimpleNamespace(user_text="hey Alex", recent_history_text=""),
                                 llm=llm, participant=True, **kw)
    return seen[-1]


@pytest.mark.asyncio
async def test_the_vote_without_room_is_todays(monkeypatch):
    plain = await _vote_prompt(signals="SIGNALS")
    assert plain == await _vote_prompt(signals="SIGNALS", room=False, names="")
    assert "Who a message is for" not in plain


@pytest.mark.asyncio
async def test_the_room_vote_reads_the_names_after_the_signals_before_the_note():
    prompt = await _vote_prompt(signals="SIGNALS", note="NOTE", room=True,
                                names='Names in this message: "Alex" (someone in this room, not her)')
    assert C in _flat(prompt)
    tail = prompt[prompt.index("SIGNALS"):]
    assert tail.index("SIGNALS") < tail.index("Names in this message") < tail.index("NOTE")


# ── D ────────────────────────────────────────────────────────────────────────

def test_d_changes_two_lines_in_a_group_and_nothing_in_a_dm():
    from ai.prompt_loader import prompt_loader
    from ai.turn_read import room_template

    template = prompt_loader.load_prompt("turn_read")
    room = room_template(template)
    assert D_OPENING in room and D_LABEL in room
    assert "Before I answer" not in room and "WHAT THEY JUST SAID TO ME" not in room
    assert room.replace(D_OPENING, "Before I answer, I take a quiet read of them.").replace(
        D_LABEL, "WHAT THEY JUST SAID TO ME:") == template


def test_a_drifted_template_is_left_as_it_is():
    from ai.turn_read import room_template

    assert room_template("something else {message}") == "something else {message}"


@pytest.mark.asyncio
@pytest.mark.parametrize("in_room", [True, False])
async def test_her_first_read_is_sent_with_the_group_lines_only_in_a_room(monkeypatch, in_room):
    import ai.llm_client as llm_mod
    from ai import turn_read

    sent: list[str] = []

    async def fake(**kw):
        sent.append(kw["messages"][0]["content"])
        return '{"read": "ok", "weight": 0.1}'

    monkeypatch.setattr(llm_mod.llm, "chat_completion", fake)
    await turn_read.take_turn_read(message="hey Alex", recent_conversation="",
                                   relationship_posture="", emotional_arc="",
                                   in_room=in_room)
    assert (D_LABEL in sent[-1]) is in_room
    assert ("WHAT THEY JUST SAID TO ME:" in sent[-1]) is not in_room


# ── her first read sees the names line in a room (the replay, 10-12) ────────

@pytest.mark.asyncio
@pytest.mark.parametrize("in_room", [True, False])
async def test_her_first_read_sees_the_names_line_only_in_a_room(monkeypatch, in_room):
    import ai.llm_client as llm_mod
    from ai import turn_read
    from core.agent.room_tail import current_room_names

    sent: list[str] = []

    async def fake(**kw):
        sent.append(kw["messages"][0]["content"])
        return '{"read": "ok", "weight": 0.1}'

    monkeypatch.setattr(llm_mod.llm, "chat_completion", fake)
    line = 'Names in this message: "Alex" (someone in this room, not me)'
    token = current_room_names.set(line)
    try:
        await turn_read.take_turn_read(message="hey Alex, you around?", recent_conversation="",
                                       relationship_posture="", emotional_arc="",
                                       in_room=in_room)
    finally:
        current_room_names.reset(token)
    assert (("hey Alex, you around?\n" + line) in sent[-1]) is in_room
    assert (line in sent[-1]) is in_room
