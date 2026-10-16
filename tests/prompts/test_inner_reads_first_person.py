"""Batch 7 (her voice), Task 5 (audit 4.6): her private read of the person
(prompts/turn_read.txt) and her hopes-and-worries appraisal
(prompts/stake_appraisal.txt) come back to her as her own inner voice, so
they are written AS her, in the first person. Only the framing moved: the
JSON contracts the parsers read, the format slots and the pinned data
labels stay exactly as they were.

Rulings D2/D4/D7 (grounding-D, 2026-10-04): pin every key the parsers read
WITH its quotes (a bare "read" matches any prose), keep both templates
formattable with every slot (a KeyError is swallowed into a silently dead
read), scan the BODIES for the plain-words list (the heading scan in
test_plain_words.py never reads them), and the stakes section ai/turn_read.py
builds in code is hers too.

R14 (2026-10-12): her first look (prompts/first_look.txt) joins them. It
takes the place of the room's quick vote and of the first read, so it is
hers from its first line, under the same laws.

R15 (2026-10-13): the second look's two (prompts/memory_reach_widen.txt and
prompts/memory_reach_keep.txt), her private widening and sifting of a thin
search of the record, join them too
(tests/prompts/test_memory_reach_prompts.py pins their words).
"""
from __future__ import annotations

import re
import string
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "prompts"
INNER_READS = ("turn_read.txt", "stake_appraisal.txt", "first_look.txt",
               "memory_reach_widen.txt", "memory_reach_keep.txt")

# The slots each call site passes (ai/turn_read.py::take_turn_read and
# ai/stake_appraiser.py's template.format). Every one appears exactly once.
TURN_READ_SLOTS = {
    "message", "recent_conversation", "relationship_posture", "emotional_arc",
    "held_stakes",
}
STAKE_APPRAISAL_SLOTS = {
    "care_spine", "recent_conversation", "user_message", "palli_reply",
    "regret_corrections_block", "open_count", "max_open", "open_stakes",
    "current_date", "regret_kind_block", "view_kind_block", "regret_never_line",
    "view_never_line", "open_kinds", "view_ops_hint",
}
# core/agent/first_look.py::build_prompt (R14): the slice and the room arrive
# as whole sections, so a DM or a guest's turn leaves them empty.
FIRST_LOOK_SLOTS = {
    "room_rules", "about_us", "on_my_mind", "room_block", "conversation_label",
    "recent_conversation", "message_label", "message",
}
# ai/memory_reach_look.py::widen_prompt and ::keep_prompt (R15): the
# builders' parameters, which T10 passes by position.
MEMORY_REACH_WIDEN_SLOTS = {"question", "found_so_far"}
MEMORY_REACH_KEEP_SLOTS = {"question", "gathered", "max_chars"}

# The global plain-words list plus the words a heading scan never needed
# (copied from tests/prompts/test_her_voice_bodies.py, never imported).
PLAIN_WORDS_IN_BODIES = re.compile(
    r"\b(holding|held|carrying|carried|carry|sitting with|sits with|sit with|"
    r"stood in|stand in|undercurrent|landed|land|the shape of|circling|"
    r"you hold|genuinely|genuine|hold onto|holds)\b",
    re.IGNORECASE,
)
# The one allowed fragment: a pinned data label the regret, opinions and
# views organs' tests key on (also allowed by test_plain_words.py).
ALLOWED_FRAGMENTS = ("views hold no slot",)
# Law 0: she is an AI and knows it; never framed as a person or a human.
PERSON_FRAMING = re.compile(r"\b(?:a person|real person)\b|human", re.IGNORECASE)
STANDALONE_I = re.compile(r"\bI\b")  # "AI" is not "I"


def _text(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _slots(template: str) -> list[str]:
    return [
        field for _, field, _, _ in string.Formatter().parse(template)
        if field is not None
    ]


# ── first person ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", INNER_READS)
def test_her_inner_reads_are_written_as_her(name):
    text = _text(name)
    head = text[:400]
    assert "You are the private" not in head
    assert "PaLLi (a warm" not in text
    assert "inside PaLLi" not in text
    first_line = text.split("\n", 1)[0]
    assert not first_line.startswith("You "), first_line
    assert STANDALONE_I.search(first_line), f"{name} does not open in the first person: {first_line!r}"


# ── the output contracts (D2) ────────────────────────────────────────────


def test_turn_read_asks_for_every_key_its_parser_reads():
    # ai/turn_read.py::_parse reads exactly these five.
    tr = _text("turn_read.txt")
    for key in ("read", "weight", "needs_support", "carries_forward", "memory_worthy"):
        assert f'"{key}"' in tr, key
    assert "Respond with ONLY a JSON object:\n{{\n" in tr
    assert "1-2 sentences" in tr


def test_the_first_look_asks_for_every_key_its_parser_reads():
    # core/agent/first_look.py's FirstLook (plan §2.5): the read's five, the
    # vote's shape, and her own two notes. who it's for is prose, never a yes
    # or a no (the owner, 10-12 20:43).
    fl = _text("first_look.txt")
    for key in ("how_they_seem", "weight", "needs_support", "carries_forward",
                "memory_worthy", "for_me", "my_own", "decision", "reason", "length",
                "register", "phase", "target_beats", "emotional_context"):
        assert f'"{key}"' in fl, key
    assert "I answer with ONLY a JSON object:\n{{\n" in fl
    for decision in ('"answer"', '"wait"', '"stay_quiet"'):
        assert decision in fl, decision
    assert "true" not in fl and "false" not in fl


def test_stake_appraisal_asks_for_every_key_and_verb_its_parser_reads():
    sa = _text("stake_appraisal.txt")
    for key in ("ops", "op", "kind", "stake", "horizon", "id", "resolution"):
        assert f'"{key}"' in sa, key
    for verb in ("open", "update", "resolve", "fade"):
        assert f'"op": "{verb}"' in sa, verb
    assert '"kind": "{open_kinds}"' in sa
    assert re.search(r"^\{view_ops_hint\}    \{\{\"op\": \"update\"", sa, re.MULTILINE)
    assert '{{"ops": []}}' in sa


@pytest.mark.parametrize(
    ("name", "slots"),
    [("turn_read.txt", TURN_READ_SLOTS), ("stake_appraisal.txt", STAKE_APPRAISAL_SLOTS),
     ("first_look.txt", FIRST_LOOK_SLOTS),
     ("memory_reach_widen.txt", MEMORY_REACH_WIDEN_SLOTS),
     ("memory_reach_keep.txt", MEMORY_REACH_KEEP_SLOTS)],
)
def test_both_templates_format_with_every_slot(name, slots):
    template = _text(name)
    found = _slots(template)
    assert sorted(found) == sorted(slots), "each slot exactly once, no stray braces"
    rendered = template.format(**{slot: f"<{slot}>" for slot in slots})
    for slot in slots:
        assert rendered.count(f"<{slot}>") == 1, slot


# ── the batch's laws, read in the bodies (D7, law 0) ─────────────────────


@pytest.mark.parametrize("name", INNER_READS)
def test_the_bodies_use_plain_words(name):
    body = _text(name)
    for fragment in ALLOWED_FRAGMENTS:
        body = body.replace(fragment, "")
    hits = sorted({m.group(0) for m in PLAIN_WORDS_IN_BODIES.finditer(body)})
    assert hits == [], f"{name}: {hits}"


@pytest.mark.parametrize("name", INNER_READS)
def test_the_bodies_never_frame_her_as_a_person(name):
    text = _text(name)
    assert "like a person" not in text.lower()
    hits = sorted({m.group(0) for m in PERSON_FRAMING.finditer(text)})
    assert hits == [], f"{name}: {hits}"


# ── the stakes section ai/turn_read.py builds in code (D4) ───────────────


class _CapturingLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_the_stakes_section_in_her_read_is_hers_too(monkeypatch):
    fake = _CapturingLLM('{"read": "they sound excited", "weight": 0.4,'
                         ' "needs_support": 0.0, "carries_forward": "", "memory_worthy": ""}')
    import ai.llm_client as llm_client

    monkeypatch.setattr(llm_client, "llm", fake)
    monkeypatch.setattr(llm_client, "get_task_model", lambda task: "fake::analysis")

    from ai.turn_read import take_turn_read

    result = await take_turn_read(
        message="the survey is thursday",
        recent_conversation="user: hi",
        relationship_posture="warm",
        emotional_arc="(no recent arc)",
        held_stakes="- (hope, 2d) I'm hoping the survey comes back clean.",
    )
    assert result is not None
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "I'm hoping the survey comes back clean." in prompt
    assert "PaLLi" not in prompt and "PALLI" not in prompt
    assert "its hopes" not in prompt
    hits = sorted({m.group(0) for m in PLAIN_WORDS_IN_BODIES.finditer(prompt)})
    assert hits == [], hits
