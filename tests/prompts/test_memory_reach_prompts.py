"""The second look's two prompts and their wiring (R15, T9).

prompts/memory_reach_widen.txt and prompts/memory_reach_keep.txt are PaLLi's
private looks inside a reply: the search of the record came back thin, so she
widens it, then sifts what it gathered. They are filled by
ai/memory_reach_look.py's ``widen_prompt`` and ``keep_prompt`` alone, and T10
calls those two by position, so each template's slots are the builder's
parameters, in order: the str.format KeyError trap (a missing kwarg is a
dead look) closed at the interface.

The words are Fable's, items 23-29 and 29a, pinned here verbatim as the
owner approved them on 10-13 (Draft 2, Part C of the NAS file
memory-words-for-yes-2026-10-13.md): every line of a render is one of them or
a slot's value, nothing added. Both speak as "I" and never address PaLLi as "you";
tests/prompts/test_inner_reads_first_person.py holds them to the inner reads'
laws too (INNER_READS).

Stand-in names only: Robin is the owner, Dana a guest.
"""
from __future__ import annotations

import inspect
import json
import re
import string
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "prompts"
WIDEN = PROMPTS / "memory_reach_widen.txt"
KEEP = PROMPTS / "memory_reach_keep.txt"

SECOND_PERSON = re.compile(r"\b(you|your|yours|yourself)\b", re.IGNORECASE)

# Items 23-26: the widen look. Item 26's "/" separates its two data labels.
ITEM_23 = ("Someone asked me about something said before, and my first search of our "
           "record didn't settle it. This is my second, harder look: before I answer, I "
           "widen the search rather than guess.")
ITEM_24 = ("I think about where the words would actually be: other ways it might have "
           "been put, a tighter or looser time, and which of the lines found so far are "
           "closest to it, so the messages around them would help.")
ITEM_25 = ('I answer with ONLY a JSON object: {"rephrasings": ["up to two other wordings, '
           'in the words they\'d have used"], "since": "YYYY-MM-DD or empty", "until": '
           '"YYYY-MM-DD or empty", "open_up": ["ids of lines worth reading around, up to '
           'three"], "follow": ["ids of lines whose thread is still on topic, up to two"]}')
QUESTION_LABEL = "THE QUESTION:"
FOUND_LABEL = 'WHAT THE SEARCH FOUND SO FAR (dated lines; "me" is what I said):'
# Items 27-29 and 29a: the keep look. Item 29a is its own two labels (by then
# the search is over, so "so far" would say more was coming); item 29 carries
# the {max_chars} slot.
GATHERED_LABEL = 'EVERYTHING THE SEARCH GATHERED (dated lines; "me" is what I said):'
ITEM_27 = ("Someone asked me about something said before, and these are the lines my "
           "search gathered. I keep what bears on the question, word for word where the "
           "words matter (a number, a line, a name, a date), with when and who said it, "
           "and I drop the rest.")
ITEM_28 = "If nothing here answers it, I say so plainly instead of stretching a near miss."
ITEM_29 = ("What I keep, as plain dated lines under {max_chars} characters, or the one "
           "line: Nothing here answers it.")

# The keys T10's parse_plan reads from the widen look's answer.
PLAN_KEYS = {"rephrasings", "since", "until", "open_up", "follow"}

QUESTION = "what was the name of the bakery Dana mentioned"
FOUND = ("In our private chat:\n#41 [Oct 5 19:02] them: Dana said the bakery on Elm\n"
         "#42 [Oct 5 19:03] me: the one with the blue door?")


def _slots(path: Path) -> list[str]:
    return [f for _, f, _, _ in string.Formatter().parse(path.read_text(encoding="utf-8"))
            if f is not None]


def _widen(**over) -> str:
    from ai.memory_reach_look import widen_prompt

    facts = dict(question=QUESTION, found_so_far=FOUND)
    facts.update(over)
    return widen_prompt(**facts)


def _keep(**over) -> str:
    from ai.memory_reach_look import keep_prompt

    facts = dict(question=QUESTION, gathered=FOUND, max_chars=1200)
    facts.update(over)
    return keep_prompt(**facts)


# ── registered, on the banner, counted ───────────────────────────────────


@pytest.mark.parametrize("name", ["memory_reach_widen", "memory_reach_keep"])
def test_both_are_registered_on_the_turn_spine(name):
    from ai.prompt_loader import EXPECTED_PROMPTS, prompt_loader

    assert name in EXPECTED_PROMPTS
    assert EXPECTED_PROMPTS.index(name) < EXPECTED_PROMPTS.index("vision_analysis"), \
        "the second look runs inside a reply someone is waiting on"
    assert prompt_loader.load_prompt(name)


def test_the_judge_stays_under_measuring():
    from ai.prompt_loader import EXPECTED_PROMPTS

    assert EXPECTED_PROMPTS.index("memory_reach_judge") > EXPECTED_PROMPTS.index("presence_judge")


def test_the_readme_counts_and_lists_both():
    readme = (PROMPTS / "README.md").read_text(encoding="utf-8")
    top_level = sorted(p.stem for p in PROMPTS.glob("*.txt"))
    assert f"## The live prompts ({len(top_level)})" in readme
    spine = readme.split("### A conversation in progress", 1)[1].split("\n### ", 1)[0]
    for name in ("memory_reach_widen.txt", "memory_reach_keep.txt"):
        assert f"- `{name}`" in spine, name
    assert spine.count("`ai/memory_reach_look.py`") == 2


# ── the slots are the builders' parameters (T10's interface) ─────────────


@pytest.mark.parametrize(
    ("path", "builder", "params"),
    [(WIDEN, "widen_prompt", ["question", "found_so_far"]),
     (KEEP, "keep_prompt", ["question", "gathered", "max_chars"])],
)
def test_each_templates_slots_are_its_builders_parameters_in_order(path, builder, params):
    import ai.memory_reach_look as look

    slots = _slots(path)
    assert len(slots) == len(set(slots)), "each slot exactly once"
    assert set(slots) == set(params)
    signature = inspect.signature(getattr(look, builder))
    assert list(signature.parameters) == params, "T10 calls these by position"


def test_every_value_reaches_the_widen_page_once_and_no_brace_is_left():
    rendered = _widen()
    assert rendered.count(QUESTION) == 1 and rendered.count(FOUND) == 1
    assert "{question}" not in rendered and "{found_so_far}" not in rendered
    assert "{{" not in rendered and "}}" not in rendered


def test_every_value_reaches_the_keep_page_once_and_no_brace_is_left():
    rendered = _keep()
    assert rendered.count(QUESTION) == 1 and rendered.count(FOUND) == 1
    assert "under 1200 characters" in rendered
    for slot in ("{question}", "{gathered}", "{max_chars}"):
        assert slot not in rendered, slot
    assert "{{" not in rendered and "}}" not in rendered


def test_a_value_with_braces_is_taken_as_it_is():
    rendered = _widen(question='did Dana say "{x}" or {0}?')
    assert 'did Dana say "{x}" or {0}?' in rendered


@pytest.mark.parametrize("builder", ["widen_prompt", "keep_prompt"])
def test_a_missing_template_raises_so_the_caller_can_fall_back(builder, monkeypatch):
    import ai.memory_reach_look as look
    from ai.prompt_loader import prompt_loader

    monkeypatch.setattr(prompt_loader, "load_prompt", lambda name: None)
    args = (QUESTION, FOUND) if builder == "widen_prompt" else (QUESTION, FOUND, 1200)
    with pytest.raises(RuntimeError):
        getattr(look, builder)(*args)


# ── Fable's words, verbatim, and nothing else ────────────────────────────


def _lines(rendered: str, *values: str) -> list[str]:
    for value in values:
        rendered = rendered.replace(value, "")
    return [line for line in rendered.splitlines() if line.strip()]


def test_the_widen_page_is_items_23_to_26_and_the_two_values():
    rendered = _widen()
    assert _lines(rendered, QUESTION, FOUND) == [
        ITEM_23, ITEM_24, ITEM_25, QUESTION_LABEL, FOUND_LABEL]
    assert f"{QUESTION_LABEL}\n{QUESTION}\n" in rendered
    assert rendered.endswith(f"{FOUND_LABEL}\n{FOUND}"), "the lines come last"


def test_the_keep_page_is_items_27_to_29_and_29a_and_the_values():
    rendered = _keep()
    assert _lines(rendered, QUESTION, FOUND) == [
        ITEM_27, ITEM_28, QUESTION_LABEL, GATHERED_LABEL, ITEM_29.format(max_chars=1200)]
    assert f"{QUESTION_LABEL}\n{QUESTION}\n" in rendered
    assert f"{GATHERED_LABEL}\n{FOUND}\n" in rendered
    assert FOUND_LABEL not in rendered, "item 26's labels stay in the widen look"
    assert rendered.endswith(ITEM_29.format(max_chars=1200)), "one closing line"


def test_the_widen_answer_is_the_json_object_t10_parses():
    rendered = _widen()
    shape = json.loads(rendered.split("I answer with ONLY a JSON object: ", 1)[1].split("\n", 1)[0])
    assert set(shape) == PLAN_KEYS
    assert isinstance(shape["rephrasings"], list) and isinstance(shape["open_up"], list)
    assert isinstance(shape["follow"], list)


# ── her private looks: "I", never "you" ──────────────────────────────────


@pytest.mark.parametrize("path", [WIDEN, KEEP])
def test_neither_look_addresses_her_as_you(path):
    text = path.read_text(encoding="utf-8")
    assert sorted({m.group(0) for m in SECOND_PERSON.finditer(text)}) == []
    assert "PaLLi" not in text and "PALLI" not in text


@pytest.mark.parametrize("render", [_widen, _keep])
def test_no_render_addresses_her_as_you(render):
    rendered = render()
    assert sorted({m.group(0) for m in SECOND_PERSON.finditer(rendered)}) == []


@pytest.mark.parametrize("path", [WIDEN, KEEP])
def test_new_prompt_files_are_lf(path):
    assert b"\r\n" not in path.read_bytes()


# ── the call shapes, where the lane scanner sees them ────────────────────


def test_each_lane_has_its_call_shape_and_never_thinks():
    from ai.llm_openai_compatible import OpenAICompatibleProvider, is_no_think_lane
    from ai.memory_reach_look import CALL_ARGS, KEEP_LANE, WIDEN_LANE

    assert (WIDEN_LANE, KEEP_LANE) == ("memory_reach_widen", "memory_reach_keep")
    assert set(CALL_ARGS) == {WIDEN_LANE, KEEP_LANE}
    for lane, args in CALL_ARGS.items():
        assert args["log_context"] == lane
        assert args["temperature"] == 0.2
        assert is_no_think_lane(lane)
        assert (OpenAICompatibleProvider.request_timeout_for_context(lane)
                == float(OpenAICompatibleProvider.REQUEST_TIMEOUT_SECONDS))
    assert CALL_ARGS[WIDEN_LANE]["max_tokens"] == 600
    assert CALL_ARGS[KEEP_LANE]["max_tokens"] == 900
