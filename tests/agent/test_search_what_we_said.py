"""R15, T8 — the tool ``search_what_we_said``: PaLLi's search of the record
of everything said, on the owner's turns only, registered only while
companion.memory_reach is on.

Fable's words (the brief's items 1–17, and Draft 1's 7a, 9a, 14a and 16a,
as the owner approved them on 10-13) are pinned verbatim. Nothing here
reaches a real model, the real record or the real embedder: the catch-up,
the search and the second look are fakes at their own modules. Stand-in
names only (Robin is the owner; Dana and Sam are friends).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ai.memory_reach_index as mri
import ai.memory_reach_look as mrl
import ai.memory_reach_search as mrs
import core.agent.builtin_tools as bt
from ai.memory_reach_look import LookResult
from ai.memory_reach_search import NO_ROOM, Hit, Scope
from core.agent.builtin_tools.search_what_we_said import SearchWhatWeSaidTool
from core.agent.session import ToolUseBlock
from core.agent.tool_outcome import ToolFailure
from core.agent.tools import ToolContext

NAME = "search_what_we_said"
ROOT = Path(__file__).resolve().parents[2]

# The brief's "Words for the owner's yes", items 1–17 (and 7a, 9a, 14a, 16a), byte for byte.
ITEMS_1_6 = [
    "Search the record of everything we've said: every message since the start, both sides, "
    "in every chat, by exact words or by meaning.",
    "Reach for it when a question turns on what was actually said: what they told you, what "
    "you sent, when, in which words.",
    "Give the words you expect to find, or a phrase in quotes for an exact match; add dates "
    "when you know roughly when.",
    "The quick look returns the lines, dated, with who said them and where.",
    "If the lines don't settle it, call again with look_harder=true: a wider look that reads "
    "around each hit, tries other wordings and widens the dates, about ten seconds.",
    "When nothing comes back you haven't found it; that isn't proof it wasn't said.",
]
INPUTS_7_12 = {
    "query": "The words you expect to find, or a phrase in quotes for the exact words.",
    "exact": "Only lines with this exact phrase (default false).",
    "since": "A date, YYYY-MM-DD, to narrow when (optional).",
    "until": "A date, YYYY-MM-DD, to narrow when (optional).",
    "who": 'Whose lines: "them", "me", a name, or "anyone" (default anyone).',
    "room": '"here" for this chat; leave it out for every chat.',
    "look_harder": "A wider look: nearby lines, other wordings, wider dates; about ten seconds "
                   "(default false).",
}
ITEM_7A = "No search ran: the query was empty. Give the words you expect to find."
ITEM_9A = ('No search ran: "{value}" isn\'t a date in the form YYYY-MM-DD. '
           "Fix it or leave it out.")
LINE_13 = "Found {n} lines ({where}, from {since} to {until}{words_only}):"
LINE_13_ONE = "Found 1 line ({where}, from {since} to {until}{words_only}):"
LINE_14 = "(The quick look was thin, so this is the wider look; it took {seconds} seconds.)"
LINE_14A = "(This is the wider look you asked for; it took {seconds} seconds.)"
LINE_15 = ("(This was a quick look. If these lines don't settle it, call again with "
           "look_harder=true for a wider look: nearby lines, other wordings, wider dates; "
           "about ten seconds.)")
LINE_16 = ('Nothing found for "{query}": searched {where}, from {since} to {until}'
           "{words_only}{wider}. Not finding it doesn't mean it wasn't said.")
LINE_16A = ('"here" means a chat room, and this isn\'t one, so nothing was searched. '
            "Leave room out to search every chat.")
LINE_17 = "(The wider look ran out of its ten seconds; this is what it had gathered.)"
WIDER = ", and the wider look too"
WORDS_ONLY = ", by words only"

STAMP = re.compile(r"\[[A-Z][a-z]{2} \d{1,2}( \d{4})? \d{2}:\d{2}\]")
TS = datetime(2026, 10, 5, 19, 2, tzinfo=timezone.utc).timestamp()


def _hit(i: int, speaker: str, text: str, *, room: str = "discord:111", name: str = "",
         score: float = 0.8, exact: bool = False) -> Hit:
    return Hit(id=i, room=room, ts=TS + 60 * i, day="2026-10-05", speaker=speaker,
               name=name or {"owner": "Robin", "palli": "PaLLi"}.get(speaker, ""), text=text,
               score=score, words=score, meaning=score, exact=exact)


STRONG_HITS = [
    _hit(1, "owner", "the kite shop on Elm opens at nine on Saturdays"),
    _hit(2, "palli", "noted: nine on Saturdays at the kite shop"),
    _hit(3, "guest", "I went there last week, Elm is busy", name="Dana"),
]
THIN_HITS = [_hit(4, "owner", "something about kites", score=0.2)]


def _ctx(*, owner: bool = True, surface: str = "discord_text", channel_id: int = 111,
         owner_id: int = 1) -> ToolContext:
    return ToolContext(turn_owner_id=owner_id, is_owner=owner, surface=surface,
                       channel_id=channel_id)


class Fakes:
    """The catch-up, the search and the second look, faked at their modules."""

    def __init__(self, monkeypatch, *, hits=None, look: LookResult | None = None,
                 words_only: bool = False) -> None:
        self.catch_ups: list[dict] = []
        self.searches: list[tuple[str, dict]] = []
        self.looks: list[tuple[tuple, dict]] = []
        self.hits = list(STRONG_HITS if hits is None else hits)
        self.words_only = words_only
        self.look = look or LookResult(text="", hits=[], rounds=1, calls=2, seconds=3.4,
                                       timed_out=False, thin=True)

        async def catch_up(**kwargs):
            self.catch_ups.append(kwargs)
            return {}

        async def search(query, **kwargs):
            self.searches.append((query, kwargs))
            found = mrs.Hits(self.hits)          # what the real search gives back
            found.words_only = self.words_only
            return found

        async def second_look(*args, **kwargs):
            self.looks.append((args, kwargs))
            return self.look

        monkeypatch.setattr(mri, "catch_up", catch_up)
        monkeypatch.setattr(mrs, "search", search)
        monkeypatch.setattr(mrl, "second_look", second_look)


def _run(tool_input: dict, ctx: ToolContext | None = None):
    return asyncio.run(SearchWhatWeSaidTool().execute(tool_input, ctx or _ctx()))


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(mri, "enabled", lambda: True)


# ── the shape and Fable's words ───────────────────────────────────────────

def test_shape():
    tool = SearchWhatWeSaidTool()
    assert tool.name == NAME
    assert tool.owner_only is True and tool.requires_approval is False
    assert set(tool.input_schema["properties"]) == set(INPUTS_7_12)
    assert tool.input_schema["required"] == ["query"]
    props = tool.input_schema["properties"]
    assert {k: v["type"] for k, v in props.items()} == {
        "query": "string", "exact": "boolean", "since": "string", "until": "string",
        "who": "string", "room": "string", "look_harder": "boolean"}


def test_the_description_is_items_1_to_6_verbatim():
    assert SearchWhatWeSaidTool.description == " ".join(ITEMS_1_6)


def test_each_input_says_items_7_to_12_verbatim():
    props = SearchWhatWeSaidTool.input_schema["properties"]
    assert {k: v["description"] for k, v in props.items()} == INPUTS_7_12


# ── registration ──────────────────────────────────────────────────────────

def _names(reg) -> list[str]:
    return reg.all_names()


def _schemas_bytes(reg, ctx=None) -> str:
    return json.dumps(reg.schemas_for(ctx or _ctx()), sort_keys=False)


def _inventory(reg) -> str:
    return asyncio.run(reg.get("whoami").execute({}, _ctx()))


def test_absent_when_the_switch_is_off():
    reg = bt.build_default_registry()
    assert NAME not in _names(reg)
    assert NAME not in _schemas_bytes(reg)
    assert NAME not in _inventory(reg)


def test_registered_when_the_switch_is_on_and_whoami_lists_it(switch_on):
    reg = bt.build_default_registry()
    assert NAME in _names(reg)
    assert NAME in _inventory(reg)
    assert NAME not in _names(bt.build_default_registry(surface="workbench"))


def test_switch_off_the_tool_list_is_byte_identical(monkeypatch):
    """The switch adds this one tool and changes nothing else: on, minus the
    tool, is the off list byte for byte (each schema in its order), and the
    off build never constructs the tool."""
    built = []
    real_init = SearchWhatWeSaidTool.__init__

    def counting_init(self, *a, **k):
        built.append(1)
        real_init(self, *a, **k)

    monkeypatch.setattr(SearchWhatWeSaidTool, "__init__", counting_init)
    off = bt.build_default_registry()
    off_bytes = _schemas_bytes(off)
    off_names = _names(off)
    assert built == []

    monkeypatch.setattr(mri, "enabled", lambda: True)
    on = bt.build_default_registry()
    assert built == [1]
    on_schemas = on.schemas_for(_ctx())
    assert [s["name"] for s in on_schemas].count(NAME) == 1
    assert json.dumps([s for s in on_schemas if s["name"] != NAME]) == off_bytes
    assert [n for n in _names(on) if n != NAME] == off_names


def test_a_broken_switch_reads_as_off(monkeypatch):
    def broken():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(mri, "enabled", broken)
    assert NAME not in _names(bt.build_default_registry())


def test_the_gate_reads_the_switch_through_the_index_module(monkeypatch):
    """The suite's pin patches ``ai.memory_reach_index.enabled``; the gate
    must read it there at call time, or the pin would not hold."""
    monkeypatch.setattr(mri, "enabled", lambda: True)
    assert bt._memory_reach_enabled() is True
    monkeypatch.setattr(mri, "enabled", lambda: False)
    assert bt._memory_reach_enabled() is False


# ── owner-only ────────────────────────────────────────────────────────────

def test_a_guest_never_sees_or_runs_it_even_with_the_switch_on(switch_on, monkeypatch):
    fakes = Fakes(monkeypatch)
    reg = bt.build_default_registry()
    guest = _ctx(owner=False, owner_id=42)
    assert NAME not in [s["name"] for s in reg.schemas_for(guest)]
    result = asyncio.run(reg.dispatch(
        ToolUseBlock(id="t1", name=NAME, input={"query": "kite shop"}), guest))
    assert result.is_error is True
    # The tool's own guard, past the registry (a direct call).
    out = _run({"query": "kite shop"}, guest)
    assert isinstance(out, ToolFailure)
    assert "owner-only" in str(out)
    assert fakes.catch_ups == [] and fakes.searches == [] and fakes.looks == []


# ── the quick look ────────────────────────────────────────────────────────

def test_the_quick_look_renders_dated_lines_with_who_and_line_15(monkeypatch):
    fakes = Fakes(monkeypatch)
    out = _run({"query": "kite shop hours"})
    assert isinstance(out, str)
    head, *rest = out.split("\n")
    assert head == LINE_13.format(n=3, where="every chat", since="the start", until="now",
                                  words_only="")
    assert "In the Discord chat 111:" in out
    assert len(STAMP.findall(out)) == 3
    assert re.search(r"\] them: the kite shop on Elm opens at nine on Saturdays$", out, re.M)
    assert re.search(r"\] me: noted: nine on Saturdays at the kite shop$", out, re.M)
    assert re.search(r"\] Dana: I went there last week, Elm is busy$", out, re.M)
    assert out.endswith("\n\n" + LINE_15)
    assert fakes.looks == []
    assert fakes.catch_ups == [{"budget_s": 1.5}]
    query, kwargs = fakes.searches[0]
    assert query == "kite shop hours"
    assert kwargs["scope"] == Scope(None)


def test_the_first_search_takes_who_exact_and_the_dates(monkeypatch):
    fakes = Fakes(monkeypatch)
    out = _run({"query": '"nine on Saturdays"', "exact": True, "who": "Dana",
                "since": "2026-10-01", "until": "2026-10-31"})
    _, kwargs = fakes.searches[0]
    assert kwargs["exact"] is True and kwargs["who"] == "Dana"
    assert (kwargs["since"], kwargs["until"]) == ("2026-10-01", "2026-10-31")
    assert out.startswith(LINE_13.format(n=3, where="every chat", since="2026-10-01",
                                         until="2026-10-31", words_only=WORDS_ONLY))


def test_flags_given_as_words_are_read(monkeypatch):
    fakes = Fakes(monkeypatch)
    _run({"query": "kite", "exact": "false", "look_harder": "true"})
    assert fakes.searches[0][1]["exact"] is False
    assert fakes.looks[0][1]["look_harder"] is True


# ── nothing found ─────────────────────────────────────────────────────────

def test_nothing_found_names_the_scope_and_the_wider_look(monkeypatch):
    fakes = Fakes(monkeypatch, hits=[])
    out = _run({"query": "the blue umbrella", "room": "here"})
    assert out == LINE_16.format(query="the blue umbrella", where="this chat",
                                 since="the start", until="now", words_only="", wider=WIDER)
    assert len(fakes.looks) == 1              # nothing is thin: the wider look ran


def test_nothing_found_in_every_chat_with_dates(monkeypatch):
    Fakes(monkeypatch, hits=[])
    out = _run({"query": "the blue umbrella", "since": "2026-09-01"})
    assert out == LINE_16.format(query="the blue umbrella", where="every chat",
                                 since="2026-09-01", until="now", words_only="", wider=WIDER)


# ── room="here" ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("surface, channel, key", [
    ("discord_text", 777, "discord:777"),               # a DM is its own room
    ("telegram_text", -100123, "telegram:-100123"),     # a Telegram group
])
def test_here_scopes_to_the_turns_own_room(monkeypatch, surface, channel, key):
    fakes = Fakes(monkeypatch)
    _run({"query": "kite", "room": "here"}, _ctx(surface=surface, channel_id=channel))
    assert fakes.searches[0][1]["scope"] == Scope(key)


def test_here_reads_the_room_by_the_search_modules_own_rule(monkeypatch):
    seen = []
    real = mrs._room_of

    def spy(turn):
        seen.append(turn)
        return real(turn)

    monkeypatch.setattr(mrs, "_room_of", spy)
    Fakes(monkeypatch)
    _run({"query": "kite", "room": "here"}, _ctx(channel_id=777))
    assert len(seen) == 1


@pytest.mark.parametrize("surface, channel", [
    ("dashboard", 0), ("voice_local", 0), ("discord_text", 0), ("discord_voice", 555),
])
def test_here_with_no_real_room_finds_nothing_and_never_widens(monkeypatch, surface, channel):
    fakes = Fakes(monkeypatch)
    out = _run({"query": "kite", "room": "here"}, _ctx(surface=surface, channel_id=channel))
    assert out == LINE_16A                    # item 16a replaces 16: nothing was searched
    assert fakes.searches == [] and fakes.looks == [] and fakes.catch_ups == []


def test_no_room_given_is_every_chat(monkeypatch):
    fakes = Fakes(monkeypatch)
    _run({"query": "kite", "room": "everywhere"})
    assert fakes.searches[0][1]["scope"] == Scope(None)


# ── the second look ───────────────────────────────────────────────────────

KEPT = "[Oct 5 19:02] them: the kite shop on Elm opens at nine on Saturdays"


def test_a_thin_result_takes_the_second_look(monkeypatch):
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=2, calls=3, seconds=6.24,
                      timed_out=False, thin=False)
    fakes = Fakes(monkeypatch, hits=THIN_HITS, look=look)
    out = _run({"query": "kite shop", "since": "2026-10-01", "who": "them", "exact": True})
    args, kwargs = fakes.looks[0]
    assert args == ("kite shop", THIN_HITS)
    # The harder look searches every speaker in scope and not only the
    # phrase: who and exact are the first search's alone.
    assert kwargs == {"scope": Scope(None), "since": "2026-10-01", "until": None,
                      "look_harder": False}
    assert out == LINE_14.format(seconds="6.2") + "\n" + KEPT
    assert LINE_15 not in out


def test_look_harder_widens_even_a_strong_result(monkeypatch):
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=1, calls=2, seconds=4.0,
                      timed_out=False, thin=False)
    fakes = Fakes(monkeypatch, look=look)
    out = _run({"query": "kite shop", "look_harder": True, "room": "here"},
               _ctx(channel_id=777))
    assert fakes.looks[0][1]["look_harder"] is True
    assert fakes.looks[0][1]["scope"] == Scope("discord:777")
    assert out == LINE_14A.format(seconds="4.0") + "\n" + KEPT


def test_a_look_that_ran_out_of_time_says_so(monkeypatch):
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=1, calls=1, seconds=10.0,
                      timed_out=True, thin=True)
    Fakes(monkeypatch, hits=THIN_HITS, look=look)
    out = _run({"query": "kite shop"})
    assert out == LINE_17 + "\n" + KEPT


def test_a_look_that_ran_out_of_time_with_nothing_says_both(monkeypatch):
    look = LookResult(text="", hits=[], rounds=1, calls=1, seconds=10.0,
                      timed_out=True, thin=True)
    Fakes(monkeypatch, hits=[], look=look)
    out = _run({"query": "kite shop"})
    assert out == LINE_17 + "\n" + LINE_16.format(
        query="kite shop", where="every chat", since="the start", until="now",
        words_only="", wider=WIDER)


# ── one line or many; the wider look asked for; by words only ─────────────

def _one_exact_hit() -> list[Hit]:
    return [_hit(1, "owner", "the kite shop on Elm opens at nine", score=1.0, exact=True)]


def test_one_line_is_found_1_line_not_1_lines(monkeypatch):
    Fakes(monkeypatch, hits=_one_exact_hit())      # an exact hit is not thin: the quick look
    out = _run({"query": "kite shop"})
    head = out.split("\n")[0]
    assert head == LINE_13_ONE.format(where="every chat", since="the start", until="now",
                                      words_only="")
    assert head == "Found 1 line (every chat, from the start to now):"
    assert "lines" not in head and out.endswith("\n\n" + LINE_15)


def test_one_line_that_fits_of_several_is_still_one_line(monkeypatch):
    big = [_hit(i, "owner", "word " * 500) for i in (1, 2)]
    Fakes(monkeypatch, hits=big)
    out = _run({"query": "word"})
    head = out.split("\n")[0]
    assert len(STAMP.findall(out)) == 1
    assert head.startswith("Found 1 line (") and not head.startswith("Found 1 lines")


def test_two_lines_are_found_2_lines(monkeypatch):
    Fakes(monkeypatch, hits=STRONG_HITS[:2])
    assert _run({"query": "kite"}).split("\n")[0].startswith("Found 2 lines (")


def test_a_thin_quick_look_gets_item_14_with_no_round_count(monkeypatch):
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=2, calls=3, seconds=9.26,
                      timed_out=False, thin=False)
    Fakes(monkeypatch, hits=THIN_HITS, look=look)
    out = _run({"query": "kite shop"})
    assert out.split("\n")[0] == ("(The quick look was thin, so this is the wider look; "
                                  "it took 9.3 seconds.)")
    assert "round" not in out and LINE_14A.format(seconds="9.3") not in out


@pytest.mark.parametrize("hits", [THIN_HITS, STRONG_HITS, []], ids=["thin", "strong", "none"])
def test_a_wider_look_that_was_asked_for_gets_item_14a_thin_or_not(monkeypatch, hits):
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=2, calls=3, seconds=4.04,
                      timed_out=False, thin=False)
    Fakes(monkeypatch, hits=hits, look=look)
    out = _run({"query": "kite shop", "look_harder": True})
    assert out.split("\n")[0] == "(This is the wider look you asked for; it took 4.0 seconds.)"
    assert "thin" not in out.split("\n")[0] and "round" not in out


def test_look_harder_given_as_a_word_is_the_asked_for_line(monkeypatch):
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=1, calls=2, seconds=2.0,
                      timed_out=False, thin=False)
    Fakes(monkeypatch, look=look)
    assert _run({"query": "kite", "look_harder": "true"}).startswith(
        LINE_14A.format(seconds="2.0") + "\n")
    assert _run({"query": "kite", "look_harder": "false"}).startswith("Found 3 lines")


def test_item_17_wins_over_14_and_14a_when_the_look_ran_out_of_time(monkeypatch):
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=1, calls=1, seconds=10.0,
                      timed_out=True, thin=True)
    Fakes(monkeypatch, look=look)
    assert _run({"query": "kite", "look_harder": True}) == LINE_17 + "\n" + KEPT


def test_by_words_only_is_added_for_an_exact_search_and_for_a_search_without_meaning(
        monkeypatch):
    # an exact-phrase search
    Fakes(monkeypatch)
    out = _run({"query": '"nine on Saturdays"', "exact": True})
    assert out.split("\n")[0] == ("Found 3 lines (every chat, from the start to now, "
                                  "by words only):")
    # a search the record says ran without its meaning leg (off, failed, over its time)
    Fakes(monkeypatch, words_only=True)
    out = _run({"query": "nine on Saturdays", "since": "2026-10-01", "room": "here"})
    assert out.split("\n")[0] == ("Found 3 lines (this chat, from 2026-10-01 to now, "
                                  "by words only):")
    Fakes(monkeypatch, hits=_one_exact_hit(), words_only=True)
    assert _run({"query": "kite"}).split("\n")[0] == (
        "Found 1 line (every chat, from the start to now, by words only):")


def test_by_words_only_is_left_off_when_meaning_ran_or_nobody_can_tell(monkeypatch):
    Fakes(monkeypatch)                                    # words_only False: meaning ran
    out = _run({"query": "nine on Saturdays", "exact": False})
    assert "by words" not in out
    # a plain list from a search that says nothing about it: still no tail
    fakes = Fakes(monkeypatch)

    async def plain_search(query, **kwargs):
        return list(STRONG_HITS)

    monkeypatch.setattr(mrs, "search", plain_search)
    assert "by words" not in _run({"query": "kite"}) and fakes.looks == []


def test_by_words_only_rides_item_16_before_the_wider_look_tail(monkeypatch):
    Fakes(monkeypatch, hits=[], words_only=True)
    out = _run({"query": "the blue umbrella"})
    assert out == LINE_16.format(query="the blue umbrella", where="every chat",
                                 since="the start", until="now", words_only=WORDS_ONLY,
                                 wider=WIDER)
    assert ("from the start to now, by words only, and the wider look too. "
            "Not finding it") in out
    Fakes(monkeypatch, hits=[])
    exact = _run({"query": "the blue umbrella", "exact": True})
    assert ", by words only, and the wider look too." in exact


def test_the_wider_looks_own_lines_never_carry_the_quick_looks_tail(monkeypatch):
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=1, calls=2, seconds=3.0,
                      timed_out=False, thin=False)
    Fakes(monkeypatch, look=look, words_only=True)
    out = _run({"query": "kite", "look_harder": True, "exact": True})
    assert out == LINE_14A.format(seconds="3.0") + "\n" + KEPT


def test_here_with_no_chat_room_is_item_16a_and_with_a_room_it_is_still_this_chat(
        monkeypatch):
    fakes = Fakes(monkeypatch, hits=[])
    no_room = _run({"query": "kite", "room": "here"}, _ctx(surface="dashboard", channel_id=0))
    assert no_room == ('"here" means a chat room, and this isn\'t one, so nothing was searched. '
                       "Leave room out to search every chat.")
    assert not isinstance(no_room, ToolFailure) and "this chat" not in no_room
    assert fakes.searches == []
    real = _run({"query": "kite", "room": "here"}, _ctx(channel_id=777))
    assert real == LINE_16.format(query="kite", where="this chat", since="the start",
                                  until="now", words_only="", wider=WIDER)
    # without room="here" a turn with no room searches every chat as before
    elsewhere = _run({"query": "kite"}, _ctx(surface="dashboard", channel_id=0))
    assert "every chat" in elsewhere and len(fakes.searches) == 2


# ── bad input: plain answers, never a raise ──────────────────────────────

@pytest.mark.parametrize("field, value, shown", [
    ("since", "next tuesday", "next tuesday"), ("until", "2026-13-45", "2026-13-45"),
    ("since", ["2026-10-01"], "['2026-10-01']"),
])
def test_a_bad_date_is_an_error_that_names_it_item_9a(monkeypatch, field, value, shown):
    fakes = Fakes(monkeypatch)
    out = _run({"query": "kite", field: value})
    assert isinstance(out, ToolFailure) and str(out) == ITEM_9A.format(value=shown)
    assert fakes.searches == [] and fakes.looks == [] and fakes.catch_ups == []


def test_item_9a_names_the_date_that_is_bad_and_cuts_a_long_one_at_40(monkeypatch):
    fakes = Fakes(monkeypatch)
    out = _run({"query": "kite", "since": "2026-10-01", "until": "the day after\nwe went to the "
                "lake with everyone, in the rain"})
    text = str(out)
    assert isinstance(out, ToolFailure)
    assert text == ITEM_9A.format(value="the day after we went to the lake with…")
    value = text.split('"')[1]
    assert len(value) <= 40 and "\n" not in text
    both = _run({"query": "kite", "since": "soon", "until": "later"})
    assert str(both) == ITEM_9A.format(value="soon")        # the first one found
    assert fakes.searches == []


def test_an_empty_query_is_an_error_item_7a(monkeypatch):
    fakes = Fakes(monkeypatch)
    for query in ("   ", "", None):
        out = _run({"query": query})
        assert isinstance(out, ToolFailure) and str(out) == ITEM_7A
    assert fakes.searches == [] and fakes.looks == [] and fakes.catch_ups == []


# ── the cap ───────────────────────────────────────────────────────────────

def test_the_result_is_capped_at_4000_characters(monkeypatch):
    long = [_hit(i, "owner", f"line {i} " + "word " * 300) for i in range(1, 13)]
    Fakes(monkeypatch, hits=long)
    out = _run({"query": "word"})
    assert len(out) <= 4000
    assert out.endswith(LINE_15)
    n = int(re.match(r"Found (\d+) lines", out).group(1))
    assert 1 <= n < 12 and len(STAMP.findall(out)) == n


def test_a_long_look_or_query_is_still_capped(monkeypatch):
    look = LookResult(text="x" * 9000, hits=STRONG_HITS, rounds=1, calls=2, seconds=1.0,
                      timed_out=False, thin=False)
    Fakes(monkeypatch, hits=THIN_HITS, look=look)
    assert len(_run({"query": "kite"})) <= 4000
    Fakes(monkeypatch, hits=[])
    assert len(_run({"query": "kite " * 2000})) <= 4000


# ── the log line ──────────────────────────────────────────────────────────

LOGGER = "core.agent.builtin_tools.search_what_we_said"


def _reach_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.getMessage().startswith("memory reach:")]


def test_one_log_line_per_call_counts_only(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    look = LookResult(text=KEPT, hits=STRONG_HITS, rounds=2, calls=3, seconds=6.0,
                      timed_out=False, thin=False)
    Fakes(monkeypatch, hits=THIN_HITS, look=look)
    query = "the kite shop on Elm"
    _run({"query": query, "room": "here"}, _ctx(channel_id=987654321))
    lines = _reach_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert re.fullmatch(r"memory reach: query_chars=20 scope=room hits=1 thin=True rounds=2 "
                        r"calls=3 seconds=\d+\.\d\d", line)
    for word in ("kite", "Elm", "987654321", "discord"):
        assert word not in line


def test_the_log_line_says_all_for_every_chat_and_still_logs_a_refused_date(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    Fakes(monkeypatch)
    _run({"query": "kite"})
    _run({"query": "kite", "since": "someday"})
    lines = _reach_lines(caplog)
    assert len(lines) == 2
    assert " scope=all hits=3 thin=False rounds=0 calls=0 " in lines[0]
    assert " scope=all hits=0 " in lines[1]
    assert "someday" not in lines[1]


def _replay_harness():
    """The replay's harness by path, with no bytecode written under evals/.
    The public copy ships without evals/ (private tooling), so there it skips."""
    if not (ROOT / "evals" / "memory_reach_replay" / "harness.py").is_file():
        pytest.skip("evals/ is not in this tree (the public copy ships without it)")
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "memory_reach_replay_harness_t8", ROOT / "evals" / "memory_reach_replay" / "harness.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = saved


def test_the_replay_reads_the_line_as_a_search(monkeypatch, caplog):
    parse_reach_line = _replay_harness().parse_reach_line
    caplog.set_level(logging.INFO, logger=LOGGER)
    Fakes(monkeypatch)
    _run({"query": "kite"})
    parsed = parse_reach_line(_reach_lines(caplog)[0])
    assert parsed["kind"] == "search" and parsed["query_chars"] == 4
    assert parsed["hits"] == 3 and parsed["rounds"] == 0


# ── a shared room: a friend never reads what the search found ─────────────

def test_in_a_shared_room_a_friend_gets_the_search_by_name_only():
    """The search reaches every chat, so its input and result are the
    owner's alone: a collaborator's payload carries the call by name only
    (core/agent/session.py OWNER_PRIVATE_RECEIPT_TOOLS), the owner's keeps it."""
    from core.agent.session import (
        OWNER_ONLY_RECEIPT_TAIL,
        RECEIPT_MARK,
        AgentMessage,
        TextBlock,
        ToolResultBlock,
        diet_for_llm,
    )

    call = ToolUseBlock(id="s1", name=NAME, input={"query": "QUERY-SECRET door code"})
    found = "Found 1 line (every chat, from the start to now):\n" \
            "In the Discord chat 42:\n[Oct 5 19:02] them: DM-SECRET the code is 4321"
    rows = [
        AgentMessage(role="user", content=[TextBlock(text="what was that thing I told you?")]),
        AgentMessage(role="assistant", content=[TextBlock(text="let me look"), call]),
        AgentMessage(role="tool", content=[ToolResultBlock(tool_use_id="s1", content=found)]),
        AgentMessage(role="assistant", content=[TextBlock(text="Found it.")]),
        AgentMessage(role="user", content=[TextBlock(text="[Sam]: what did you find?")]),
    ]

    def flat(msgs) -> str:
        return "\n".join(b.text for m in msgs for b in m.content if isinstance(b, TextBlock))

    friend = flat(diet_for_llm(rows, keep_from=len(rows), audience="collaborator"))
    assert f"{RECEIPT_MARK} {NAME} {OWNER_ONLY_RECEIPT_TAIL}" in friend
    assert "SECRET" not in friend and "4321" not in friend
    owner = flat(diet_for_llm(rows, keep_from=len(rows), audience="owner"))
    assert "QUERY-SECRET" in owner and "DM-SECRET" in owner


# ── what the reply used (R15, T13 + W3): the hand-off to the runner ───────
# The tool hands the runner what it gave the reply — the dated lines it
# returned, the query it searched, and the search's counts (the same as its
# log line) — on the turn's one ToolContext, the ``deliveries`` way: only
# once the result stands. The runner writes them to core/reply_sources.py.

def test_a_fresh_context_has_empty_hand_off_lists_of_its_own():
    a, b = _ctx(), _ctx()
    assert a.record_lines == [] and a.record_queries == [] and a.record_looks == []
    a.record_lines.append("x")
    assert b.record_lines == []


def test_the_quick_looks_dated_lines_and_query_are_handed_over(monkeypatch):
    Fakes(monkeypatch)
    ctx = _ctx()
    out = _run({"query": "kite shop"}, ctx)
    shown = [line for line in out.splitlines() if STAMP.match(line)]
    assert len(shown) == 3
    assert ctx.record_lines == shown          # the dated lines only: no room heading, no sentence
    assert ctx.record_queries == ["kite shop"]
    (look,) = ctx.record_looks
    assert {k: look[k] for k in ("hits", "thin", "rounds", "calls")} == {
        "hits": 3, "thin": False, "rounds": 0, "calls": 0}
    assert isinstance(look["seconds"], float) and look["seconds"] >= 0


def test_the_second_looks_kept_lines_and_counts_are_handed_over(monkeypatch):
    look = LookResult(text=f"In the Discord chat 111:\n{KEPT}\n", hits=STRONG_HITS, rounds=2,
                      calls=3, seconds=6.24, timed_out=False, thin=False)
    Fakes(monkeypatch, hits=THIN_HITS, look=look)
    ctx = _ctx()
    _run({"query": "kite shop"}, ctx)
    assert ctx.record_lines == [KEPT]
    assert ctx.record_queries == ["kite shop"]
    assert {k: ctx.record_looks[0][k] for k in ("hits", "thin", "rounds", "calls")} == {
        "hits": 1, "thin": True, "rounds": 2, "calls": 3}


def test_nothing_found_hands_over_the_query_and_no_lines(monkeypatch):
    Fakes(monkeypatch, hits=[])
    ctx = _ctx()
    out = _run({"query": "kite shop"}, ctx)
    assert out.startswith('Nothing found for "kite shop"')
    assert ctx.record_lines == [] and ctx.record_queries == ["kite shop"]
    assert len(ctx.record_looks) == 1


def test_two_searches_in_one_reply_add_up(monkeypatch):
    Fakes(monkeypatch)
    ctx = _ctx()
    _run({"query": "kite shop"}, ctx)
    _run({"query": "Elm"}, ctx)
    assert ctx.record_queries == ["kite shop", "Elm"]
    assert len(ctx.record_lines) == 6 and len(ctx.record_looks) == 2


@pytest.mark.parametrize("tool_input, ctx_kwargs", [
    ({"query": "  "}, {}),                                             # 7a: no search ran
    ({"query": "kite", "since": "last week"}, {}),                     # 9a: no search ran
    ({"query": "kite", "room": "here"}, {"surface": "dashboard", "channel_id": 0}),   # 16a
    ({"query": "kite"}, {"owner": False}),                             # never a guest's
])
def test_a_call_where_no_search_ran_hands_over_nothing(monkeypatch, tool_input, ctx_kwargs):
    fakes = Fakes(monkeypatch)
    ctx = _ctx(**ctx_kwargs)
    _run(tool_input, ctx)
    assert fakes.searches == []
    assert ctx.record_lines == [] and ctx.record_queries == [] and ctx.record_looks == []


def test_a_search_that_raises_hands_over_nothing(monkeypatch):
    Fakes(monkeypatch)

    async def broken(query, **kwargs):
        raise RuntimeError("the record is locked")

    monkeypatch.setattr(mrs, "search", broken)
    ctx = _ctx()
    with pytest.raises(RuntimeError):
        _run({"query": "kite shop"}, ctx)
    assert ctx.record_lines == [] and ctx.record_queries == [] and ctx.record_looks == []
