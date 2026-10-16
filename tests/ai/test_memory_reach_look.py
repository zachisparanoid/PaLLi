"""The second look, capped and measured (R15, T10; plan
docs/2026-10-13-memory-that-reaches-plan.md).

When the quick search of the record is thin, PaLLi widens it: other
wordings, wider dates, the lines around a hit, a thread followed; then she
keeps what bears on the question. Code holds the resource facts only: at
most 2 rounds, 3 model calls and 10 seconds, the room scope the caller
gave (never widened), and how much text comes back.

The fixture record holds two rooms: ``discord:1`` (Robin, the owner, with
Dana) and ``discord:2`` (Robin with Sam). "kite shop" is said in both.
Every model here is a fake and the embedder is a fake: no network.
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import logging
import sqlite3
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from ai import memory_reach_index as mri
from ai import memory_reach_look as look
from ai import memory_reach_search as mrs
from ai.memory_reach_search import Hit, Scope

ROOT = Path(__file__).resolve().parents[2]
T0 = 1_791_500_000.0          # October 2026
DANA_ROOM = "discord:1"
SAM_ROOM = "discord:2"

# (ref, room, seconds after T0, speaker, name, text)
_LINES = [
    # discord:1 — Robin and Dana
    ("a1", DANA_ROOM, 0, "owner", "Robin", "morning Dana"),
    ("a2", DANA_ROOM, 60, "guest", "Dana", "the kite shop on Elm opens at nine on Saturdays"),
    ("a3", DANA_ROOM, 120, "palli", "PaLLi", "Nine on Saturdays, the kite shop it is"),
    ("a4", DANA_ROOM, 180, "owner", "Robin", "the green kite cost forty dollars"),
    ("a5", DANA_ROOM, 240, "guest", "Dana", "worth every dollar"),
    # discord:2 — Robin and Sam
    ("b1", SAM_ROOM, 1000, "owner", "Robin", "Sam, is the boat fixed yet?"),
    ("b2", SAM_ROOM, 1060, "guest", "Sam", "the boat engine is fixed, it cost three hundred"),
    ("b3", SAM_ROOM, 1120, "owner", "Robin", "great news"),
    ("b4", SAM_ROOM, 1180, "palli", "PaLLi", "Then the boat is ready for the river"),
    ("b5", SAM_ROOM, 1240, "guest", "Sam", "the kite shop by the harbour closes for winter"),
    ("b6", SAM_ROOM, 1300, "owner", "Robin", "noted"),
    ("b7", SAM_ROOM, 1360, "guest", "Sam", "the harbour kite shop reopens in April"),
]


class FakeEmbed:
    """Instant, no network. The record holds no vectors, so every line
    scores by its words alone: the scores are the share of the query's
    content words a line holds."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class FakeLLM:
    """The model, faked: ``widen`` answers each widen page in turn (the last
    one again once they run out), ``keep`` answers the keep page. Records
    every page it is given. ``sleep`` makes every call that slow."""

    def __init__(self, widen=("{}",), keep="kept: nothing", *, sleep: float = 0.0,
                 keep_sleep: float = 0.0) -> None:
        self.widen = [w if isinstance(w, str) else json.dumps(w) for w in widen]
        self.keep = keep
        self.sleep = sleep
        self.keep_sleep = keep_sleep
        self.pages: list[str] = []

    @staticmethod
    def is_keep(page: str) -> bool:
        return "What I keep" in page

    async def __call__(self, page: str) -> str:
        self.pages.append(page)
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.is_keep(page):
            if self.keep_sleep:
                await asyncio.sleep(self.keep_sleep)
            return self.keep
        n = sum(1 for p in self.pages if not self.is_keep(p))
        return self.widen[min(n, len(self.widen)) - 1]

    @property
    def kinds(self) -> list[str]:
        return ["keep" if self.is_keep(p) else "widen" for p in self.pages]


@pytest.fixture(autouse=True)
def _no_backoff_between_tests():
    mri._EMBED_PAUSE["until"] = 0.0
    yield
    mri._EMBED_PAUSE["until"] = 0.0


@pytest.fixture
def record(tmp_path) -> Path:
    path = tmp_path / "record.sqlite3"
    rows = []
    for ref, room, dt, speaker, name, text in _LINES:
        ts = T0 + dt
        rows.append(mri.Row(source="db" if speaker != "guest" else "room_file", source_ref=ref,
                            room=room, ts=ts, day=mri._day(ts), speaker=speaker, name=name,
                            text=text))
    conn = mri.connect(path)
    try:
        mri.add_rows(conn, rows, vectors={})
    finally:
        conn.close()
    return path


@pytest.fixture
def ids(record) -> dict[str, int]:
    conn = mri.connect(record)
    try:
        return {ref: rid for ref, rid in conn.execute("SELECT source_ref, id FROM record")}
    finally:
        conn.close()


def _line(record: Path, ids: dict[str, int], ref: str, *, score: float = 0.1) -> Hit:
    """One fixture line as a weak hit (the quick look's leftovers)."""
    conn = mrs.open_record(record)
    try:
        (hit,) = mrs.neighbours(conn, ids[ref], scope=Scope(None), before=0, after=0)
    finally:
        conn.close()
    return dataclasses.replace(hit, score=score, words=score)


def _synthetic(n: int, *, chars: int = 300, exact: bool = False) -> list[Hit]:
    return [Hit(id=1000 + i, room=SAM_ROOM, ts=T0 + i, day="2026-10-09", speaker="guest",
                name="Sam", text=f"line {i} " + "x" * chars, score=1.0 if exact else 0.1,
                words=0.1, meaning=0.0, exact=exact) for i in range(n)]


def _texts(hits: list[Hit]) -> set[str]:
    return {h.text for h in hits}


# --- the two caps: rounds and calls ------------------------------------------

@pytest.mark.asyncio
async def test_a_widen_plan_that_finds_strong_hits_ends_after_one_round_and_two_calls(record):
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep="[Oct 9 19:01] Dana: nine")

    result = await look.second_look("when does the kite place open", [], scope=Scope(None),
                                    llm=llm, embed=FakeEmbed(), path=record)

    assert (result.rounds, result.calls) == (1, 2)
    assert llm.kinds == ["widen", "keep"]
    assert not result.thin and not result.timed_out
    assert {"the kite shop on Elm opens at nine on Saturdays",
            "the harbour kite shop reopens in April"} <= _texts(result.hits)
    assert result.text == "[Oct 9 19:01] Dana: nine"


@pytest.mark.asyncio
async def test_still_thin_after_two_rounds_is_three_calls_never_four(record, ids):
    llm = FakeLLM(widen=[{"rephrasings": ["zebra quartz"]}, {"rephrasings": ["violet anvil"]}],
                  keep="Nothing here answers it.")
    first = [_line(record, ids, "a1")]

    result = await look.second_look("what did Dana say about the zebra", first,
                                    scope=Scope(None), llm=llm, embed=FakeEmbed(), path=record)

    assert (result.rounds, result.calls) == (2, 3)
    assert llm.kinds == ["widen", "widen", "keep"]
    assert result.thin
    assert result.text == "Nothing here answers it."


@pytest.mark.asyncio
async def test_the_call_cap_holds_on_its_own_with_rounds_to_spare(record, ids, monkeypatch):
    """Rounds to spare (5): the widens still stop at two, so the keep is the
    third call and there is never a fourth."""
    monkeypatch.setattr(look, "MAX_ROUNDS", 5)
    llm = FakeLLM(widen=[{"rephrasings": ["zebra quartz"]}], keep="kept")

    result = await look.second_look("the zebra", [_line(record, ids, "a1")], scope=Scope(None),
                                    llm=llm, embed=FakeEmbed(), path=record)

    assert llm.kinds == ["widen", "widen", "keep"]
    assert (result.rounds, result.calls) == (2, 3)


@pytest.mark.asyncio
async def test_nothing_gathered_makes_no_keep_call_and_no_text(record):
    llm = FakeLLM(widen=[{"rephrasings": ["zebra quartz"]}])

    result = await look.second_look("the zebra", [], scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=record)

    assert (result.rounds, result.calls) == (2, 2)
    assert llm.kinds == ["widen", "widen"]
    assert result.hits == [] and result.text == "" and result.thin


@pytest.mark.asyncio
async def test_a_first_look_that_is_not_thin_goes_straight_to_the_keep(record):
    first = _synthetic(2, chars=20, exact=True)
    llm = FakeLLM(keep="kept")

    result = await look.second_look("anything", first, scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=record)

    assert (result.rounds, result.calls) == (0, 1)
    assert llm.kinds == ["keep"]
    assert result.text == "kept" and not result.thin


@pytest.mark.asyncio
async def test_asked_to_look_harder_it_widens_once_even_when_the_first_lines_are_not_thin(
        record, ids):
    """PaLLi's look_harder=true: the lines didn't settle it, which is her
    judgement, so one round widens whatever the first lines' strength; a
    second only while still thin."""
    first = [_line(record, ids, "a2", score=0.9), _line(record, ids, "a3", score=0.9)]
    assert not mrs.is_thin(first)
    a2 = ids["a2"]
    llm = FakeLLM(widen=[{"open_up": [f"#{a2}"]}], keep="kept")

    result = await look.second_look("the kite shop", first, scope=Scope(None), llm=llm,
                                    look_harder=True, embed=FakeEmbed(), path=record)

    assert (result.rounds, result.calls) == (1, 2)
    assert llm.kinds == ["widen", "keep"]
    assert {ids[f"a{i}"] for i in range(1, 6)} <= {h.id for h in result.hits}


@pytest.mark.asyncio
async def test_a_bad_plan_widens_nothing_and_the_rounds_still_stop_at_two(record, ids):
    llm = FakeLLM(widen=["I would look for the kite, maybe?"], keep="")
    first = [_line(record, ids, "a4")]

    result = await look.second_look("the kite", first, scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=record)

    assert (result.rounds, result.calls) == (2, 3)
    assert [h.id for h in result.hits] == [ids["a4"]]
    # The keep came back empty: the gathered lines themselves, as dated lines.
    assert "them: the green kite cost forty dollars" in result.text


# --- the ten seconds -----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_model_that_sleeps_twenty_seconds_is_cut_at_ten_with_the_partial_lines(
        record, ids):
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], sleep=20.0)
    first = [_line(record, ids, "a2", score=0.2)]

    started = time.monotonic()
    result = await look.second_look("when does the kite shop open", first, scope=Scope(None),
                                    llm=llm, embed=FakeEmbed(), path=record)
    wall = time.monotonic() - started

    assert result.timed_out
    assert result.seconds <= 10.5 and wall <= 10.5
    assert result.seconds >= 9.5
    assert (result.rounds, result.calls) == (1, 1)
    assert "Dana: the kite shop on Elm opens at nine on Saturdays" in result.text
    assert [h.id for h in result.hits] == [ids["a2"]]


@pytest.mark.asyncio
async def test_a_search_that_hangs_is_cut_by_the_ten_second_wall(record, ids, monkeypatch):
    """No model is slow here: only the wall around the whole look bounds a
    search of the record."""
    async def hung_search(*args, **kwargs):
        await asyncio.sleep(20.0)
        return []

    monkeypatch.setattr(mrs, "search", hung_search)
    first = [_line(record, ids, "a2", score=0.2)]
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep="kept")

    started = time.monotonic()
    result = await look.second_look("when does the kite shop open", first, scope=Scope(None),
                                    llm=llm, embed=FakeEmbed(), path=record)
    wall = time.monotonic() - started

    assert result.timed_out
    assert result.seconds <= 10.5 and wall <= 10.5
    assert (result.rounds, result.calls) == (1, 1)
    assert "Dana: the kite shop on Elm opens at nine on Saturdays" in result.text


@pytest.mark.asyncio
async def test_a_read_around_a_line_that_hangs_is_cut_by_the_ten_second_wall(
        record, ids, monkeypatch):
    """The lines around a hit are read in a thread; one that hangs is cut
    by the wall too (the thread is let go once the look has answered)."""
    real_neighbours = mrs.neighbours
    release = threading.Event()

    def hung_neighbours(conn, hit_id, **kwargs):
        release.wait(20.0)
        return real_neighbours(conn, hit_id, **kwargs)

    first = [_line(record, ids, "a2", score=0.2)]
    monkeypatch.setattr(mrs, "neighbours", hung_neighbours)
    llm = FakeLLM(widen=[{"open_up": [f"#{ids['a2']}"]}], keep="kept")

    started = time.monotonic()
    try:
        result = await look.second_look("when does the kite shop open", first,
                                        scope=Scope(None), llm=llm, embed=FakeEmbed(),
                                        path=record)
        wall = time.monotonic() - started
    finally:
        release.set()

    assert result.timed_out
    assert result.seconds <= 10.5 and wall <= 10.5
    assert (result.rounds, result.calls) == (1, 1)
    assert "Dana: the kite shop on Elm opens at nine on Saturdays" in result.text


@pytest.mark.asyncio
async def test_a_slow_keep_returns_what_the_widening_gathered(record, ids):
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep_sleep=20.0)
    first = [_line(record, ids, "a1")]

    started = time.monotonic()
    result = await look.second_look("the kite shop", first, scope=Scope(None), llm=llm,
                                    budget_s=1.0, embed=FakeEmbed(), path=record)

    assert time.monotonic() - started < 3.0
    assert result.timed_out and (result.rounds, result.calls) == (1, 2)
    assert "Sam: the harbour kite shop reopens in April" in result.text
    assert "Dana: the kite shop on Elm opens at nine on Saturdays" in result.text


@pytest.mark.asyncio
async def test_each_call_gets_only_the_time_that_remains(record, ids):
    """The look's own clock says 9.8 of the 10 s are gone before the first
    call: that call gets 0.2 s, not a fresh 10."""
    readings = iter([100.0])
    clock = lambda: next(readings, 109.8)       # noqa: E731 — a stand-in clock
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], sleep=5.0)

    started = time.monotonic()
    result = await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None),
                                    llm=llm, embed=FakeEmbed(), path=record, now=clock)

    assert time.monotonic() - started < 2.0
    assert result.timed_out
    assert result.seconds == pytest.approx(9.8)
    assert "them: the green kite cost forty dollars" in result.text


@pytest.mark.asyncio
async def test_with_no_time_left_no_call_is_made_or_counted(record, ids):
    readings = iter([100.0])
    clock = lambda: next(readings, 111.0)       # noqa: E731 — the budget is already gone
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}])

    result = await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None),
                                    llm=llm, embed=FakeEmbed(), path=record, now=clock)

    assert llm.pages == []
    assert (result.rounds, result.calls, result.timed_out) == (0, 0, True)
    assert "them: the green kite cost forty dollars" in result.text


@pytest.mark.asyncio
async def test_a_failing_model_or_search_never_raises_into_the_turn(record, ids, monkeypatch):
    async def broken_llm(page: str) -> str:
        raise RuntimeError("the provider is down")

    first = [_line(record, ids, "a4")]
    result = await look.second_look("the kite", first, scope=Scope(None), llm=broken_llm,
                                    embed=FakeEmbed(), path=record)
    assert not result.timed_out and result.calls == 3
    assert "them: the green kite cost forty dollars" in result.text

    async def broken_search(*args, **kwargs):
        raise sqlite3.OperationalError("database disk image is malformed")

    monkeypatch.setattr(mrs, "search", broken_search)
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep="")
    result = await look.second_look("the kite", first, scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=record)
    assert [h.id for h in result.hits] == [ids["a4"]]


@pytest.mark.asyncio
async def test_a_missing_record_gathers_nothing_and_does_not_create_it(tmp_path):
    missing = tmp_path / "none.sqlite3"
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"], "open_up": ["#5"], "follow": [5]}])

    result = await look.second_look("the kite", [], scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=missing)

    assert result.hits == [] and result.text == ""
    assert not missing.exists()


# --- the plan, parsed ---------------------------------------------------------

PLAN = {"rephrasings": ["kite shop hours", "when it opens"], "since": "2026-10-01",
        "until": "2026-10-12", "open_up": ["#41", "42"], "follow": [43]}


def test_parse_plan_reads_a_fenced_answer():
    raw = "```json\n" + json.dumps(PLAN, indent=2) + "\n```"
    assert look.parse_plan(raw) == {"rephrasings": ["kite shop hours", "when it opens"],
                                    "since": "2026-10-01", "until": "2026-10-12",
                                    "open_up": [41, 42], "follow": [43]}


def test_parse_plan_reads_the_object_out_of_prose():
    raw = ("Here is my plan, I think the words were different: " + json.dumps(PLAN)
           + " That should find it {if it was said}.")
    plan = look.parse_plan(raw)
    assert plan["rephrasings"] == ["kite shop hours", "when it opens"]
    assert plan["open_up"] == [41, 42] and plan["follow"] == [43]


@pytest.mark.parametrize("raw", ["", "no plan today", "{rephrasings: [kite}", "[41, 42]",
                                 '"just a string"', "{", None, 42, "```json\n```"])
def test_parse_plan_on_bad_json_is_empty(raw):
    assert look.parse_plan(raw) == {}


def test_parse_plan_caps_every_list():
    raw = json.dumps({"rephrasings": ["one", "two", "three"], "open_up": [1, 2, 3, 4, 5],
                      "follow": [6, 7, 8, 9]})
    plan = look.parse_plan(raw)
    assert plan["rephrasings"] == ["one", "two"]
    assert plan["open_up"] == [1, 2, 3]
    assert plan["follow"] == [6, 7]


def test_parse_plan_keeps_only_whole_line_ids():
    junk = [True, 4.5, "abc", -3, 0, "#", "# 7", "7a", 10 ** 30, "99999999999999999999",
            None, {"id": 1}, [2]]
    raw = json.dumps({"open_up": junk + ["#12", " 13 ", 14, 12], "follow": junk + [15]})
    plan = look.parse_plan(raw)
    assert plan["open_up"] == [12, 13, 14]
    assert plan["follow"] == [15]


@pytest.mark.parametrize("day, kept", [
    ("2026-10-01", "2026-10-01"), (" 2026-10-01 ", "2026-10-01"), ("2026-02-30", None),
    ("2026-13-01", None), ("13/10/2026", None), ("20261001", None), ("2026-W41-1", None),
    ("", None), ("empty", None), (None, None), (20261001, None), ("2026-10-01T09:00", None)])
def test_parse_plan_keeps_only_real_days(day, kept):
    plan = look.parse_plan(json.dumps({"since": day, "until": day}))
    assert plan["since"] == kept and plan["until"] == kept


def test_parse_plan_cleans_the_rephrasings():
    raw = json.dumps({"rephrasings": ["  ", 7, None, "Kite   shop", "kite shop", "x" * 900]})
    plan = look.parse_plan(raw)
    assert plan["rephrasings"][0] == "Kite shop"
    assert len(plan["rephrasings"]) == 2 and len(plan["rephrasings"][1]) <= 200
    assert look.parse_plan(json.dumps({"rephrasings": "kite shop"}))["rephrasings"] == ["kite shop"]


def test_parse_plan_always_gives_the_five_keys_for_an_object():
    assert look.parse_plan("{}") == {"rephrasings": [], "since": None, "until": None,
                                     "open_up": [], "follow": []}


# --- the scope ------------------------------------------------------------------

def _spy(monkeypatch) -> list[tuple[str, Scope]]:
    seen: list[tuple[str, Scope]] = []
    real_search, real_neighbours, real_thread = mrs.search, mrs.neighbours, mrs.thread

    async def search(*args, **kwargs):
        seen.append(("search", kwargs["scope"]))
        return await real_search(*args, **kwargs)

    def neighbours(conn, hit_id, **kwargs):
        seen.append(("neighbours", kwargs["scope"]))
        return real_neighbours(conn, hit_id, **kwargs)

    def thread(conn, hit_id, **kwargs):
        seen.append(("thread", kwargs["scope"]))
        return real_thread(conn, hit_id, **kwargs)

    monkeypatch.setattr(mrs, "search", search)
    monkeypatch.setattr(mrs, "neighbours", neighbours)
    monkeypatch.setattr(mrs, "thread", thread)
    return seen


@pytest.mark.asyncio
async def test_the_plans_ids_drive_neighbours_and_thread_under_the_callers_scope(
        record, ids, monkeypatch):
    first = [_line(record, ids, "b2")]
    seen = _spy(monkeypatch)
    scope = Scope(SAM_ROOM)
    b2 = ids["b2"]
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"], "open_up": [f"#{b2}"],
                          "follow": [str(b2)]}])

    result = await look.second_look("what did Sam pay for the engine", first, scope=scope,
                                    llm=llm, embed=FakeEmbed(), path=record)

    assert [kind for kind, _ in seen] == ["search", "neighbours", "thread"]
    assert all(s is scope for _, s in seen)
    assert {h.room for h in result.hits} == {SAM_ROOM}
    # b2's neighbours are the whole of Sam's room.
    assert {h.id for h in result.hits} == {ids[f"b{i}"] for i in range(1, 8)}


@pytest.mark.asyncio
async def test_a_room_scoped_look_never_gathers_another_rooms_lines(record, ids):
    """The plan names Dana's room's ids, and "kite shop" is said there too:
    a look scoped to Sam's room gathers nothing from Dana's."""
    dana = [ids["a2"], ids["a3"], ids["a4"]]
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"], "open_up": [f"#{i}" for i in dana],
                          "follow": dana[:2]}], keep="")

    result = await look.second_look("the kite shop", [_line(record, ids, "b2")],
                                    scope=Scope(SAM_ROOM), llm=llm, embed=FakeEmbed(),
                                    path=record)

    assert {h.room for h in result.hits} == {SAM_ROOM}
    assert "Dana" not in result.text and "Elm" not in result.text
    assert {ids["b5"], ids["b7"]} <= {h.id for h in result.hits}


@pytest.mark.asyncio
async def test_a_stray_line_from_another_room_in_the_first_lines_is_dropped(record, ids):
    """A line of Dana's room that strayed into the first lines of a look
    scoped to Sam's room is dropped before anything: it is never on a page,
    never opened around, never in the hits or the text."""
    stray = _line(record, ids, "a4")
    llm = FakeLLM(widen=[{"open_up": [f"#{stray.id}"], "follow": [stray.id]}], keep="")

    result = await look.second_look("the kite", [_line(record, ids, "b2"), stray],
                                    scope=Scope(SAM_ROOM), llm=llm, embed=FakeEmbed(),
                                    path=record)

    assert {h.room for h in result.hits} == {SAM_ROOM}
    assert stray.id not in {h.id for h in result.hits}
    assert all(stray.text not in page and f"#{stray.id} " not in page for page in llm.pages)
    assert stray.text not in result.text and "worth every dollar" not in result.text
    assert "Sam: the boat engine is fixed" in result.text


@pytest.mark.asyncio
async def test_first_lines_all_from_another_room_leave_nothing(record, ids):
    llm = FakeLLM(widen=[{}], keep="kept")

    result = await look.second_look("the kite", [_line(record, ids, "a2")],
                                    scope=Scope(SAM_ROOM), llm=llm, embed=FakeEmbed(),
                                    path=record)

    assert result.hits == [] and result.text == ""
    assert llm.kinds == ["widen", "widen"]


@pytest.mark.asyncio
async def test_an_id_never_shown_on_the_page_is_not_opened(record, ids, monkeypatch):
    first = [_line(record, ids, "b2")]
    seen = _spy(monkeypatch)
    llm = FakeLLM(widen=[{"open_up": [ids["b5"]], "follow": [ids["b7"]]}], keep="")

    result = await look.second_look("the boat", first, scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=record)

    assert [kind for kind, _ in seen] == []
    assert [h.id for h in result.hits] == [ids["b2"]]


@pytest.mark.asyncio
async def test_the_scope_must_be_a_scope():
    with pytest.raises(TypeError):
        await look.second_look("the kite", [], scope="discord:2", llm=FakeLLM())


# --- the dates -------------------------------------------------------------------

def _search_spy(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    real = mrs.search

    async def search(query, **kwargs):
        calls.append({"query": query, "since": kwargs.get("since"), "until": kwargs.get("until")})
        return await real(query, **kwargs)

    monkeypatch.setattr(mrs, "search", search)
    return calls


@pytest.mark.asyncio
async def test_the_first_searchs_dates_widen_by_thirty_days(record, ids, monkeypatch):
    calls = _search_spy(monkeypatch)
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}])

    await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None), llm=llm,
                           embed=FakeEmbed(), path=record, since="2026-10-10",
                           until="2026-10-12")

    # The question itself is searched again over the wider dates, then the rephrasing.
    assert calls == [
        {"query": "the kite", "since": "2026-09-10", "until": "2026-11-11"},
        {"query": "kite shop", "since": "2026-09-10", "until": "2026-11-11"},
    ]


@pytest.mark.asyncio
async def test_the_plans_own_dates_win_and_a_bad_one_falls_back(record, ids, monkeypatch):
    calls = _search_spy(monkeypatch)
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"], "since": "2026-08-01",
                          "until": "2026-02-30"}])

    await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None), llm=llm,
                           embed=FakeEmbed(), path=record, since="2026-10-10",
                           until="2026-10-12")

    assert {(c["since"], c["until"]) for c in calls} == {("2026-08-01", "2026-11-11")}


@pytest.mark.asyncio
async def test_no_dates_anywhere_searches_only_the_rephrasings_and_never_twice(
        record, ids, monkeypatch):
    calls = _search_spy(monkeypatch)
    llm = FakeLLM(widen=[{"rephrasings": ["the kite", "zebra quartz"]},
                         {"rephrasings": ["zebra quartz", "violet anvil"]}], keep="")

    await look.second_look("the kite", [_line(record, ids, "a1")], scope=Scope(None), llm=llm,
                           embed=FakeEmbed(), path=record)

    assert [c["query"] for c in calls] == ["zebra quartz", "violet anvil"]
    assert all(c["since"] is None and c["until"] is None for c in calls)


@pytest.mark.asyncio
async def test_the_first_searchs_dates_are_read_as_the_search_reads_them(
        record, ids, monkeypatch):
    """One parser for the first search's dates: whatever ``search`` takes
    as a day (here "20261001" and an ISO week day), the look widens."""
    calls = _search_spy(monkeypatch)
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}])
    week_day = date.fromisoformat("2026-W41-1")

    await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None), llm=llm,
                           embed=FakeEmbed(), path=record, since="20261001",
                           until="2026-W41-1")

    assert {(c["since"], c["until"]) for c in calls} == {
        ("2026-09-01", (week_day + timedelta(days=30)).isoformat())}


@pytest.mark.asyncio
async def test_a_first_search_date_no_search_could_read_is_no_date(record, ids, monkeypatch):
    calls = _search_spy(monkeypatch)
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}])

    result = await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None),
                                    llm=llm, embed=FakeEmbed(), path=record,
                                    since="next tuesday", until=date(2026, 10, 12))

    assert [(c["query"], c["since"], c["until"]) for c in calls] == [
        ("the kite", None, "2026-11-11"), ("kite shop", None, "2026-11-11")]
    assert result.hits


@pytest.mark.asyncio
async def test_dates_given_backwards_are_read_the_right_way_round(record, ids, monkeypatch):
    calls = _search_spy(monkeypatch)
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"], "since": "2026-10-12",
                          "until": "2026-10-01"}], keep="")

    await look.second_look("the kite", [_line(record, ids, "a1")], scope=Scope(None), llm=llm,
                           embed=FakeEmbed(), path=record)

    assert calls[0]["since"] == "2026-10-01" and calls[0]["until"] == "2026-10-12"


# --- what the pages show, and what comes back ---------------------------------------

@pytest.mark.asyncio
async def test_the_widen_page_names_line_ids_and_the_keep_page_does_not(record, ids):
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep="kept")
    a4 = ids["a4"]

    await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None), llm=llm,
                           embed=FakeEmbed(), path=record)

    widen, keep = llm.pages
    assert f"#{a4} [" in widen and "them: the green kite cost forty dollars" in widen
    assert "#" not in keep.split("THE QUESTION:")[1]
    assert "them: the green kite cost forty dollars" in keep
    assert "under 1200 characters" in keep


@pytest.mark.asyncio
async def test_an_empty_first_look_shows_the_bare_label(record):
    llm = FakeLLM(widen=[{}])

    await look.second_look("the zebra", [], scope=Scope(None), llm=llm, embed=FakeEmbed(),
                           path=record)

    assert llm.pages[0].rstrip().endswith('WHAT THE SEARCH FOUND SO FAR (dated lines; '
                                          '"me" is what I said):')


@pytest.mark.asyncio
async def test_keep_chars_holds_for_the_kept_lines(record, ids):
    lines = "\n".join(f"[Oct 9 19:{i:02d}] Dana: " + " ".join(["kite"] * 20) for i in range(60))
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep=lines)

    result = await look.second_look("the kite", [], scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=record)

    assert look.KEEP_CHARS == 1200
    assert 0 < len(result.text) <= look.KEEP_CHARS
    assert all(line in lines.splitlines() for line in result.text.splitlines())


@pytest.mark.asyncio
async def test_keep_chars_holds_for_one_long_kept_line(record):
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep="kite " * 1000)

    result = await look.second_look("the kite", [], scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=record)

    assert len(result.text) <= look.KEEP_CHARS and result.text.endswith("…")


@pytest.mark.asyncio
async def test_keep_chars_holds_for_the_lines_shown_when_nothing_was_kept(tmp_path):
    first = _synthetic(30, chars=300, exact=True)
    llm = FakeLLM(keep="")

    result = await look.second_look("anything", first, scope=Scope(None), llm=llm,
                                    embed=FakeEmbed(), path=tmp_path / "none.sqlite3")

    assert 0 < len(result.text) <= look.KEEP_CHARS
    assert result.text.count("Sam: line ") <= 12
    assert "Sam: line 0 " in result.text
    assert len(result.hits) == 30


@pytest.mark.asyncio
async def test_the_shown_lines_are_at_most_twelve(tmp_path):
    first = _synthetic(30, chars=5, exact=True)

    result = await look.second_look("anything", first, scope=Scope(None), llm=FakeLLM(keep=""),
                                    embed=FakeEmbed(), path=tmp_path / "none.sqlite3")

    assert result.text.count("Sam: line ") == 12


@pytest.mark.asyncio
async def test_every_page_stays_inside_its_size(tmp_path):
    first = _synthetic(400, chars=200, exact=True)
    llm = FakeLLM(keep="kept")

    await look.second_look("anything", first, scope=Scope(None), llm=llm, embed=FakeEmbed(),
                           path=tmp_path / "none.sqlite3")

    (keep,) = llm.pages
    gathered = keep.split("(dated lines; \"me\" is what I said):\n", 1)[1]
    assert len(gathered) <= look.PAGE_CHARS + 200
    assert "Sam: line 0 " in gathered


# --- the one log line -----------------------------------------------------------------

def _harness():
    if not (ROOT / "evals" / "memory_reach_replay" / "harness.py").is_file():
        pytest.skip("evals/ is not in this tree (the public copy ships without it)")
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "memory_reach_replay_harness_t10",
            ROOT / "evals" / "memory_reach_replay" / "harness.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.dont_write_bytecode = saved


@pytest.mark.asyncio
async def test_one_log_line_with_counts_only_that_the_replay_reads(record, ids, caplog):
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep="kept")
    question = "what did Dana say about the Elm opening"

    with caplog.at_level(logging.DEBUG, logger="ai.memory_reach_look"):
        result = await look.second_look(question, [_line(record, ids, "a4")],
                                        scope=Scope(None), llm=llm, embed=FakeEmbed(),
                                        path=record)

    reach = [r.getMessage() for r in caplog.records
             if r.name == "ai.memory_reach_look" and r.getMessage().startswith("memory reach:")]
    assert len(reach) == 1
    (line,) = reach
    assert line.startswith("memory reach: second look rounds=")
    for word in ("Dana", "Elm", "kite", "opening", "forty"):
        assert word not in line
    parsed = _harness().parse_reach_line(line)
    assert parsed == {"kind": "second_look", "rounds": result.rounds, "calls": result.calls,
                      "seconds": pytest.approx(result.seconds, abs=0.01),
                      "thin": result.thin, "timed_out": False}
    assert isinstance(parsed["seconds"], float)


@pytest.mark.asyncio
async def test_a_failed_look_still_logs_one_reach_line(record, ids, caplog, monkeypatch):
    async def broken_search(*args, **kwargs):
        raise sqlite3.OperationalError("database disk image is malformed")

    monkeypatch.setattr(mrs, "search", broken_search)
    llm = FakeLLM(widen=[{"rephrasings": ["kite shop"]}], keep="")

    with caplog.at_level(logging.DEBUG, logger="ai.memory_reach_look"):
        await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None),
                               llm=llm, embed=FakeEmbed(), path=record)

    reach = [r.getMessage() for r in caplog.records if r.getMessage().startswith("memory reach:")]
    assert len(reach) == 1 and reach[0].startswith("memory reach: second look ")
    assert all("malformed" not in r.getMessage() for r in caplog.records)


# --- the lanes' callable ------------------------------------------------------------------

@pytest.mark.parametrize("lane, max_tokens", [(look.WIDEN_LANE, 600), (look.KEEP_LANE, 900)])
def test_build_llm_makes_one_call_with_the_lanes_shape(lane, max_tokens, monkeypatch):
    import ai.llm_client as llm_client

    sent: list[dict] = []

    async def chat_completion(**kwargs):
        sent.append(kwargs)
        return None

    monkeypatch.setattr(llm_client.llm, "chat_completion", chat_completion)
    monkeypatch.delenv("PALLI_CADENCE_MODEL", raising=False)
    monkeypatch.delenv("CADENCE_MODEL", raising=False)
    monkeypatch.setattr("core.config.config.CADENCE_MODEL", "", raising=False)

    assert asyncio.run(look.build_llm(lane)("<the page>")) == ""
    assert sent == [{"messages": [{"role": "user", "content": "<the page>"}],
                     "temperature": 0.2, "max_tokens": max_tokens, "log_context": lane}]


def test_build_llm_takes_the_quick_reads_model(monkeypatch):
    import ai.llm_client as llm_client

    sent: list[dict] = []

    async def chat_completion(**kwargs):
        sent.append(kwargs)
        return "{}"

    monkeypatch.setattr(llm_client.llm, "chat_completion", chat_completion)
    monkeypatch.setattr("core.config.config.CADENCE_MODEL", "deepseek::fast", raising=False)
    assert asyncio.run(look.build_llm(look.KEEP_LANE)("p")) == "{}"
    assert sent[0]["model"] == "deepseek::fast"


def test_build_llm_knows_only_the_two_lanes():
    with pytest.raises(ValueError):
        look.build_llm("first_look")


@pytest.mark.asyncio
async def test_with_no_llm_given_each_page_goes_to_its_own_lane(record, ids, monkeypatch):
    lanes: list[str] = []

    def build(lane):
        async def call(page: str) -> str:
            lanes.append(lane)
            return json.dumps({"rephrasings": ["kite shop"]}) if lane == look.WIDEN_LANE else "k"
        return call

    monkeypatch.setattr(look, "build_llm", build)
    result = await look.second_look("the kite", [_line(record, ids, "a4")], scope=Scope(None),
                                    embed=FakeEmbed(), path=record)

    assert lanes == [look.WIDEN_LANE, look.KEEP_LANE] and result.text == "k"


def test_the_caps_are_the_plans():
    assert (look.MAX_ROUNDS, look.MAX_CALLS, look.BUDGET_SECONDS, look.KEEP_CHARS) == (2, 3, 10.0,
                                                                                       1200)
