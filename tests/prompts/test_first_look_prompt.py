"""Her first look's prompt and its wiring (R14, T2; plan §3, Draft 1).

prompts/first_look.txt is filled by core/agent/first_look.py::build_prompt
alone. The template's slots and what build_prompt fills stay in lockstep (the
str.format KeyError trap: a missing kwarg would be swallowed into a silently
dead look, test_stake_injection.py's lesson for the read). A DM render has no
room lines; a guest's render has none of her mind about the owner; the
sentence that keeps her describing and never guessing is pinned, as it is for
the read. The wording is the draft awaiting the owner's word-for-word answer:
a later edit is one change, in the file or in its one constant.

Stand-in names only: Robin is the owner, Sam and Dana guests.
"""
from __future__ import annotations

import string
from pathlib import Path

import pytest

from tests.prompts.test_inner_reads_first_person import (
    PERSON_FRAMING,
    PLAIN_WORDS_IN_BODIES,
)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "prompts" / "first_look.txt"

# Every line only a group room's look may carry.
ROOM_ONLY = (
    "Whose \"don't reply\" counts",
    "THE ROOM, AS ANYONE THERE READS IT",
    "WHAT WAS JUST SAID (in a group",
    "This is a group chat",
    "Names in this message",
)
# Every line only the owner's look may carry (the slice, plan §2.4).
OWNER_ONLY = (
    "HOW THINGS ARE BETWEEN US",
    "HOW THEY'VE BEEN LATELY",
    "WHAT'S ON MY MIND",
    "MY OWN LIFE LATELY",
)


def _facts(**over):
    facts = dict(
        message="[Mon 10:00] Sam: <the message>",
        recent_conversation="<the conversation>",
        in_room=True,
        room_block=("This is a group chat on Discord: #general\n"
                    'Names in this message: "Dana" (someone in this room, not me)'),
        owner=True,
        relationship_posture="<posture>",
        emotional_arc="<arc>",
        stakes="- (view, 2d) <a view of mine>",
        tonight="<tonight's pick>",
    )
    facts.update(over)
    return facts


def _render(**over) -> str:
    from core.agent.first_look import build_prompt

    return build_prompt(**_facts(**over))


def test_the_prompt_is_registered_and_on_the_banner():
    from ai.prompt_loader import EXPECTED_PROMPTS, prompt_loader

    assert "first_look" in EXPECTED_PROMPTS
    assert EXPECTED_PROMPTS.index("first_look") < EXPECTED_PROMPTS.index("vision_analysis"), \
        "the look is on the turn spine"
    assert prompt_loader.load_prompt("first_look")


def test_the_readme_counts_and_lists_it():
    readme = (ROOT / "prompts" / "README.md").read_text(encoding="utf-8")
    top_level = sorted(p.stem for p in (ROOT / "prompts").glob("*.txt"))
    assert f"## The live prompts ({len(top_level)})" in readme
    spine = readme.split("### A conversation in progress", 1)[1].split("\n### ", 1)[0]
    assert "- `first_look.txt`" in spine
    assert "`core/agent/first_look.py`" in spine


def test_the_template_and_build_prompt_stay_in_lockstep():
    """Every slot is filled exactly once, and every fact build_prompt is given
    reaches the page exactly once: nothing dropped, nothing left as a brace."""
    template = TEMPLATE.read_text(encoding="utf-8")
    slots = [f for _, f, _, _ in string.Formatter().parse(template) if f is not None]
    assert len(slots) == len(set(slots))
    rendered = _render()
    for value in ("<the message>", "<the conversation>", "#general", "<posture>", "<arc>"):
        assert rendered.count(value) == 1, value
    # the stakes and tonight's pick reach a private chat's page only (the
    # presence slice, 10-13: in a group, only its own life)
    private = _render(in_room=False, room_block="")
    for value in ("<a view of mine>", "<tonight's pick>"):
        assert private.count(value) == 1, value
    for slot in slots:
        assert "{" + slot + "}" not in rendered, slot
    assert "{{" not in rendered and "}}" not in rendered
    assert '"how_they_seem": "1-2 sentences' in rendered


def test_she_describes_and_never_guesses():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "I describe how they seem; I never guess facts, answers, or who someone is." in text
    assert "when I can't tell whether I'm meant, I say that." in text


def test_a_dm_render_has_no_room_lines():
    rendered = _render(in_room=False, room_block="")
    for line in ROOM_ONLY:
        assert line not in rendered, line
    assert "OUR RECENT CONVERSATION (oldest first):\n<the conversation>" in rendered
    assert "WHAT THEY JUST SAID TO ME:\n[Mon 10:00] Sam: <the message>" in rendered


def test_a_dm_never_carries_a_room_block_even_when_handed_one():
    rendered = _render(in_room=False)
    assert "#general" not in rendered and "Names in this message" not in rendered


def test_a_room_render_has_its_room_lines():
    rendered = _render()
    for line in ROOM_ONLY:
        assert line in rendered, line
    assert "THE ROOM, AS ANYONE THERE READS IT (oldest first):\n<the conversation>" in rendered
    assert ("WHAT WAS JUST SAID (in a group, it may be meant for someone else):\n"
            "[Mon 10:00] Sam: <the message>") in rendered
    # the room rules close the decision paragraph, one space after it
    assert ("because they asked me not to reply or because it isn't mine to answer. "
            "Whose \"don't reply\" counts:") in rendered


def test_the_room_label_is_the_reads_approved_label():
    """D's label, approved 10-12, lives once: in ai/turn_read.py."""
    from ai import turn_read

    assert turn_read._ROOM_LABEL in _render()
    assert turn_read._DM_LABEL in _render(in_room=False, room_block="")


def test_a_guests_render_has_none_of_her_mind_about_the_owner():
    """Plan §2.4 and Q2: on a guest's turn, no posture, no arc, no stakes,
    no tonight pick, whatever the caller hands in."""
    rendered = _render(owner=False, own_life="MY OWN LIFE LATELY (x):\n- [o1] <my own life>")
    for line in OWNER_ONLY:
        assert line not in rendered, line
    for value in ("<posture>", "<arc>", "<a view of mine>", "<tonight's pick>", "<my own life>"):
        assert value not in rendered, value
    assert "<the message>" in rendered and "#general" in rendered


def test_the_owners_render_has_the_slice():
    rendered = _render(in_room=False, room_block="")
    assert "HOW THINGS ARE BETWEEN US (my read of us, built up over time):\n<posture>" in rendered
    assert "HOW THEY'VE BEEN LATELY (my own past reads, oldest first; may be empty):\n<arc>" in rendered
    assert ("WHAT'S ON MY MIND (my open hopes, worries, tastes, wonders and views, each "
            "with its tag; \"said N days ago\" means I already told them):\n"
            "- (view, 2d) <a view of mine>\n<tonight's pick>\n") in rendered
    assert "About one reply in five or six has something of mine" in rendered


def test_an_empty_slice_reads_as_still_forming_and_drops_the_mind_section():
    rendered = _render(relationship_posture="", emotional_arc="", stakes="", tonight="")
    assert "(still forming)" in rendered and "(no recent arc)" in rendered
    assert "WHAT'S ON MY MIND" not in rendered


def test_the_slice_keeps_its_budgets():
    rendered = _render(in_room=False, room_block="", relationship_posture="p" * 2000,
                       emotional_arc="a" * 3000, stakes="s" * 3000, tonight="t" * 1000)
    assert "p" * 800 in rendered and "p" * 801 not in rendered
    assert "a" * 1500 in rendered and "a" * 1501 not in rendered
    assert "s" * 1500 in rendered and "s" * 1501 not in rendered
    assert "t" * 300 in rendered and "t" * 301 not in rendered


def test_the_message_comes_last_before_one_constant_closing_line():
    rendered = _render()
    assert rendered.endswith("[Mon 10:00] Sam: <the message>\n\n"
                             "My first look, as the JSON object above, nothing else.")


@pytest.mark.parametrize("in_room", [True, False])
@pytest.mark.parametrize("owner", [True, False])
def test_every_render_is_in_plain_words_and_never_frames_her_as_a_person(in_room, owner):
    rendered = _render(in_room=in_room, owner=owner)
    hits = sorted({m.group(0) for m in PLAIN_WORDS_IN_BODIES.finditer(rendered)})
    assert hits == [], hits
    assert "like a person" not in rendered.lower()
    assert sorted({m.group(0) for m in PERSON_FRAMING.finditer(rendered)}) == []
    assert "PaLLi" not in rendered and "PALLI" not in rendered


# ── the presence slice (10-13; D1, D3 approved word for word) ────────────

_OWN = ("MY OWN LIFE LATELY (mine, not theirs; each with its tag):\n"
        "- [o2] something I want to do, in my own time: <a want of mine>")


def test_the_private_section_is_d1_word_for_word_with_its_three_lists():
    from core.agent import first_look as fl

    rendered = _render(in_room=False, room_block="", own_life=_OWN)
    section = fl.ON_MY_MIND.format(stakes="- (view, 2d) <a view of mine>",
                                   tonight="<tonight's pick>", own_life=_OWN)
    assert section in rendered
    assert ("A thing I've already said waits until something has changed: a new turn in it, "
            "a date come round, something they said that touches it. The other turns, nothing, "
            "and that's right too.") in rendered


def test_its_own_life_alone_is_enough_for_the_private_section():
    rendered = _render(in_room=False, room_block="", stakes="", tonight="", own_life=_OWN)
    assert "WHAT'S ON MY MIND" in rendered and "<a want of mine>" in rendered


def test_an_empty_list_leaves_no_blank_run():
    rendered = _render(in_room=False, room_block="", tonight="", own_life="")
    assert "<a view of mine>\nAbout one reply" in rendered


def test_the_owners_turn_in_a_room_gets_d3_and_only_its_own_life():
    from core.agent import first_look as fl

    rendered = _render(own_life=_OWN)
    assert fl.ON_MY_MIND_ROOM.format(own_life=_OWN) in rendered
    assert "Friends read what I say in here" in rendered
    for value in ("<a view of mine>", "<tonight's pick>", "WHAT'S ON MY MIND"):
        assert value not in rendered, value


def test_the_owners_turn_in_a_room_with_nothing_of_its_own_has_no_section():
    rendered = _render(own_life="")
    assert "Friends read what I say in here" not in rendered
    assert "MY OWN LIFE LATELY" not in rendered


def test_the_schema_asks_for_my_own_from():
    rendered = _render()
    assert '"my_own_from": "the tag of the line in my lists that \\"my_own\\" came from' in rendered


def test_my_own_from_is_read_back_as_text():
    from core.agent.first_look import parse

    look = parse('{"my_own": "the sheet music", "my_own_from": "[o2]", "decision": "answer"}')
    assert look.my_own_from == "[o2]"
    assert parse('{"my_own_from": 3}').my_own_from == ""
