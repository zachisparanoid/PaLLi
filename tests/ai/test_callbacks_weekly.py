"""The shared language is harvested weekly (Batch 6, task 2; item 3.3).

A bit becomes theirs when it is thrown back, and that is a pattern across
days: judged seconds after each message, from one exchange, not one bit
was ever found. Finding NEW bits moved to a weekly harvest over seven days
of the two of them talking. The model names the bit and quotes the line;
code owns the rest: the line must be in one row of the week, the bit must
be in the line, who and when come from that row, at most two births a
week, once per week.

Their "that's not ours" still retires a bit in the turn; that door is
pinned in tests/ai/test_callbacks_reflect_ops.py.

Every test runs against a tmp database and a tmp book, with real rows
written through the real conversation store.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

import ai.callbacks_store as cs
from ai import callbacks_harvest as harvest
from core.database import PalliBrain

ROOT = Path(__file__).resolve().parents[2]

# Monday 2026-09-28, a little after 04:00: the first night of ISO week 40.
# The seven days looked back on are Monday the 21st to Sunday the 27th.
NOW = datetime(2026, 9, 28, 4, 20).timestamp()
WEEK = "2026-W40"
MONDAY = datetime(2026, 9, 21)

THEIRS = "fine, we take the long way round again"
HERS = "the long way round it is. I am keeping that one."
BIT = "the long way round"


def _born(bit=BIT, line=THEIRS, kind="bit", coined_by="both", **extra) -> dict:
    return {"bit": bit, "kind": kind, "coined_by": coined_by, "line": line, **extra}


def _answer(*births) -> str:
    return json.dumps({"born": list(births)})


def _llm(reply: str, seen: list | None = None):
    async def call(prompt: str) -> str:
        if seen is not None:
            seen.append(prompt)
        return reply
    return call


async def _must_not_call(prompt: str) -> str:
    raise AssertionError("the model was asked")


@pytest_asyncio.fixture
async def brain(tmp_path):
    db = PalliBrain(str(tmp_path / "week.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path / "callbacks")
    return tmp_path / "callbacks"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(cs, "idiolect_enabled", lambda: True)


async def _say(brain, when: datetime, role: str, content: str, *,
               platform: str = "discord", user_id: int = 1) -> int:
    """One real row, written through the real store, then dated."""
    from core.conversation_history_store import ConversationHistoryStore

    row_id = await ConversationHistoryStore(brain).store_conversation(
        user_id=user_id, platform=platform, channel_id=5, thread_id=None,
        role=role, content=content)
    conn = await brain.get_connection()
    try:
        await conn.execute("UPDATE conversations SET timestamp = $1 WHERE id = $2",
                           when.timestamp(), row_id)
    finally:
        await conn.close()
    return row_id


async def _the_bit_is_thrown_back(brain) -> tuple[int, int]:
    """She says it on Tuesday; they throw it back on Thursday."""
    hers = await _say(brain, MONDAY.replace(day=22, hour=10, minute=5), "assistant", HERS)
    theirs = await _say(brain, MONDAY.replace(day=24, hour=19, minute=40), "user", THEIRS)
    return hers, theirs


async def _harvest(brain, reply=None, *, llm_call=None, now=NOW, seen=None, owner_id=1):
    return await harvest.run_weekly_callbacks_harvest(
        owner_id, llm_call=llm_call or _llm(reply, seen), now=now, brain_client=brain)


def _state(book: Path) -> dict:
    return json.loads((book / ".state.json").read_text(encoding="utf-8"))


def _deeds() -> list:
    from ai.recent_deeds import read_recent_deeds

    return [(d["kind"], d["detail"]) for d in read_recent_deeds(limit=20)]


# ── the brief's test: seven days, not one turn ──────────────────────────

@pytest.mark.asyncio
async def test_the_harvest_reads_seven_days_not_one_turn(brain, book, on):
    for offset in range(7):
        day = MONDAY + timedelta(days=offset)
        await _say(brain, day.replace(hour=9), "user", f"a line of theirs on day {offset}")
        await _say(brain, day.replace(hour=9, minute=1), "assistant",
                   f"a line of mine on day {offset}")
    await _say(brain, MONDAY - timedelta(days=1, hours=-9), "user", "the week before")
    await _say(brain, MONDAY + timedelta(days=7, hours=1), "user", "after midnight tonight")
    seen: list = []
    await _harvest(brain, _answer(), seen=seen)
    assert len(seen) == 1
    prompt = seen[0]
    for offset in range(7):
        assert f"09:00  them: a line of theirs on day {offset}" in prompt
        assert f"09:01  me: a line of mine on day {offset}" in prompt
    assert "the week before" not in prompt
    assert "after midnight tonight" not in prompt
    assert "Monday, 2026-09-21" in prompt and "Sunday, 2026-09-27" in prompt
    assert "2026-09-21 to 2026-09-27" in prompt
    assert "<<" not in prompt and ">>" not in prompt


@pytest.mark.asyncio
async def test_the_week_is_asked_for_one_day_at_a_time(brain, book, on, monkeypatch):
    asked: list = []
    real = brain.get_conversations_for_day

    async def spy(**kw):
        asked.append((kw["day_start_ts"], kw["day_end_ts"]))
        return await real(**kw)

    monkeypatch.setattr(brain, "get_conversations_for_day", spy)
    await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer())
    assert asked == [
        ((MONDAY + timedelta(days=n)).timestamp(), (MONDAY + timedelta(days=n + 1)).timestamp())
        for n in range(7)
    ]


# ── the window is the week before the one claimed (fix round 1, R66) ────

def _bounds_of(first_day: datetime) -> list:
    """The seven local day-bounds, Monday 00:00 to Sunday 24:00, from
    ``first_day`` (a Monday at midnight)."""
    return [((first_day + timedelta(days=n)).timestamp(),
             (first_day + timedelta(days=n + 1)).timestamp()) for n in range(7)]


async def _asked_for(brain, monkeypatch, now: float, reply=None, *, llm_call=None) -> list:
    """The day-bounds one harvest at ``now`` asked the store for."""
    asked: list = []
    real = brain.get_conversations_for_day

    async def spy(**kw):
        asked.append((kw["day_start_ts"], kw["day_end_ts"]))
        return await real(**kw)

    monkeypatch.setattr(brain, "get_conversations_for_day", spy)
    await _harvest(brain, reply, llm_call=llm_call, now=now)
    monkeypatch.setattr(brain, "get_conversations_for_day", real)
    return asked


@pytest.mark.asyncio
async def test_a_retry_later_in_the_week_reads_the_same_seven_days(brain, book, on, monkeypatch):
    """The week is claimed only when a harvest completes, so a failed call
    on Monday is tried again on a later night of the same week. That night
    reads the SAME seven days: the ISO week before the one it claims. A
    window that slid with the night would never read the days between the
    last week read and the new one."""
    async def boom(prompt):
        raise RuntimeError("the provider is down")

    await _the_bit_is_thrown_back(brain)
    on_monday = await _asked_for(brain, monkeypatch, NOW, llm_call=boom)
    assert not (book / ".state.json").exists()                  # the week is still open
    thursday = NOW + 3 * 86400
    on_thursday = await _asked_for(brain, monkeypatch, thursday, _answer())
    assert on_monday == on_thursday == _bounds_of(MONDAY)
    assert _state(book)["last_week"] == WEEK


@pytest.mark.asyncio
async def test_a_retry_still_finds_a_line_said_on_the_first_day_of_the_week(brain, book, on):
    """Said on Monday the 21st, the first day of the week looked back on; the
    harvest that finally runs on Thursday the 1st of October still reads it,
    and reads nothing of the week it claims."""
    theirs = await _say(brain, MONDAY.replace(hour=8), "user", THEIRS)
    await _say(brain, datetime(2026, 9, 29, 12, 0), "user", "a line from the week being claimed")
    seen: list = []
    out = await _harvest(brain, _answer(_born()), now=NOW + 3 * 86400, seen=seen)
    assert out["week"] == WEEK and len(out["planted"]) == 1
    assert cs.open_callbacks()[0].receipt["source"] == f"conversation:{theirs}"
    assert "a line from the week being claimed" not in seen[0]
    assert "2026-09-21 to 2026-09-27" in seen[0]


@pytest.mark.parametrize("night,claimed,first_day", [
    # 2026 has 53 ISO weeks. Its week 53 is Monday 28 December to Sunday
    # 3 January; 2027's week 1 begins Monday 4 January.
    (datetime(2027, 1, 4, 4, 20), "2027-W01", datetime(2026, 12, 28)),   # Monday of week 1
    (datetime(2027, 1, 7, 4, 20), "2027-W01", datetime(2026, 12, 28)),   # Thursday of week 1
    (datetime(2027, 1, 10, 23, 50), "2027-W01", datetime(2026, 12, 28)),  # its last night
    (datetime(2026, 12, 28, 4, 20), "2026-W53", datetime(2026, 12, 21)),  # Monday of week 53
    (datetime(2026, 12, 31, 4, 20), "2026-W53", datetime(2026, 12, 21)),  # Thursday of week 53
    (datetime(2027, 1, 2, 4, 20), "2026-W53", datetime(2026, 12, 21)),   # a Saturday in the new year
])
def test_the_week_looked_back_on_turns_at_the_years_end_too(night, claimed, first_day):
    now = night.timestamp()
    assert harvest.week_key_for(now) == claimed
    assert harvest._window_days(now) == [
        (first_day + timedelta(days=n)).strftime("%Y-%m-%d") for n in range(7)]


@pytest.mark.asyncio
async def test_the_harvest_at_the_years_end_asks_for_the_right_days(brain, book, on, monkeypatch):
    """Claimed on the Thursday of 2027's week 1, the harvest reads 2026's
    week 53: Monday 28 December to Sunday 3 January, whole local days."""
    await _say(brain, datetime(2026, 12, 29, 10), "user", THEIRS)
    asked = await _asked_for(brain, monkeypatch, datetime(2027, 1, 7, 4, 20).timestamp(),
                             _answer())
    assert asked == _bounds_of(datetime(2026, 12, 28))
    assert _state(book)["last_week"] == "2027-W01"


# ── the rails (ruling R28) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_bit_thrown_back_is_planted_with_the_rows_own_receipt(brain, book, on):
    _hers, theirs = await _the_bit_is_thrown_back(brain)
    out = await _harvest(brain, _answer(_born(line="we take the long way round again")))
    rows = cs.open_callbacks()
    assert len(rows) == 1 and rows[0].bit == BIT
    receipt = rows[0].receipt
    assert receipt["who"] == "him"                       # the row is theirs
    said = datetime.fromisoformat(receipt["when"])
    assert said.tzinfo is not None
    assert said.timestamp() == MONDAY.replace(day=24, hour=19, minute=40).timestamp()
    assert receipt["line"] == "we take the long way round again"
    assert receipt["source"] == f"conversation:{theirs}"
    assert out == {"week": WEEK, "planted": [rows[0].id]}
    assert ("callback_born", f'a bit of ours was born: "{BIT}"') in _deeds()


@pytest.mark.asyncio
async def test_a_line_of_hers_is_stamped_as_hers(brain, book, on):
    hers, _theirs = await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer(_born(line="the long way round it is")))
    receipt = cs.open_callbacks()[0].receipt
    assert receipt["who"] == "her" and receipt["source"] == f"conversation:{hers}"


@pytest.mark.asyncio
async def test_the_day_on_the_receipt_is_the_day_it_was_said(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer(_born()))
    assert cs._born_day(cs.open_callbacks()[0].receipt["when"]) == "Sep 24"


@pytest.mark.asyncio
async def test_a_line_that_is_in_no_row_of_the_week_writes_nothing(brain, book, on, caplog):
    await _the_bit_is_thrown_back(brain)
    with caplog.at_level(logging.INFO):
        out = await _harvest(brain, _answer(
            _born(line="you called it the long way round back in the spring")))
    assert cs.open_callbacks() == [] and _deeds() == []
    assert out == {"week": WEEK, "planted": [], "refused": 1}
    assert "callbacks_harvest: birth refused: line not in the week" in caplog.text


@pytest.mark.asyncio
async def test_a_line_made_of_two_rows_is_in_no_row(brain, book, on):
    """The line must be in ONE row: the end of one message and the start of
    the next are not a line anybody said."""
    await _say(brain, MONDAY.replace(hour=9), "user", "we could take the long way")
    await _say(brain, MONDAY.replace(hour=9, minute=1), "assistant", "round the back, then")
    out = await _harvest(brain, _answer(_born(bit="long way round",
                                              line="the long way round the back")))
    assert cs.open_callbacks() == [] and out["refused"] == 1


@pytest.mark.asyncio
async def test_a_bit_that_is_not_in_its_line_writes_nothing(brain, book, on, caplog):
    await _the_bit_is_thrown_back(brain)
    with caplog.at_level(logging.INFO):
        out = await _harvest(brain, _answer(
            _born(bit="the scenic protocol", line="I am keeping that one")))   # a real quote
    assert cs.open_callbacks() == [] and out["refused"] == 1
    assert "callbacks_harvest: birth refused: bit not in its line" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("line", ["ok", "!!", "a"])
async def test_a_line_too_short_to_be_a_line_writes_nothing(brain, book, on, line):
    await _say(brain, MONDAY.replace(hour=9), "user", "ok a !! fine")
    out = await _harvest(brain, _answer(_born(bit=line, line=line)))
    assert cs.open_callbacks() == [] and out["refused"] == 1


@pytest.mark.asyncio
async def test_a_bit_that_is_only_marks_writes_nothing(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    out = await _harvest(brain, _answer(_born(bit="!!!", line=THEIRS)))
    assert cs.open_callbacks() == [] and out["refused"] == 1


@pytest.mark.asyncio
async def test_the_model_can_never_stamp_who_when_or_source(brain, book, on):
    _hers, theirs = await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer(_born(
        who="her", when="1999-01-01T00:00:00+00:00", source="a dream",
        receipt={"who": "her", "when": "1999-01-01T00:00:00+00:00"})))
    receipt = cs.open_callbacks()[0].receipt
    assert receipt["who"] == "him"
    assert receipt["when"].startswith("2026-09-2")
    assert receipt["source"] == f"conversation:{theirs}"


@pytest.mark.asyncio
async def test_the_first_row_that_carries_the_line_is_the_receipt(brain, book, on):
    first = await _say(brain, MONDAY.replace(day=22, hour=8), "assistant",
                       "we take the long way round, then")
    await _say(brain, MONDAY.replace(day=25, hour=8), "user",
               "ha. we take the long way round every time")
    await _harvest(brain, _answer(_born(line="we take the long way round")))
    receipt = cs.open_callbacks()[0].receipt
    assert receipt["who"] == "her" and receipt["source"] == f"conversation:{first}"


@pytest.mark.asyncio
async def test_a_line_copied_with_its_clock_and_speaker_is_read_without_them(brain, book, on):
    """The week is shown as "HH:MM  them: ..." lines, so a quote may come
    back with that label still on it."""
    await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer(_born(line="19:40  them: " + THEIRS)))
    rows = cs.open_callbacks()
    assert len(rows) == 1 and rows[0].receipt["line"] == THEIRS


@pytest.mark.asyncio
async def test_marks_and_capitals_do_not_hide_a_line(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer(_born(line="Fine - we take the LONG WAY ROUND, again!")))
    assert len(cs.open_callbacks()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("said,filed", [
    ("them", "him"), ("me", "her"), ("both", "both"),      # the words she is asked in
    ("him", "him"), ("her", "her"),                        # the book's own words
    (" Them ", "him"),
])
async def test_whose_it_is_is_asked_in_her_words_and_filed_in_the_books(
        brain, book, on, said, filed):
    await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer(_born(coined_by=said)))
    assert [c.coined_by for c in cs.open_callbacks()] == [filed]
    assert set(harvest._COINER.values()) == set(cs.COINERS)


# ── what counts as the two of them talking ──────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["proactive", "reminder_fire", "local", ""])
async def test_a_row_that_is_not_a_conversation_is_never_read(brain, book, on, platform):
    await _say(brain, MONDAY.replace(hour=9), "user", "hello there, a plain line")
    await _say(brain, MONDAY.replace(hour=10), "user", THEIRS, platform=platform)
    seen: list = []
    out = await _harvest(brain, _answer(_born()), seen=seen)
    assert THEIRS not in seen[0]
    assert cs.open_callbacks() == [] and out["refused"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["discord", "telegram", "voice", "dashboard"])
async def test_every_door_where_they_talk_is_read(brain, book, on, platform):
    await _say(brain, MONDAY.replace(hour=10), "user", THEIRS, platform=platform)
    await _harvest(brain, _answer(_born()))
    assert len(cs.open_callbacks()) == 1


def test_the_doors_come_from_the_organs_own_list():
    """One list for the house: the platforms are the callbacks organ's
    conversational surfaces, read through the surface-to-platform mapping
    the conversation rows were written with."""
    from core.turn.recall import platform_for_surface

    assert harvest._conversational_platforms() == frozenset(
        platform_for_surface(s) for s in cs.CONVERSATIONAL_SURFACES)
    assert harvest._conversational_platforms() == frozenset(
        {"discord", "telegram", "voice", "dashboard", "mobile"})


@pytest.mark.asyncio
async def test_somebody_elses_rows_are_never_read(brain, book, on):
    await _say(brain, MONDAY.replace(hour=9), "user", "hello there, a plain line")
    await _say(brain, MONDAY.replace(hour=10), "user", THEIRS, user_id=2)
    seen: list = []
    out = await _harvest(brain, _answer(_born()), seen=seen)
    assert THEIRS not in seen[0]
    assert cs.open_callbacks() == [] and out["refused"] == 1


@pytest.mark.asyncio
async def test_a_row_that_is_neither_of_them_is_never_read(brain, book, on):
    await _say(brain, MONDAY.replace(hour=9), "user", "hello there, a plain line")
    await _say(brain, MONDAY.replace(hour=10), "system", THEIRS)
    out = await _harvest(brain, _answer(_born()))
    assert cs.open_callbacks() == [] and out["refused"] == 1


# ── the budgets: two a week, once a week (rulings R28, R29) ─────────────

@pytest.mark.asyncio
async def test_a_third_birth_in_one_week_waits(brain, book, on):
    await _say(brain, MONDAY.replace(hour=9), "user",
               "the long way round, tiny victory lap, soup o'clock")
    out = await _harvest(brain, _answer(
        _born(bit="the long way round", line="the long way round, tiny victory lap"),
        _born(bit="tiny victory lap", line="tiny victory lap, soup o'clock"),
        _born(bit="soup o'clock", line="tiny victory lap, soup o'clock"),
    ))
    assert [c.bit for c in cs.open_callbacks()] == ["the long way round", "tiny victory lap"]
    assert len(out["planted"]) == 2 and out["over_the_cap"] == 1
    assert _state(book)["planted"] == 2


@pytest.mark.asyncio
async def test_a_refused_birth_does_not_spend_the_weeks_budget(brain, book, on):
    await _say(brain, MONDAY.replace(hour=9), "user",
               "the long way round, tiny victory lap, soup o'clock")
    out = await _harvest(brain, _answer(
        _born(bit="made up", line="a line nobody said this week"),
        _born(bit="the long way round", line="the long way round, tiny victory lap"),
        _born(bit="tiny victory lap", line="tiny victory lap, soup o'clock"),
    ))
    assert len(cs.open_callbacks()) == 2
    assert len(out["planted"]) == 2 and out["refused"] == 1 and "over_the_cap" not in out


@pytest.mark.asyncio
async def test_the_harvest_leaves_its_trace_planted_or_not(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer())
    assert _state(book) == {"last_week": WEEK, "planted": 0, "at": NOW}
    assert b"\r" not in (book / ".state.json").read_bytes()


@pytest.mark.asyncio
async def test_a_week_already_harvested_is_not_asked_again(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer(_born()))
    before = _state(book)
    out = await _harvest(brain, llm_call=_must_not_call, now=NOW + 3 * 86400)   # Thursday
    assert out == {"skipped": "already_ran", "week": WEEK}
    assert _state(book) == before and len(cs.open_callbacks()) == 1


@pytest.mark.asyncio
async def test_the_next_week_runs(brain, book, on):
    await _harvest(brain, _answer())
    await _say(brain, MONDAY + timedelta(days=9, hours=10), "user", THEIRS)
    out = await _harvest(brain, _answer(_born()), now=NOW + 7 * 86400)
    assert out["week"] == "2026-W41" and len(out["planted"]) == 1
    assert _state(book)["last_week"] == "2026-W41"


@pytest.mark.asyncio
async def test_a_week_with_no_conversation_asks_nothing(brain, book, on):
    out = await _harvest(brain, llm_call=_must_not_call)
    assert out == {"week": WEEK, "planted": [], "skipped": "no_conversation"}
    assert _state(book) == {"last_week": WEEK, "planted": 0, "at": NOW}


# ── the book she already has ────────────────────────────────────────────

def _receipt(line=THEIRS) -> dict:
    return {"who": "him", "when": datetime(2026, 9, 1, 12, tzinfo=timezone.utc).isoformat(),
            "line": line, "source": "test"}


@pytest.mark.asyncio
async def test_a_bit_already_in_the_book_is_not_planted_twice(brain, book, on, caplog):
    cs.plant(bit=BIT, kind="bit", coined_by="both", receipt=_receipt())
    await _the_bit_is_thrown_back(brain)
    with caplog.at_level(logging.INFO):
        out = await _harvest(brain, _answer(_born(bit="The Long Way Round!")))
    assert len(cs.open_callbacks()) == 1
    assert out["planted"] == [] and out["refused"] == 1
    assert "callbacks_harvest: birth refused: already in the book" in caplog.text
    assert _deeds() == []


@pytest.mark.asyncio
async def test_a_bit_they_retired_never_comes_back_by_itself(brain, book, on, caplog):
    """Their "that's not ours" is the only say they have in this book. The
    store would take the bit again (it only knows open ones), so the
    harvest refuses it."""
    old = cs.plant(bit=BIT, kind="bit", coined_by="both", receipt=_receipt())
    cs.retire(old.id, reason="that is not ours")
    await _the_bit_is_thrown_back(brain)
    with caplog.at_level(logging.INFO):
        out = await _harvest(brain, _answer(_born()))
    assert cs.open_callbacks() == []
    assert out["planted"] == [] and out["refused"] == 1
    assert "callbacks_harvest: birth refused: retired by their word" in caplog.text


@pytest.mark.asyncio
async def test_the_prompt_shows_the_book_open_and_retired(brain, book, on):
    cs.plant(bit="tiny victory lap", kind="bit", coined_by="her",
             receipt=_receipt("time for a tiny victory lap"))
    old = cs.plant(bit="soup o'clock", kind="shorthand", coined_by="him",
                   receipt=_receipt("it is soup o'clock"))
    cs.retire(old.id, reason="that is not ours")
    await _the_bit_is_thrown_back(brain)
    seen: list = []
    await _harvest(brain, _answer(), seen=seen)
    shown = seen[0]
    assert '- "tiny victory lap"' in shown
    retired = next(l for l in shown.splitlines() if "soup o'clock" in l)
    assert "they said it is not yours together" in retired
    # the receipt stays in the book: no id, no quoted line, no source
    assert old.id not in shown and "time for a tiny victory lap" not in shown


@pytest.mark.asyncio
async def test_an_empty_book_says_so_in_plain_words(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    seen: list = []
    await _harvest(brain, _answer(), seen=seen)
    assert "(nothing yet)" in seen[0]


@pytest.mark.asyncio
async def test_the_trace_beside_the_book_does_not_disturb_its_readers(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    await _harvest(brain, _answer(_born()))
    assert sorted(p.name for p in book.iterdir()) == [".state.json", "callbacks.jsonl"]
    assert [c.bit for c in cs.load_callbacks().values()] == [BIT]
    assert [c.bit for c in cs.recent_callbacks()] == [BIT]
    assert cs.find_open_by_bit(BIT) is not None
    assert BIT in cs.render_callbacks_band()


def test_the_trace_is_kept_beside_the_book_wherever_the_book_is(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path / "a")
    assert harvest._state_path(None) == tmp_path / "a" / ".state.json"
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path / "b"))       # the subprocess seam
    assert harvest._state_path(None) == tmp_path / "b" / ".state.json"
    assert harvest._state_path(tmp_path / "c") == tmp_path / "c" / ".state.json"


def test_the_suite_never_points_at_her_real_book():
    assert not harvest._state_path(None).resolve().is_relative_to((ROOT / "data").resolve())


# ── the flag (ruling R29) ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_harvest_is_dark_while_the_flag_is_off(brain, book, monkeypatch):
    async def must_not_read(**kw):
        raise AssertionError("the week was read with the organ off")

    monkeypatch.setattr(brain, "get_conversations_for_day", must_not_read)
    out = await _harvest(brain, llm_call=_must_not_call)
    assert out == {"skipped": "flag_off"}
    assert not book.exists()


@pytest.mark.asyncio
async def test_the_flag_is_the_callbacks_organs_own_and_no_other(brain, book, on, monkeypatch):
    """Not the story pages' flag, not their first writing: a week in which
    no page was revised is still a week the two of them talked."""
    import ai.narrative_self as ns

    monkeypatch.setattr(ns, "narrative_self_enabled", lambda: False)
    monkeypatch.setattr(ns, "first_authorship_done", lambda *a, **kw: False)
    await _the_bit_is_thrown_back(brain)
    out = await _harvest(brain, _answer(_born()))
    assert len(out["planted"]) == 1


# ── answers outside the contract, and failures ──────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "", "nothing this week", '{"ops": []}', '{"born": null}', '{"born": {"bit": "x"}}',
    '{"born": "none"}', '{"callbacks_born": []}',
])
async def test_an_answer_outside_the_contract_writes_nothing(brain, book, on, reply):
    await _the_bit_is_thrown_back(brain)
    out = await _harvest(brain, reply)
    assert out == {"week": WEEK, "error": "unparseable"}
    assert not (book / ".state.json").exists()
    assert cs.open_callbacks() == []


@pytest.mark.asyncio
async def test_a_birth_that_is_not_well_formed_is_dropped_and_counted(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    out = await _harvest(brain, _answer(
        "not a birth",
        {"bit": "", "kind": "bit", "coined_by": "both", "line": THEIRS},            # no bit
        {"bit": BIT, "kind": "pun", "coined_by": "both", "line": THEIRS},            # bad kind
        {"bit": BIT, "kind": "bit", "coined_by": "nobody", "line": THEIRS},          # bad coiner
        {"bit": BIT, "kind": "bit", "coined_by": "both"},                            # no line
        {"bit": {"a": 1}, "kind": "bit", "coined_by": "both", "line": THEIRS},       # not text
        {"bit": BIT, "kind": "bit", "coined_by": "both", "line": ["a", "b"]},        # not text
    ))
    assert cs.open_callbacks() == []
    assert out == {"week": WEEK, "planted": [], "refused": 7}
    assert _state(book)["planted"] == 0


@pytest.mark.asyncio
async def test_a_runaway_list_is_read_only_so_far(brain, book, on):
    await _the_bit_is_thrown_back(brain)
    births = [_born(bit=f"made up {n}", line=f"a line nobody said {n}") for n in range(40)]
    out = await _harvest(brain, _answer(*births, _born()))
    assert cs.open_callbacks() == []
    assert out["refused"] == harvest.MAX_PROPOSALS_READ


@pytest.mark.asyncio
async def test_a_failed_call_never_raises_and_is_never_an_empty_error(brain, book, on):
    await _the_bit_is_thrown_back(brain)

    async def boom(prompt):
        raise RuntimeError("the provider is down")

    async def silent(prompt):
        raise asyncio.TimeoutError()

    assert await _harvest(brain, llm_call=boom) == {"week": WEEK, "error": "the provider is down"}
    assert await _harvest(brain, llm_call=silent) == {"week": WEEK, "error": "TimeoutError"}
    assert not (book / ".state.json").exists()


@pytest.mark.asyncio
async def test_a_week_that_cannot_be_read_is_an_error_not_a_quiet_week(brain, book, on, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(brain, "get_conversations_for_day", boom)
    out = await _harvest(brain, llm_call=_must_not_call)
    assert out == {"week": WEEK, "error": "the database is locked"}
    assert not (book / ".state.json").exists()


@pytest.mark.asyncio
async def test_a_cancel_goes_through(brain, book, on):
    await _the_bit_is_thrown_back(brain)

    async def cancelled(prompt):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _harvest(brain, llm_call=cancelled)


@pytest.mark.asyncio
async def test_one_plant_that_raises_does_not_cost_the_next(brain, book, on, monkeypatch):
    await _say(brain, MONDAY.replace(hour=9), "user", "the long way round, tiny victory lap")
    real = cs.plant
    seen: list = []

    def flaky(**kw):
        seen.append(kw["bit"])
        if len(seen) == 1:
            raise OSError("disk full")
        return real(**kw)

    monkeypatch.setattr(cs, "plant", flaky)
    out = await _harvest(brain, _answer(
        _born(bit="the long way round", line="the long way round, tiny victory lap"),
        _born(bit="tiny victory lap", line="the long way round, tiny victory lap")))
    assert [c.bit for c in cs.open_callbacks()] == ["tiny victory lap"]
    assert len(out["planted"]) == 1 and out["refused"] == 1


def test_the_envelope_clears_the_whole_deep_ladder():
    from ai.llm_openai_compatible import OpenAICompatibleProvider as P

    assert harvest._HARVEST_TIMEOUT_S >= P.deep_ladder_ceiling_seconds()


# ── the lane and the prompt (ruling R27) ────────────────────────────────

def test_the_lane_is_deep_by_its_name():
    from ai.llm_openai_compatible import OpenAICompatibleProvider as P, is_deep_lane

    assert is_deep_lane("night_cycle_callbacks_harvest")
    assert (P.request_timeout_for_context("night_cycle_callbacks_harvest")
            == P.DEEP_REQUEST_TIMEOUT_SECONDS)


@pytest.mark.asyncio
async def test_the_real_call_names_its_lane(monkeypatch):
    import ai.llm_client as llm_client

    calls: list = []

    class _Fake:
        async def chat_completion(self, messages, **kw):
            calls.append((messages, kw))
            return '{"born": []}'

    monkeypatch.setattr(llm_client, "llm", _Fake())
    await harvest.callbacks_harvest_llm_call("WEEK PROMPT")
    (messages, kw), = calls
    assert kw["log_context"] == "night_cycle_callbacks_harvest"
    assert messages == [{"role": "user", "content": "WEEK PROMPT"}]


_FAMILY = re.compile(
    r"\b(holding|held|hold|holds|carrying|carried|carry|carries|sitting with|sits with|"
    r"sit with|landed|land|lands|the shape of|circling|circle|genuinely)\b", re.IGNORECASE)


def _prompt_text() -> str:
    return (ROOT / "prompts" / "night_cycle_callbacks_harvest.txt").read_text(encoding="utf-8")


def test_the_prompt_has_its_three_slots_each_once():
    text = _prompt_text()
    assert set(re.findall(r"<<([A-Z_]+)>>", text)) == {
        "WEEK_DATES", "WEEK_CONVERSATION", "ALREADY_IN_THE_BOOK"}
    for slot in ("WEEK_DATES", "WEEK_CONVERSATION", "ALREADY_IN_THE_BOOK"):
        assert text.count(f"<<{slot}>>") == 1, slot


def test_the_prompt_states_the_contract_and_never_asks_for_the_receipt():
    text = _prompt_text()
    assert '{"born": [{"bit": "' in text and '"line": "' in text
    assert '"kind": "nickname|bit|shorthand"' in text
    assert '"coined_by": "them|me|both"' in text
    assert '{"born": []}' in text
    assert "STRICT JSON" in text
    for stamped in ('"who"', '"when"', '"source"', '"receipt"'):
        assert stamped not in text, stamped


def test_the_prompt_carries_the_bar_the_turn_used_to_carry():
    low = _prompt_text().lower()
    assert "thrown back" in low
    assert "a single funny line is not a bit" in low
    assert "the line must contain the bit itself" in low
    # reworded for a week: nothing in it speaks of one exchange
    assert "this exchange" not in low
    assert "week" in low


def test_the_prompt_speaks_to_her_and_names_the_two_speakers():
    text = _prompt_text()
    assert text.startswith("You are PaLLi")
    assert '"them:"' in text and '"me:"' in text


def test_the_prompt_uses_plain_words_and_lf():
    raw = (ROOT / "prompts" / "night_cycle_callbacks_harvest.txt").read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert _FAMILY.findall(text) == []
    # nowhere, not even as a value: tests/release/test_owner_pronouns_neutral.py
    # reads every file under prompts/
    assert not re.search(r"\b(he|him|his|himself)\b", text, re.IGNORECASE)


def test_the_prompt_is_watched_at_boot():
    from ai.prompt_loader import EXPECTED_PROMPTS

    assert "night_cycle_callbacks_harvest" in EXPECTED_PROMPTS


# ── the night's step (ruling R29) ───────────────────────────────────────

def _stub_the_night(monkeypatch, calls, *, ritual):
    import ai.consolidation as cons
    import ai.disclosure_harvest as disclosures
    import ai.narrative_self as ns
    import ai.night_cycle as nc
    import ai.passions as passions
    import ai.stake_review as review

    async def fake_resolve():
        return 1

    async def fake_gather(*, owner_id, date):
        return "material"

    async def fake_reflect(**kw):
        calls.append("reflect")
        return {"wrote": kw["date"]}

    async def fake_consolidation(**kw):
        calls.append("consolidation")
        return {"skipped": "flag_off"}

    async def fake_ritual(**kw):
        calls.append("narrative")
        return dict(ritual)

    async def fake_targeted(**kw):
        calls.append("narrative_targeted")
        return {"skipped": "flag_off"}

    async def fake_review(**kw):
        calls.append("stake_review")
        return {"applied": 0}

    async def fake_passions_step(**kw):
        calls.append("passions")
        return {"journal": {"skipped": "flag_off"}, "interests": {"skipped": "flag_off"}}

    async def fake_disclosures(**kw):
        calls.append("disclosure_harvest")
        return {"faded": 0, "planted": None}

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)
    monkeypatch.setattr(ns, "run_targeted_correction_pass", fake_targeted)
    monkeypatch.setattr(review, "run_nightly_stake_review", fake_review)
    monkeypatch.setattr(passions, "run_passions_step", fake_passions_step)
    monkeypatch.setattr(disclosures, "run_nightly_disclosure_harvest", fake_disclosures)
    return nc


@pytest.mark.parametrize("ritual", [
    {"week": WEEK, "docs": {}},
    {"skipped": "flag_off"},                     # the story pages are off
    {"skipped": "first_authorship_pending"},     # or were never written
    {"skipped": "already_this_week"},
    {"error": "authorship_empty", "week": WEEK},  # or no page was revised
])
def test_the_harvest_runs_right_after_the_weekly_ritual_whatever_it_did(monkeypatch, ritual):
    calls: list = []
    nc = _stub_the_night(monkeypatch, calls, ritual=ritual)
    before = time.time()

    async def fake_harvest(owner_id, **kw):
        calls.append("callbacks_harvest")
        assert owner_id == 1
        assert kw["llm_call"] is harvest.callbacks_harvest_llm_call
        assert before <= kw["now"] <= time.time()
        return {"week": WEEK, "planted": []}

    monkeypatch.setattr(harvest, "run_weekly_callbacks_harvest", fake_harvest)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-27"))
    assert calls[:5] == ["reflect", "consolidation", "narrative", "callbacks_harvest",
                         "narrative_targeted"]
    assert result["callbacks_harvest"] == {"week": WEEK, "planted": []}
    assert result["narrative"] == ritual


def test_the_real_step_is_dark_in_the_night_while_the_flag_is_off(monkeypatch, book):
    nc = _stub_the_night(monkeypatch, [], ritual={"week": WEEK, "docs": {}})
    monkeypatch.setattr(harvest, "callbacks_harvest_llm_call", _must_not_call)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-27"))
    assert result["callbacks_harvest"] == {"skipped": "flag_off"}
    assert not book.exists()


def test_a_failed_harvest_never_costs_the_night(monkeypatch):
    calls: list = []
    nc = _stub_the_night(monkeypatch, calls, ritual={"week": WEEK, "docs": {}})

    async def boom(owner_id, **kw):
        raise RuntimeError("")

    monkeypatch.setattr(harvest, "run_weekly_callbacks_harvest", boom)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-27"))
    assert result["wrote"] == "2026-09-27"
    assert result["narrative"] == {"week": WEEK, "docs": {}}
    assert result["callbacks_harvest"] == {"error": "RuntimeError"}
    assert calls[-2:] == ["passions", "disclosure_harvest"]


def test_a_cancelled_harvest_cancels_the_night(monkeypatch):
    nc = _stub_the_night(monkeypatch, [], ritual={"week": WEEK, "docs": {}})

    async def cancelled(owner_id, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(harvest, "run_weekly_callbacks_harvest", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(nc.NightCycleEngine()._run_once("2026-09-27"))


def test_the_health_line_names_a_broken_harvest_and_not_a_quiet_one():
    from ai.night_cycle import _surface_errors

    assert _surface_errors(
        {"wrote": "x", "callbacks_harvest": {"week": WEEK, "error": "unparseable"}}
    ) == "callbacks_harvest: unparseable"
    assert _surface_errors(
        {"wrote": "x", "callbacks_harvest": {"error": "harvest exploded"}}
    ) == "callbacks_harvest: harvest exploded"
    for healthy in ({"week": WEEK, "planted": []},
                    {"week": WEEK, "planted": ["a-bit-0a1b2c"]},
                    {"week": WEEK, "planted": [], "refused": 2},
                    {"week": WEEK, "planted": [], "skipped": "no_conversation"},
                    {"skipped": "flag_off"},
                    {"skipped": "already_ran", "week": WEEK}):
        assert _surface_errors({"wrote": "x", "callbacks_harvest": healthy}) == "", healthy
