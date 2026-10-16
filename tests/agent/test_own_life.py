"""PaLLi's own life lately (the presence slice, 2026-10-13; D2, D7, D8 approved
word for word): what of its own the first look and the group opener can see,
the room check that keeps the owner's private life out of a group, and the
check that marks a stake said only when a reply really said it.

Stand-in names only: Robin is the owner."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from core.agent import own_life as ol

NOW = 1_000_000.0


@pytest.fixture
def stores(monkeypatch, tmp_path):
    """Every store the block reads, faked; the room-check cache in tmp."""
    import ai.curiosity_research as cr
    import ai.daydream_store as dd
    import ai.fear_store as fs
    import ai.quest_store as qs

    state = SimpleNamespace(
        fears=[SimpleNamespace(id="f1", text="that I'll forget what mattered", status="held",
                               planted_at=NOW - 9 * 86400, last_touched_at=NOW - 86400)],
        quests={"q1": SimpleNamespace(id="q1", text="learn to read sheet music", source="hers",
                                      status="alive", born=NOW - 5 * 86400, updated_at=NOW - 3600),
                "q2": SimpleNamespace(id="q2", text="Robin's garden", source="him-spoken",
                                      status="alive", born=NOW, updated_at=NOW)},
        daydreams=[SimpleNamespace(text="a lighthouse that keeps a diary", written_at=NOW - 26 * 3600)],
        dives=[SimpleNamespace(thought="how tide tables are made", timestamp=NOW - 2.2 * 86400)],
        fears_on=True,
    )
    monkeypatch.setattr(fs, "fears_enabled", lambda: state.fears_on)
    monkeypatch.setattr(fs, "held_fears", lambda **_: list(state.fears))
    monkeypatch.setattr(qs, "load_quests", lambda **_: dict(state.quests))
    monkeypatch.setattr(dd, "read_recent", lambda **_: list(state.daydreams))
    monkeypatch.setattr(cr, "recent_research_entries", lambda **_: list(state.dives))
    monkeypatch.setattr(ol, "CHECK_CACHE_PATH", tmp_path / "presence" / "own_life_room_check.json")
    return state


def test_the_block_has_the_heading_and_four_fixed_tags(stores):
    block = ol.own_life_block(ol.gather_items(now=NOW))
    assert block.splitlines() == [
        ol.OWN_LIFE_HEADING,
        "- [o1] something I'm afraid of: that I'll forget what mattered",
        "- [o2] something I want to do, in my own time: learn to read sheet music",
        "- [o3] what I was daydreaming about, yesterday: a lighthouse that keeps a diary",
        "- [o4] something I went and read about on my own, 2 days ago: how tide tables are made",
    ]


def test_a_missing_store_leaves_the_others_on_their_own_tags(stores):
    stores.fears_on = False
    stores.daydreams = []
    lines = ol.own_life_block(ol.gather_items(now=NOW)).splitlines()
    assert lines[1].startswith("- [o2] ") and lines[2].startswith("- [o4] ")


def test_nothing_of_its_own_is_no_block(stores):
    stores.fears, stores.quests, stores.daydreams, stores.dives = [], {}, [], []
    assert ol.own_life_block(ol.gather_items(now=NOW)) == ""


def test_old_daydreams_and_finds_are_left_out(stores):
    stores.daydreams = [SimpleNamespace(text="old", written_at=NOW - 4 * 86400)]
    stores.dives = [SimpleNamespace(thought="old", timestamp=NOW - 4 * 86400)]
    tags = [i.tag for i in ol.gather_items(now=NOW)]
    assert tags == ["o1", "o2"]


def test_a_breaking_store_costs_only_its_line(stores, monkeypatch):
    import ai.quest_store as qs

    def boom(**_):
        raise OSError("disk")

    monkeypatch.setattr(qs, "load_quests", boom)
    assert [i.tag for i in ol.gather_items(now=NOW)] == ["o1", "o3", "o4"]


def test_each_line_is_short(stores):
    stores.daydreams = [SimpleNamespace(text="x" * 2000, written_at=NOW - 3600)]
    item = next(i for i in ol.gather_items(now=NOW) if i.tag == "o3")
    assert len(item.text) <= ol.ITEM_CHARS


# ── the room check (D8) ──────────────────────────────────────────────────

def _answers(*replies):
    seen = []

    async def call(prompt):
        seen.append(prompt)
        return replies[len(seen) - 1] if len(seen) <= len(replies) else replies[-1]

    return call, seen


def test_an_unchecked_item_never_reaches_a_room_and_is_checked_once(stores):
    items = ol.gather_items(now=NOW)
    call, seen = _answers('{"room_ok": true}')
    assert ol.room_safe(items) == []                       # never waits on a check
    asyncio.run(ol.check_unchecked(items, llm_call=call))
    assert len(seen) == 4
    assert [i.tag for i in ol.room_safe(items)] == ["o1", "o2", "o3", "o4"]
    asyncio.run(ol.check_unchecked(items, llm_call=call))
    assert len(seen) == 4                                  # cached: asked once per item


def test_the_check_page_is_the_approved_one_filled(stores):
    items = ol.gather_items(now=NOW)
    call, seen = _answers('{"room_ok": false}')
    asyncio.run(ol.check_unchecked(items[:1], llm_call=call))
    assert "a fear, in its words:" in seen[0] or "a fear" in seen[0]
    assert "that I'll forget what mattered" in seen[0]
    assert "Answer no when you cannot tell." in seen[0]


@pytest.mark.parametrize("raw", ['{"room_ok": "true"}', 'yes', '', '{"room_ok": true} sure',
                                 '{"ok": true}', '[true]'])
def test_anything_but_a_clean_yes_is_a_no(stores, raw):
    items = ol.gather_items(now=NOW)[:1]
    call, _ = _answers(raw)
    asyncio.run(ol.check_unchecked(items, llm_call=call))
    assert ol.room_safe(items) == []


def test_a_fenced_yes_counts(stores):
    items = ol.gather_items(now=NOW)[:1]
    call, _ = _answers('```json\n{"room_ok": true}\n```')
    asyncio.run(ol.check_unchecked(items, llm_call=call))
    assert [i.tag for i in ol.room_safe(items)] == ["o1"]


def test_a_failed_check_is_not_cached_as_a_no(stores):
    items = ol.gather_items(now=NOW)[:1]

    async def boom(prompt):
        raise RuntimeError("down")

    asyncio.run(ol.check_unchecked(items, llm_call=boom))
    call, seen = _answers('{"room_ok": true}')
    asyncio.run(ol.check_unchecked(items, llm_call=call))
    assert len(seen) == 1 and ol.room_safe(items)


def test_an_edited_item_is_checked_again(stores):
    call, seen = _answers('{"room_ok": true}')
    asyncio.run(ol.check_unchecked(ol.gather_items(now=NOW)[:1], llm_call=call))
    stores.fears[0].text = "that Robin is unwell"
    items = ol.gather_items(now=NOW)[:1]
    assert ol.room_safe(items) == []


def test_a_damaged_cache_is_an_empty_one(stores):
    ol.CHECK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ol.CHECK_CACHE_PATH.write_text("{not json")
    assert ol.room_safe(ol.gather_items(now=NOW)) == []


def test_room_safe_lines_render_only_passed_items(stores):
    items = ol.gather_items(now=NOW)
    replies = iter(['{"room_ok": true}', '{"room_ok": false}', '{"room_ok": true}', '{"room_ok": false}'])

    async def call(prompt):
        return next(replies)

    asyncio.run(ol.check_unchecked(items, llm_call=call))
    lines = asyncio.run(ol.room_safe_lines(now=NOW, start_checks=False)).splitlines()
    assert [ln.split("]")[0] for ln in lines] == ["- [o1", "- [o3"]
    assert ol.OWN_LIFE_HEADING not in lines


# ── the said check for a stake (D7, ai/stake_ledger.py) ───────────────────

from ai import stake_ledger as sl  # noqa: E402

def test_a_stake_is_said_only_on_a_clean_yes():
    call, seen = _answers('{"said": true}')
    assert asyncio.run(sl.stake_said("hope", "the survey comes back clean",
                                     "fingers crossed on the survey", llm_call=call)) is True
    assert "a hope of its own" in seen[0] and "fingers crossed on the survey" in seen[0]
    for raw in ('{"said": "true"}', "", "yes", '{"said": true} ok'):
        call, _ = _answers(raw)
        assert asyncio.run(sl.stake_said("hope", "x", "y", llm_call=call)) is False


def test_an_empty_reply_or_a_failed_call_is_not_said():
    call, seen = _answers('{"said": true}')
    assert asyncio.run(sl.stake_said("hope", "x", "  ", llm_call=call)) is False and seen == []

    async def boom(prompt):
        raise RuntimeError("down")

    assert asyncio.run(sl.stake_said("hope", "x", "y", llm_call=boom)) is False


# ── Fable's final review (10-13): the background check ───────────────────

def test_a_second_turn_while_a_check_runs_starts_no_second_check(stores, monkeypatch):
    started = []
    gate = asyncio.Event()

    async def slow_check(items, **_):
        started.append([i.tag for i in items])
        await gate.wait()

    monkeypatch.setattr(ol, "check_unchecked", slow_check)

    async def two_turns():
        items = ol.gather_items(now=NOW)
        ol.check_in_background(items)
        ol.check_in_background(items)
        await asyncio.sleep(0)
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        ol.check_in_background(items)      # the first finished: a later turn may ask again
        await asyncio.sleep(0)
        gate.set()

    asyncio.run(two_turns())
    assert len(started) == 2


def test_a_broken_prompt_read_never_escapes_the_background_task(stores, monkeypatch):
    from ai.prompt_loader import prompt_loader

    def boom(name):
        raise OSError("disk")

    monkeypatch.setattr(prompt_loader, "load_prompt", boom)
    asyncio.run(ol.check_unchecked(ol.gather_items(now=NOW), llm_call=_answers('{"room_ok": true}')[0]))
