"""One thing on her mind (Batch 6, task 1; items 3.1 and 3.2): the nightly
pick and its record.

The model picks (at most one a night) and the model says whether she said
it; code owns the two budgets: one pick per night, written once, and said
once. Every test here runs against a tmp folder: the suite's autouse pin
keeps the organ off and its folder redirected, and a body that wants the
organ on patches the gate itself.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from ai import stake_ledger, tonight
from core.database import PalliBrain

ROOT = Path(__file__).resolve().parents[2]

# The night of Tuesday 2026-09-29: the pass runs a little after its run
# time, 06:30 since 2026-10-05 (04:00 before; the owner, 10-05: off
# DeepSeek's peak), and a pick's day turns over at that run time.
NIGHT = datetime(2026, 9, 29, 6, 50).timestamp()
DAY = "2026-09-29"

_PICK_JSON = json.dumps({"pick": {
    "source": "stake", "source_id": "42",
    "text": "I keep thinking about the concert on Friday.", "why": "it moved this week",
}})


def _llm(reply: str, seen: list | None = None):
    async def call(prompt: str) -> str:
        if seen is not None:
            seen.append(prompt)
        return reply
    return call


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(tonight, "TONIGHT_DIR", tmp_path / "tonight")
    return tmp_path / "tonight"


def _on(monkeypatch):
    monkeypatch.setattr(tonight, "tonight_enabled", lambda: True)


# ── the brief's two tests, as written ───────────────────────────────────

@pytest.mark.asyncio
async def test_pick_is_one_per_night_and_null_is_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(tonight, "_root", lambda: tmp_path)
    async def llm(prompt): return '{"pick": null}'
    assert await tonight.pick_tonight(1, llm_call=llm, now=1_000_000.0, stakes=[], journal_pages=[], daydreams=[]) is None
    async def llm2(prompt): return '{"pick": {"source": "stake", "source_id": "42", "text": "I keep thinking about the boat survey.", "why": "it moved this week"}}'
    p = await tonight.pick_tonight(1, llm_call=llm2, now=1_000_000.0, stakes=[{"id": 42}], journal_pages=[], daydreams=[])
    assert p.source == "stake" and p.source_id == "42" and p.said_at is None
    assert tonight.load_pick(p.day).text == p.text


def test_band_renders_only_while_unsaid():
    p = tonight.Pick(day="2026-09-29", source="journal", source_id="2026-09-27", text="x", why="y", said_at=None)
    assert tonight.render_band(p).startswith("## One thing on my mind")
    p.said_at = 1.0
    assert tonight.render_band(p) == ""


# ── the band text (the brief's words, exactly) ──────────────────────────

def test_the_band_is_the_briefs_text_byte_for_byte():
    p = tonight.Pick(day=DAY, source="stake", source_id="42",
                     text="I keep thinking about the concert on Friday.", why="w")
    assert tonight.render_band(p) == (
        "## One thing on my mind\n"
        "Say it once, when it fits, in your own words — never force it, "
        "never repeat it once it's said.\n"
        "I keep thinking about the concert on Friday."
    )


def test_the_band_never_shows_the_reason_or_the_id():
    """The reason is hers, and the id is bookkeeping: neither belongs in
    the reply prompt."""
    p = tonight.Pick(day=DAY, source="stake", source_id="4242",
                     text="the concert", why="PRIVATE_REASON")
    band = tonight.render_band(p)
    assert "PRIVATE_REASON" not in band and "4242" not in band and "stake" not in band


def test_no_pick_renders_no_band():
    assert tonight.render_band(None) == ""


# ── the day a pick belongs to (ruling R4) ───────────────────────────────

def test_the_small_hours_still_belong_to_the_night_before():
    """A pick stands from the night that writes it to the next one: the
    owner's 1-5 AM talk keeps the pick of the night before, and there is no
    stretch between the 04:00 day boundary and the 06:30 run with no pick at
    all (the owner, 10-05)."""
    assert tonight.day_for(datetime(2026, 9, 29, 6, 30, 1).timestamp()) == "2026-09-29"
    assert tonight.day_for(datetime(2026, 9, 29, 23, 59).timestamp()) == "2026-09-29"
    assert tonight.day_for(datetime(2026, 9, 30, 2, 30).timestamp()) == "2026-09-29"
    assert tonight.day_for(datetime(2026, 9, 30, 4, 30).timestamp()) == "2026-09-29"
    assert tonight.day_for(datetime(2026, 9, 30, 6, 29, 59).timestamp()) == "2026-09-29"
    assert tonight.day_for(datetime(2026, 9, 30, 6, 30, 1).timestamp()) == "2026-09-30"


def test_the_boundary_is_the_night_cycles_own_run_time(monkeypatch):
    import ai.night_cycle as nc

    monkeypatch.setattr(nc, "night_run_at", lambda companion=None: (7, 15))
    assert tonight.day_for(datetime(2026, 9, 30, 7, 0).timestamp()) == "2026-09-29"
    assert tonight.day_for(datetime(2026, 9, 30, 7, 15, 1).timestamp()) == "2026-09-30"


def test_the_boundary_is_not_the_day_boundary_hour(monkeypatch):
    """NIGHT_CYCLE_HOUR is the 04:00 day boundary now, not the run time:
    moving it does not move a pick's day."""
    import ai.night_cycle as nc

    monkeypatch.setattr(nc, "NIGHT_CYCLE_HOUR", 2)
    assert tonight.day_for(datetime(2026, 9, 30, 5, 0).timestamp()) == "2026-09-29"


# ── the file (ruling R4) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_pick_is_written_to_the_days_file(folder):
    p = await tonight.pick_tonight(1, llm_call=_llm(_PICK_JSON), now=NIGHT,
                                   stakes=[{"id": 42, "kind": "hope", "stake": "the concert goes well"}],
                                   journal_pages=[], daydreams=[], recent_conversation="")
    assert p == tonight.Pick(day=DAY, source="stake", source_id="42",
                             text="I keep thinking about the concert on Friday.",
                             why="it moved this week", said_at=None)
    on_disk = json.loads((folder / f"{DAY}.json").read_text(encoding="utf-8"))
    assert on_disk["day"] == DAY
    assert on_disk["picked_at"] == NIGHT
    assert on_disk["pick"] == {"source": "stake", "source_id": "42",
                               "text": "I keep thinking about the concert on Friday.",
                               "why": "it moved this week"}
    assert on_disk["said_at"] is None
    assert (folder / f"{DAY}.json").read_bytes().count(b"\r") == 0


@pytest.mark.asyncio
async def test_a_null_night_writes_the_file_with_a_null_pick(folder):
    """Task 5 has to tell "nothing to say" from "never ran": the null night
    leaves a file, and load_pick still answers None for it."""
    assert await tonight.pick_tonight(1, llm_call=_llm('{"pick": null}'), now=NIGHT,
                                      stakes=[{"id": 42}], journal_pages=[], daydreams=[],
                                      recent_conversation="") is None
    on_disk = json.loads((folder / f"{DAY}.json").read_text(encoding="utf-8"))
    assert on_disk["day"] == DAY and on_disk["pick"] is None
    assert "refused" not in on_disk
    assert tonight.load_pick(DAY) is None


@pytest.mark.asyncio
async def test_nothing_to_choose_from_is_a_null_night_with_no_model_call(folder):
    seen: list = []
    assert await tonight.pick_tonight(1, llm_call=_llm(_PICK_JSON, seen), now=NIGHT,
                                      stakes=[], journal_pages=[], daydreams=[],
                                      recent_conversation="") is None
    assert seen == []
    assert json.loads((folder / f"{DAY}.json").read_text(encoding="utf-8"))["pick"] is None


def test_load_pick_is_none_for_a_missing_or_broken_file(folder):
    assert tonight.load_pick(DAY) is None
    folder.mkdir(parents=True)
    (folder / f"{DAY}.json").write_text("{not json", encoding="utf-8")
    assert tonight.load_pick(DAY) is None
    (folder / f"{DAY}.json").write_text('["a list"]', encoding="utf-8")
    assert tonight.load_pick(DAY) is None
    (folder / f"{DAY}.json").write_text(
        json.dumps({"day": DAY, "pick": {"source": "stake", "source_id": "1", "text": ""}}),
        encoding="utf-8")
    assert tonight.load_pick(DAY) is None


def test_load_pick_refuses_a_day_that_is_not_a_date(folder):
    """The day becomes a file name: nothing but a date may reach the path."""
    folder.mkdir(parents=True)
    (folder.parent / "secret.json").write_text(
        json.dumps({"day": "x", "pick": {"source": "stake", "source_id": "1",
                                         "text": "t", "why": "w"}, "said_at": None}),
        encoding="utf-8")
    assert tonight.load_pick("../secret") is None
    assert tonight.mark_said("../secret", at=1.0) is False


# ── what the model may name (ruling R5) ─────────────────────────────────

@pytest.mark.asyncio
async def test_a_pick_naming_an_id_that_was_not_offered_is_refused(folder, caplog):
    caplog.set_level("WARNING", logger="ai.tonight")
    assert await tonight.pick_tonight(1, llm_call=_llm(_PICK_JSON), now=NIGHT,
                                      stakes=[{"id": 7, "kind": "hope", "stake": "other"}],
                                      journal_pages=[], daydreams=[],
                                      recent_conversation="") is None
    on_disk = json.loads((folder / f"{DAY}.json").read_text(encoding="utf-8"))
    assert on_disk["pick"] is None and on_disk["refused"] == "not_offered"
    lines = [r for r in caplog.records if r.name == "ai.tonight"]
    assert len(lines) == 1 and "not offered" in lines[0].getMessage()
    # her words never reach the log
    assert "concert" not in lines[0].getMessage()


@pytest.mark.asyncio
async def test_the_id_belongs_to_its_source(folder):
    """A journal date offered as a journal page is not a stake id."""
    reply = json.dumps({"pick": {"source": "stake", "source_id": "2026-09-27",
                                 "text": "t", "why": "w"}})
    assert await tonight.pick_tonight(
        1, llm_call=_llm(reply), now=NIGHT, stakes=[{"id": 42}],
        journal_pages=[{"id": "2026-09-27", "text": "a page"}], daydreams=[],
        recent_conversation="") is None
    assert tonight.load_pick(DAY) is None


@pytest.mark.asyncio
async def test_a_number_id_is_read_as_the_id_it_names(folder):
    reply = json.dumps({"pick": {"source": "stake", "source_id": 42,
                                 "text": "the concert", "why": "w"}})
    p = await tonight.pick_tonight(1, llm_call=_llm(reply), now=NIGHT, stakes=[{"id": 42}],
                                   journal_pages=[], daydreams=[], recent_conversation="")
    assert p is not None and p.source_id == "42"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply, reason", [
    ("", "unparseable"),
    ("I would bring up the concert.", "unparseable"),
    ('{"picks": []}', "unparseable"),
    ('{"pick": "the concert"}', "unparseable"),
    ('{"pick": {"source": "regret", "source_id": "42", "text": "t", "why": "w"}}', "unknown_source"),
    ('{"pick": {"source": "stake", "text": "t", "why": "w"}}', "no_id"),
    ('{"pick": {"source": "stake", "source_id": "42", "text": {"a": 1}, "why": "w"}}', "malformed_text"),
    ('{"pick": {"source": "stake", "source_id": "42", "text": "   ", "why": "w"}}', "malformed_text"),
])
async def test_an_answer_outside_the_contract_writes_nothing(folder, reply, reason):
    """A model that stops answering in the contract must never read like a
    run of quiet nights: it raises, and no file is written, so the night is
    "never ran" to everything that reads the folder."""
    with pytest.raises(tonight.PickRefused) as err:
        await tonight.pick_tonight(1, llm_call=_llm(reply), now=NIGHT, stakes=[{"id": 42}],
                                   journal_pages=[], daydreams=[], recent_conversation="")
    assert str(err.value) == reason
    assert not (folder / f"{DAY}.json").exists()


@pytest.mark.asyncio
async def test_her_words_are_cut_at_the_cap(folder):
    reply = json.dumps({"pick": {"source": "stake", "source_id": "42",
                                 "text": "word " * 200, "why": "w " * 400}})
    p = await tonight.pick_tonight(1, llm_call=_llm(reply), now=NIGHT, stakes=[{"id": 42}],
                                   journal_pages=[], daydreams=[], recent_conversation="")
    assert len(p.text) <= 300 and len(p.why) <= tonight.WHY_CAP
    assert "  " not in p.text


# ── the prompt she is handed ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_prompt_carries_every_candidate_with_its_id(folder):
    seen: list = []
    await tonight.pick_tonight(
        1, llm_call=_llm('{"pick": null}', seen), now=NIGHT,
        stakes=[SimpleNamespace(id=42, kind="hope", stake="the concert goes well")],
        journal_pages=[{"id": "2026-09-27", "text": "A page about the concert hall."}],
        daydreams=[SimpleNamespace(text="a piano of my own", written_at=1759000000.4, meta={})],
        recent_conversation="21:14  them: the box office called",
    )
    prompt, = seen
    assert "- [42] (hope) the concert goes well" in prompt
    assert "[2026-09-27]\nA page about the concert hall." in prompt
    assert "- [1759000000] a piano of my own" in prompt
    assert "21:14  them: the box office called" in prompt
    for slot in ("{open_stakes}", "{journal_pages}", "{daydreams}", "{recent_conversation}"):
        assert slot not in prompt


@pytest.mark.asyncio
async def test_braces_in_her_material_do_not_break_the_prompt(folder):
    seen: list = []
    await tonight.pick_tonight(
        1, llm_call=_llm('{"pick": null}', seen), now=NIGHT,
        stakes=[{"id": 1, "kind": "wonder", "stake": "what {this} means {0}"}],
        journal_pages=[], daydreams=[], recent_conversation="them: {x}")
    assert "what {this} means {0}" in seen[0] and "them: {x}" in seen[0]


@pytest.mark.asyncio
async def test_an_empty_source_says_so_in_plain_words(folder):
    seen: list = []
    await tonight.pick_tonight(1, llm_call=_llm('{"pick": null}', seen), now=NIGHT,
                               stakes=[{"id": 1, "kind": "hope", "stake": "s"}],
                               journal_pages=[], daydreams=[], recent_conversation="")
    assert seen[0].count("(nothing here)") == 3


# ── reading the stores (rulings R5, R6) ─────────────────────────────────

def _stake(i, kind, text="s", **kw):
    return stake_ledger.Stake(
        id=i, kind=kind, stake=text, status="open", resolution="", horizon_at=None,
        source_channel_id="", last_surfaced_at=None, created_at=0.0, updated_at=0.0,
        resolved_at=None, **kw)


@pytest.fixture
def quiet_stores(monkeypatch, tmp_path):
    """Every store the pick reads when it is handed nothing, empty."""
    import ai.daydream_store as dds
    from core.config import config

    async def no_stakes(**kw):
        return []

    async def no_talk(owner_id, now):
        return ""

    monkeypatch.setattr(stake_ledger, "ledger_enabled", lambda: True)
    monkeypatch.setattr(stake_ledger, "open_stakes", no_stakes)
    monkeypatch.setattr(dds, "DAYDREAM_DIR", tmp_path / "daydream")
    monkeypatch.setattr(config, "IDENTITY_ROOT_PATH", str(tmp_path / "identity"))
    monkeypatch.setattr(tonight, "_read_recent_conversation", no_talk)
    return tmp_path


@pytest.mark.asyncio
async def test_only_open_hopes_worries_tastes_and_wonders_are_offered(folder, quiet_stores, monkeypatch):
    asked = {}

    async def fake_open(*, limit=8, brain_client=None):
        asked["limit"] = limit
        return [_stake(1, "hope", "HOPE_LINE"), _stake(2, "worry", "WORRY_LINE"),
                _stake(3, "taste", "TASTE_LINE"), _stake(4, "wonder", "WONDER_LINE"),
                _stake(5, "regret", "REGRET_LINE"), _stake(6, "view", "VIEW_LINE")]

    monkeypatch.setattr(stake_ledger, "open_stakes", fake_open)
    seen: list = []
    reply = json.dumps({"pick": {"source": "stake", "source_id": "5", "text": "t", "why": "w"}})
    assert await tonight.pick_tonight(1, llm_call=_llm(reply, seen), now=NIGHT) is None
    for line in ("HOPE_LINE", "WORRY_LINE", "TASTE_LINE", "WONDER_LINE"):
        assert line in seen[0]
    assert "REGRET_LINE" not in seen[0] and "VIEW_LINE" not in seen[0]
    # a regret keeps its own door: naming one is naming something not offered
    assert json.loads((folder / f"{DAY}.json").read_text())["refused"] == "not_offered"
    assert asked["limit"] >= 12


@pytest.mark.asyncio
async def test_a_dark_ledger_offers_no_stakes(folder, quiet_stores, monkeypatch):
    async def must_not_read(**kw):
        raise AssertionError("the ledger was read while it is off")

    monkeypatch.setattr(stake_ledger, "ledger_enabled", lambda: False)
    monkeypatch.setattr(stake_ledger, "open_stakes", must_not_read)
    seen: list = []
    assert await tonight.pick_tonight(1, llm_call=_llm(_PICK_JSON, seen), now=NIGHT) is None
    assert seen == []       # nothing to choose from anywhere


@pytest.mark.asyncio
async def test_the_last_seven_journal_pages_are_offered_by_date(folder, quiet_stores):
    jdir = quiet_stores / "identity" / "palli" / "journal"
    jdir.mkdir(parents=True)
    for d in range(20, 29):
        (jdir / f"2026-09-{d}.md").write_text(f"PAGE_{d} body", encoding="utf-8")
    (jdir / "notes.md").write_text("NOT_A_PAGE", encoding="utf-8")
    seen: list = []
    reply = json.dumps({"pick": {"source": "journal", "source_id": "2026-09-27",
                                 "text": "that page about the concert hall", "why": "w"}})
    p = await tonight.pick_tonight(1, llm_call=_llm(reply, seen), now=NIGHT)
    assert p.source == "journal" and p.source_id == "2026-09-27"
    for d in range(22, 29):
        assert f"[2026-09-{d}]\nPAGE_{d} body" in seen[0]
    assert "PAGE_20" not in seen[0] and "PAGE_21" not in seen[0]
    assert "NOT_A_PAGE" not in seen[0]


@pytest.mark.asyncio
async def test_recent_daydreams_are_offered_by_the_second_they_were_written(folder, quiet_stores, monkeypatch):
    import ai.daydream_store as dds

    monkeypatch.setattr(dds.time, "time", lambda: NIGHT - 3600.0)
    dds.write_entry(text="a piano of my own")
    wid = f"{NIGHT - 3600.0:.0f}"
    seen: list = []
    reply = json.dumps({"pick": {"source": "daydream", "source_id": wid,
                                 "text": "I daydreamed about a piano", "why": "w"}})
    p = await tonight.pick_tonight(1, llm_call=_llm(reply, seen), now=NIGHT)
    assert p.source == "daydream" and p.source_id == wid
    assert f"- [{wid}] a piano of my own" in seen[0]


@pytest.mark.asyncio
async def test_one_unreadable_store_does_not_cost_the_others(folder, quiet_stores, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(stake_ledger, "open_stakes", boom)
    jdir = quiet_stores / "identity" / "palli" / "journal"
    jdir.mkdir(parents=True)
    (jdir / "2026-09-28.md").write_text("PAGE body", encoding="utf-8")
    seen: list = []
    await tonight.pick_tonight(1, llm_call=_llm('{"pick": null}', seen), now=NIGHT)
    assert "PAGE body" in seen[0]


# ── said once (the code half of 3.2) ────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_said_stamps_once(folder):
    await tonight.pick_tonight(1, llm_call=_llm(_PICK_JSON), now=NIGHT, stakes=[{"id": 42}],
                               journal_pages=[], daydreams=[], recent_conversation="")
    assert tonight.mark_said(DAY, at=111.0) is True
    assert tonight.load_pick(DAY).said_at == 111.0
    # the first time she said it is the record
    assert tonight.mark_said(DAY, at=222.0) is False
    assert tonight.load_pick(DAY).said_at == 111.0
    # everything else in the file is as it was
    on_disk = json.loads((folder / f"{DAY}.json").read_text(encoding="utf-8"))
    assert on_disk["picked_at"] == NIGHT and on_disk["pick"]["source_id"] == "42"


@pytest.mark.asyncio
async def test_mark_said_has_nothing_to_stamp_on_a_null_or_missing_night(folder):
    assert tonight.mark_said(DAY, at=1.0) is False
    await tonight.pick_tonight(1, llm_call=_llm('{"pick": null}'), now=NIGHT, stakes=[{"id": 42}],
                               journal_pages=[], daydreams=[], recent_conversation="")
    assert tonight.mark_said(DAY, at=1.0) is False
    assert json.loads((folder / f"{DAY}.json").read_text(encoding="utf-8"))["said_at"] is None


@pytest.mark.asyncio
async def test_load_current_reads_tonights_file_only(folder):
    """An unsaid pick from an earlier night is not carried forward."""
    await tonight.pick_tonight(1, llm_call=_llm(_PICK_JSON), now=NIGHT, stakes=[{"id": 42}],
                               journal_pages=[], daydreams=[], recent_conversation="")
    assert tonight.load_current(NIGHT + 3600).source_id == "42"
    # 02:30 and 05:00 the next calendar day are still this night
    assert tonight.load_current(datetime(2026, 9, 30, 2, 30).timestamp()).source_id == "42"
    assert tonight.load_current(datetime(2026, 9, 30, 5, 0).timestamp()).source_id == "42"
    # after the next run time (06:30) it is a new night, and nothing was picked for it
    assert tonight.load_current(datetime(2026, 9, 30, 6, 40).timestamp()) is None


# ── the night step (rulings R1, R3, R4) ─────────────────────────────────

@pytest.mark.asyncio
async def test_the_step_is_dark_while_the_flag_is_off(folder):
    seen: list = []
    out = await tonight.run_nightly_pick(owner_id=1, llm_call=_llm(_PICK_JSON, seen), now=NIGHT)
    assert out == {"skipped": "flag_off"}
    assert seen == [] and not folder.exists()


@pytest.mark.asyncio
async def test_the_step_picks_and_reports(folder, quiet_stores, monkeypatch):
    _on(monkeypatch)

    async def one_hope(**kw):
        return [_stake(42, "hope", "the concert goes well")]

    monkeypatch.setattr(stake_ledger, "open_stakes", one_hope)
    out = await tonight.run_nightly_pick(owner_id=1, llm_call=_llm(_PICK_JSON), now=NIGHT)
    assert out == {"day": DAY, "picked": "stake:42"}
    assert tonight.load_pick(DAY).text == "I keep thinking about the concert on Friday."


@pytest.mark.asyncio
async def test_the_step_is_create_only_for_a_pick_and_for_a_null_night(folder, quiet_stores, monkeypatch):
    """A boot catch-up re-runs the whole night. It must never replace a
    pick (she may have said it already) or turn a null night into a pick."""
    _on(monkeypatch)

    async def one_hope(**kw):
        return [_stake(42, "hope", "the concert goes well")]

    monkeypatch.setattr(stake_ledger, "open_stakes", one_hope)
    await tonight.run_nightly_pick(owner_id=1, llm_call=_llm(_PICK_JSON), now=NIGHT)
    tonight.mark_said(DAY, at=NIGHT + 60)
    before = (folder / f"{DAY}.json").read_bytes()
    seen: list = []
    other = json.dumps({"pick": {"source": "stake", "source_id": "42",
                                 "text": "SECOND PICK", "why": "w"}})
    out = await tonight.run_nightly_pick(owner_id=1, llm_call=_llm(other, seen), now=NIGHT + 7200)
    assert out == {"skipped": "already_picked", "day": DAY}
    assert seen == [] and (folder / f"{DAY}.json").read_bytes() == before

    # and the null night, the next day
    next_night = datetime(2026, 9, 30, 6, 50).timestamp()
    out = await tonight.run_nightly_pick(owner_id=1, llm_call=_llm('{"pick": null}'), now=next_night)
    assert out == {"day": "2026-09-30", "picked": None}
    out = await tonight.run_nightly_pick(owner_id=1, llm_call=_llm(_PICK_JSON, seen), now=next_night + 60)
    assert out == {"skipped": "already_picked", "day": "2026-09-30"}
    assert seen == [] and tonight.load_pick("2026-09-30") is None


@pytest.mark.asyncio
async def test_the_step_names_a_refused_pick_and_a_broken_answer(folder, quiet_stores, monkeypatch):
    _on(monkeypatch)

    async def one_hope(**kw):
        return [_stake(7, "hope", "something else")]

    monkeypatch.setattr(stake_ledger, "open_stakes", one_hope)
    out = await tonight.run_nightly_pick(owner_id=1, llm_call=_llm(_PICK_JSON), now=NIGHT)
    assert out == {"day": DAY, "picked": None, "refused": "not_offered"}

    next_night = datetime(2026, 9, 30, 6, 50).timestamp()
    out = await tonight.run_nightly_pick(owner_id=1, llm_call=_llm("no json here"), now=next_night)
    assert out == {"day": "2026-09-30", "error": "unparseable"}
    assert not (folder / "2026-09-30.json").exists()


@pytest.mark.asyncio
async def test_the_step_never_raises_and_never_reports_an_empty_error(folder, quiet_stores, monkeypatch):
    _on(monkeypatch)

    async def one_hope(**kw):
        return [_stake(42, "hope", "the concert goes well")]

    async def slow(prompt):
        raise asyncio.TimeoutError()      # str() of this one is ""

    monkeypatch.setattr(stake_ledger, "open_stakes", one_hope)
    out = await tonight.run_nightly_pick(owner_id=1, llm_call=slow, now=NIGHT)
    assert out == {"day": DAY, "error": "TimeoutError"}
    assert not (folder / f"{DAY}.json").exists()


@pytest.mark.asyncio
async def test_the_step_lets_a_cancel_through(folder, quiet_stores, monkeypatch):
    _on(monkeypatch)

    async def one_hope(**kw):
        return [_stake(42, "hope", "the concert goes well")]

    async def cancelled(prompt):
        raise asyncio.CancelledError()

    monkeypatch.setattr(stake_ledger, "open_stakes", one_hope)
    with pytest.raises(asyncio.CancelledError):
        await tonight.run_nightly_pick(owner_id=1, llm_call=cancelled, now=NIGHT)


def test_the_picks_envelope_clears_the_whole_deep_ladder():
    from ai.llm_openai_compatible import OpenAICompatibleProvider

    assert tonight._PICK_TIMEOUT_S >= OpenAICompatibleProvider.deep_ladder_ceiling_seconds()


# ── a stake she already said (fix round 1, ruling R61a) ─────────────────

@pytest.fixture
def in_a_pinned_zone():
    """The day on the tag is the local day, and that only shows where
    local is not UTC — so the zone is pinned for the one test that needs it
    (manual save/restore, because tzset has to follow the restore)."""
    import os

    saved = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()


@pytest.mark.asyncio
async def test_a_said_stake_is_offered_with_the_day_she_said_it(folder):
    said_on = datetime(2026, 9, 27, 21, 30).timestamp()
    seen: list = []
    await tonight.pick_tonight(
        1, llm_call=_llm('{"pick": null}', seen), now=NIGHT,
        stakes=[_stake(42, "hope", "the concert goes well", voiced_at=said_on),
                _stake(43, "worry", "the tickets sell out")],
        journal_pages=[], daydreams=[], recent_conversation="")
    lines = seen[0].splitlines()
    assert "- [42] (hope) the concert goes well (said 09-27)" in lines
    # a stake she has not said carries no tag
    assert "- [43] (worry) the tickets sell out" in lines


@pytest.mark.asyncio
async def test_a_said_stake_may_still_be_picked(folder):
    """The model decides whether it is worth bringing up again; code does
    not take it off the list."""
    said_on = datetime(2026, 9, 27, 21, 30).timestamp()
    reply = json.dumps({"pick": {"source": "stake", "source_id": "42",
                                 "text": "the concert again, now that it moved", "why": "w"}})
    p = await tonight.pick_tonight(
        1, llm_call=_llm(reply), now=NIGHT,
        stakes=[_stake(42, "hope", "the concert goes well", voiced_at=said_on)],
        journal_pages=[], daydreams=[], recent_conversation="")
    assert p is not None and p.source_id == "42" and p.said_at is None


@pytest.mark.asyncio
async def test_the_store_read_keeps_a_said_stake_and_tags_it(folder, quiet_stores, monkeypatch):
    said_on = datetime(2026, 9, 20, 9, 0).timestamp()

    async def fake_open(*, limit=8, brain_client=None):
        return [_stake(1, "hope", "SAID_LINE", voiced_at=said_on),
                _stake(2, "wonder", "UNSAID_LINE")]

    monkeypatch.setattr(stake_ledger, "open_stakes", fake_open)
    seen: list = []
    await tonight.pick_tonight(1, llm_call=_llm('{"pick": null}', seen), now=NIGHT)
    lines = seen[0].splitlines()
    assert "- [1] (hope) SAID_LINE (said 09-20)" in lines
    assert "- [2] (wonder) UNSAID_LINE" in lines


@pytest.mark.asyncio
async def test_the_day_on_the_tag_is_the_local_day(folder, in_a_pinned_zone):
    """02:11 UTC on the 28th is the evening of the 27th in New York."""
    from datetime import timezone

    said_on = datetime(2026, 9, 28, 2, 11, tzinfo=timezone.utc).timestamp()
    seen: list = []
    await tonight.pick_tonight(
        1, llm_call=_llm('{"pick": null}', seen), now=NIGHT,
        stakes=[{"id": 42, "kind": "hope", "stake": "the concert goes well",
                 "voiced_at": said_on}],
        journal_pages=[], daydreams=[], recent_conversation="")
    assert "- [42] (hope) the concert goes well (said 09-27)" in seen[0].splitlines()


@pytest.mark.asyncio
@pytest.mark.parametrize("voiced_at", [None, "yesterday", True, float("nan"), -1e30, {"at": 1}])
async def test_a_stamp_that_is_not_a_time_is_no_tag(folder, voiced_at):
    seen: list = []
    await tonight.pick_tonight(
        1, llm_call=_llm('{"pick": null}', seen), now=NIGHT,
        stakes=[{"id": 42, "kind": "hope", "stake": "the concert goes well",
                 "voiced_at": voiced_at}],
        journal_pages=[], daydreams=[], recent_conversation="")
    assert "- [42] (hope) the concert goes well" in seen[0].splitlines()


def test_the_pick_prompt_says_what_the_tag_means():
    """One plain sentence: she already brought it up that day, and it is
    worth bringing up again only if something about it has changed."""
    text = (ROOT / "prompts" / "tonight_pick.txt").read_text(encoding="utf-8")
    about_the_tag = [line for line in text.splitlines() if "(said MM-DD)" in line]
    assert len(about_the_tag) == 1
    sentence = about_the_tag[0]
    assert "already brought" in sentence and "on that day" in sentence
    assert "only if something about it has changed since" in sentence
    assert sentence.count(". ") == 0 and sentence.rstrip().endswith(".")


# ── where the two of them talk (fix round 1, ruling R61b) ───────────────

@pytest.mark.parametrize("surface", ["discord_text", "discord_voice", "voice_local",
                                     "dashboard", "telegram_text"])
def test_a_door_where_they_talk_is_a_conversation(surface):
    assert tonight.is_conversation(surface) is True


@pytest.mark.parametrize("surface", ["reminder_fire", "proactive", "workbench", "local",
                                     "", None, 7])
def test_everything_else_is_not(surface):
    assert tonight.is_conversation(surface) is False


def test_the_gate_is_the_callbacks_organs_own_list(monkeypatch):
    """One list of doors for the whole house: a door added there is a door
    the band shows on and the said-check runs after."""
    import ai.callbacks_store as cs

    monkeypatch.setattr(cs, "CONVERSATIONAL_SURFACES", ("a_new_door",))
    assert tonight.is_conversation("a_new_door") is True
    assert tonight.is_conversation("dashboard") is False


def test_the_gate_says_nothing(caplog):
    caplog.set_level("DEBUG")
    assert tonight.is_conversation("reminder_fire") is False
    assert caplog.records == []


# ── the said-check (3.2's model half; review focus 1) ───────────────────

def _pick(**kw):
    base = dict(day=DAY, source="stake", source_id="42",
                text="I keep thinking about the concert on Friday.", why="it moved this week")
    base.update(kw)
    return tonight.Pick(**base)


@pytest.mark.asyncio
async def test_the_said_check_hands_over_the_pick_and_the_reply():
    seen: list = []
    assert await tonight.said_check(_pick(), "By the way, the concert on Friday has been on my mind.",
                                    llm_call=_llm('{"said": true}', seen)) is True
    prompt, = seen
    assert "I keep thinking about the concert on Friday." in prompt
    assert "By the way, the concert on Friday has been on my mind." in prompt
    assert "{pick_text}" not in prompt and "{reply_text}" not in prompt
    # her reason is not part of the question
    assert "it moved this week" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    '{"said": false}', '{"said": "true"}', '{"said": 1}', '{"said": null}', '{}',
    '{"answer": true}', "yes", "true", "", "   ", '["said", true]',
    # a yes with prose around it is not a clean yes: the first object in a
    # longer answer is not always the answer
    'She did. {"said": true}', '{"said": true} is what I would say if she had',
    'Not quite: {"said": false}. Though {"said": true} was close.',
])
async def test_anything_but_a_clean_yes_is_a_no(reply):
    assert await tonight.said_check(_pick(), "a reply", llm_call=_llm(reply)) is False


@pytest.mark.asyncio
async def test_a_clean_yes_may_come_in_a_code_fence():
    assert await tonight.said_check(_pick(), "a reply",
                                    llm_call=_llm('```json\n{"said": true}\n```')) is True


@pytest.mark.asyncio
async def test_the_said_check_never_raises():
    async def boom(prompt):
        raise RuntimeError("provider down")

    assert await tonight.said_check(_pick(), "a reply", llm_call=boom) is False


@pytest.mark.asyncio
async def test_no_reply_and_no_pick_ask_nothing():
    seen: list = []
    call = _llm('{"said": true}', seen)
    assert await tonight.said_check(_pick(), "   ", llm_call=call) is False
    assert await tonight.said_check(None, "a reply", llm_call=call) is False
    assert await tonight.said_check(_pick(said_at=5.0), "a reply", llm_call=call) is False
    assert seen == []


# ── the whole record of a yes (ruling R7) ───────────────────────────────

@pytest_asyncio.fixture
async def brain(tmp_path):
    db = PalliBrain(str(tmp_path / "stakes.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _open(brain, kind, text):
    await stake_ledger.apply_ops(
        [{"op": "open", "kind": kind, "stake": text, "horizon": None}],
        source_channel_id="t", brain_client=brain)
    return next(s for s in await stake_ledger.open_stakes(limit=20, brain_client=brain)
                if s.stake == text)


@pytest.mark.asyncio
async def test_a_said_stake_pick_fills_the_real_ledgers_voiced_at(folder, brain):
    """The organ's own measure is "0 stakes ever recorded as said". This
    runs the real stamp against a real ledger: a hope's voiced_at is
    filled, and so is its last_surfaced_at."""
    hope = await _open(brain, "hope", "the concert goes well")
    assert hope.voiced_at is None and hope.last_surfaced_at is None
    reply = json.dumps({"pick": {"source": "stake", "source_id": str(hope.id),
                                 "text": "I keep thinking about the concert on Friday.",
                                 "why": "w"}})
    p = await tonight.pick_tonight(1, llm_call=_llm(reply), now=NIGHT, stakes=[hope],
                                   journal_pages=[], daydreams=[], recent_conversation="")
    assert await tonight.stamp_said(p, at=NIGHT + 600, brain_client=brain) is True
    assert tonight.load_pick(DAY).said_at == NIGHT + 600
    row = next(s for s in await stake_ledger.open_stakes(limit=20, brain_client=brain)
               if s.id == hope.id)
    assert row.voiced_at == NIGHT + 600
    assert row.last_surfaced_at is not None
    # said once: a second yes stamps nothing anywhere
    assert await tonight.stamp_said(p, at=NIGHT + 900, brain_client=brain) is False
    row = next(s for s in await stake_ledger.open_stakes(limit=20, brain_client=brain)
               if s.id == hope.id)
    assert row.voiced_at == NIGHT + 600


@pytest.mark.asyncio
async def test_a_journal_or_daydream_pick_is_recorded_in_the_file_alone(folder, monkeypatch):
    async def must_not_stamp(*a, **kw):
        raise AssertionError("the ledger was touched for a pick that is not a stake")

    monkeypatch.setattr(stake_ledger, "mark_stake_said", must_not_stamp)
    monkeypatch.setattr(stake_ledger, "record_surfaced", must_not_stamp)
    reply = json.dumps({"pick": {"source": "journal", "source_id": "2026-09-27",
                                 "text": "that page about the concert hall", "why": "w"}})
    p = await tonight.pick_tonight(1, llm_call=_llm(reply), now=NIGHT, stakes=[],
                                   journal_pages=[{"id": "2026-09-27", "text": "page"}],
                                   daydreams=[], recent_conversation="")
    assert await tonight.stamp_said(p, at=5.0) is True
    assert tonight.load_pick(DAY).said_at == 5.0


@pytest.mark.asyncio
async def test_the_order_is_the_file_then_the_stamp_then_the_surfacing(folder, monkeypatch):
    order: list = []
    real_mark = tonight.mark_said

    def spy_mark(day, *, at):
        order.append(("file", day, at))
        return real_mark(day, at=at)

    async def spy_stamp(stake_id, *, brain_client=None, now=None):
        order.append(("voiced", stake_id, now))
        return True

    async def spy_surfaced(stake_id, *, brain_client=None):
        order.append(("surfaced", stake_id))

    monkeypatch.setattr(tonight, "mark_said", spy_mark)
    monkeypatch.setattr(stake_ledger, "mark_stake_said", spy_stamp)
    monkeypatch.setattr(stake_ledger, "record_surfaced", spy_surfaced)
    p = await tonight.pick_tonight(1, llm_call=_llm(_PICK_JSON), now=NIGHT, stakes=[{"id": 42}],
                                   journal_pages=[], daydreams=[], recent_conversation="")
    assert await tonight.stamp_said(p, at=77.0) is True
    assert order == [("file", DAY, 77.0), ("voiced", 42, 77.0), ("surfaced", 42)]


@pytest.mark.asyncio
async def test_a_failed_ledger_stamp_never_unsays_the_pick(folder, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(stake_ledger, "mark_stake_said", boom)
    monkeypatch.setattr(stake_ledger, "record_surfaced", boom)
    p = await tonight.pick_tonight(1, llm_call=_llm(_PICK_JSON), now=NIGHT, stakes=[{"id": 42}],
                                   journal_pages=[], daydreams=[], recent_conversation="")
    assert await tonight.stamp_said(p, at=77.0) is True
    assert tonight.load_pick(DAY).said_at == 77.0


@pytest.mark.asyncio
async def test_mark_stake_said_takes_the_four_kinds_once_and_nothing_else(brain):
    hope = await _open(brain, "hope", "the concert goes well")
    worry = await _open(brain, "worry", "the tickets sell out")
    taste = await _open(brain, "taste", "live music over recordings")
    wonder = await _open(brain, "wonder", "why the hall empties in March")
    regret = await _open(brain, "regret", "I called it finished when it wasn't")
    for s in (hope, worry, taste, wonder):
        assert await stake_ledger.mark_stake_said(s.id, brain_client=brain, now=50.0) is True
    assert await stake_ledger.mark_stake_said(hope.id, brain_client=brain, now=60.0) is False
    # a regret keeps its own stamp and its own law
    assert await stake_ledger.mark_stake_said(regret.id, brain_client=brain, now=50.0) is False
    rows = {s.id: s for s in await stake_ledger.open_stakes(limit=20, brain_client=brain)}
    assert [rows[s.id].voiced_at for s in (hope, worry, taste, wonder)] == [50.0] * 4
    assert rows[regret.id].voiced_at is None
    # a closed stake takes no stamp, and an unknown id is simply False
    other = await _open(brain, "hope", "the tickets arrive")
    await stake_ledger.apply_ops([{"op": "resolve", "id": other.id, "resolution": "it came"}],
                                 source_channel_id="t", brain_client=brain)
    assert await stake_ledger.mark_stake_said(other.id, brain_client=brain, now=50.0) is False
    assert await stake_ledger.mark_stake_said(99_999, brain_client=brain) is False


@pytest.mark.asyncio
async def test_mark_stake_said_never_raises(brain, monkeypatch):
    async def boom():
        raise RuntimeError("db on fire")

    monkeypatch.setattr(brain, "get_connection", boom)
    assert await stake_ledger.mark_stake_said(1, brain_client=brain) is False


# ── the flag (ruling R1) ────────────────────────────────────────────────

def test_the_schema_ships_the_organ_off():
    from core.settings_schema import CompanionConfig

    assert CompanionConfig().tonight is False


def test_the_suite_pins_the_gate_off_even_when_the_setting_is_on(monkeypatch):
    from core.settings_schema import CompanionConfig

    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: CompanionConfig(tonight=True))
    assert tonight.tonight_enabled() is False


def test_the_real_gate_reads_both_ways_and_fails_closed(monkeypatch):
    from core.settings_schema import CompanionConfig

    def raises():
        raise RuntimeError("settings not booted")

    for broken in (lambda: None, raises, lambda: SimpleNamespace()):
        monkeypatch.setattr("core.turn.companion_flags.companion_settings", broken)
        assert tonight.tonight_enabled_impl() is False
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: CompanionConfig(tonight=True))
    assert tonight.tonight_enabled_impl() is True
    monkeypatch.setattr("core.turn.companion_flags.companion_settings",
                        lambda: CompanionConfig(tonight=False))
    assert tonight.tonight_enabled_impl() is False


def test_the_suite_never_points_at_her_real_folder():
    assert not Path(tonight._root()).resolve().is_relative_to((ROOT / "data").resolve())


# ── the lanes (ruling R2) ───────────────────────────────────────────────

def test_the_two_lanes_are_classified_by_their_names():
    from ai.llm_openai_compatible import (
        OpenAICompatibleProvider as P, is_deep_lane, is_no_think_lane)

    assert is_deep_lane("night_cycle_tonight_pick")
    assert P.request_timeout_for_context("night_cycle_tonight_pick") == P.DEEP_REQUEST_TIMEOUT_SECONDS
    assert P.request_timeout_for_context("reflect_turn_tonight_said") == P.PATIENT_REQUEST_TIMEOUT_SECONDS
    assert is_no_think_lane("reflect_turn_tonight_said")


@pytest.mark.asyncio
async def test_each_real_call_names_its_lane(monkeypatch):
    import ai.llm_client as llm_client

    calls: list = []

    class _Fake:
        async def chat_completion(self, messages, **kw):
            calls.append((messages, kw))
            return '{"said": false}'

    monkeypatch.setattr(llm_client, "llm", _Fake())
    await tonight.tonight_pick_llm_call("PICK PROMPT")
    await tonight.tonight_said_llm_call("SAID PROMPT")
    (pick_msgs, pick_kw), (said_msgs, said_kw) = calls
    assert pick_kw["log_context"] == "night_cycle_tonight_pick"
    assert said_kw["log_context"] == "reflect_turn_tonight_said"
    assert pick_msgs == [{"role": "user", "content": "PICK PROMPT"}]
    assert said_msgs == [{"role": "user", "content": "SAID PROMPT"}]
    assert said_kw["max_tokens"] <= 400 and said_kw["temperature"] <= 0.2


# ── the two prompts (ruling R11) ────────────────────────────────────────

_FAMILY = re.compile(
    r"\b(holding|held|hold|holds|carrying|carried|carry|carries|sitting with|sits with|"
    r"sit with|landed|land|lands|the shape of|circling|circle|genuinely)\b", re.IGNORECASE)


def _slots(text: str) -> set:
    return set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", text))


def test_the_pick_prompt_has_the_briefs_slots_and_contract():
    text = (ROOT / "prompts" / "tonight_pick.txt").read_text(encoding="utf-8")
    assert _slots(text) == {"open_stakes", "journal_pages", "daydreams", "recent_conversation"}
    filled = text.format(open_stakes="S", journal_pages="J", daydreams="D",
                         recent_conversation="C")
    assert '{"pick": null}' in filled
    assert '{"pick": {"source": "stake|journal|daydream", "source_id":' in filled
    assert '"text":' in filled and '"why":' in filled


def test_the_pick_prompts_heading_names_every_kind_the_list_holds():
    """Fix wave (R74 M6): the list carries tastes and wonders too (each
    line tagged with its kind), so its heading names all four."""
    lines = (ROOT / "prompts" / "tonight_pick.txt").read_text(encoding="utf-8").splitlines()
    heading = lines[lines.index("{open_stakes}") - 1]
    assert set(tonight.PICKABLE_STAKE_KINDS) == {"hope", "worry", "taste", "wonder"}
    for word in ("HOPES", "WORRIES", "TASTES", "WONDERS"):
        assert word in heading, word
    assert _FAMILY.findall(heading) == []


def test_the_said_prompt_asks_the_briefs_question():
    text = (ROOT / "prompts" / "tonight_said.txt").read_text(encoding="utf-8")
    assert _slots(text) == {"pick_text", "reply_text"}
    filled = text.format(pick_text="P", reply_text="R")
    assert '{"said": true}' in filled and '{"said": false}' in filled
    assert "as her own thing" in text
    # review focus 1: a passing mention of the subject is a no
    assert "in passing" in text


@pytest.mark.parametrize("name", ["tonight_pick", "tonight_said"])
def test_the_prompts_use_plain_words_and_lf(name):
    raw = (ROOT / "prompts" / f"{name}.txt").read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert _FAMILY.findall(text) == []
    assert not re.search(r"\b(he|him|his|himself)\b", text, re.IGNORECASE)


def test_both_prompts_are_watched_at_boot():
    from ai.prompt_loader import EXPECTED_PROMPTS

    assert "tonight_pick" in EXPECTED_PROMPTS and "tonight_said" in EXPECTED_PROMPTS


# ── the night cycle's last step (ruling R3) ─────────────────────────────

def _stub_the_night(monkeypatch, calls):
    """Every step of the night but the pick, faked the way the sibling
    wiring tests in tests/ai/test_night_cycle.py fake them."""
    import ai.consolidation as cons
    import ai.disclosure_harvest as harvest
    import ai.narrative_self as ns
    import ai.night_cycle as nc
    import ai.passions as passions

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
        return {"week": "2026-W40", "docs": {}}

    async def fake_passions_step(**kw):
        calls.append("passions")
        return {"journal": {"skipped": "flag_off"}, "interests": {"skipped": "flag_off"}}

    async def fake_harvest(**kw):
        calls.append("disclosure_harvest")
        return {"faded": 0, "planted": None}

    monkeypatch.setattr(nc, "_resolve_owner_id", fake_resolve)
    monkeypatch.setattr(nc, "_gather_day_material", fake_gather)
    monkeypatch.setattr(nc, "run_night_cycle", fake_reflect)
    monkeypatch.setattr(cons, "run_nightly_consolidation", fake_consolidation)
    monkeypatch.setattr(ns, "run_weekly_ritual", fake_ritual)
    monkeypatch.setattr(passions, "run_passions_step", fake_passions_step)
    monkeypatch.setattr(harvest, "run_nightly_disclosure_harvest", fake_harvest)
    return nc


def test_the_pick_is_the_last_step_of_the_night(monkeypatch):
    """After the disclosure harvest, so it can see the journal page the
    night just wrote. The real model call is handed in, the clock is the
    wall clock (the pick's day is today's, not the day reflected on), and
    the result rides result["tonight"]."""
    calls: list = []
    nc = _stub_the_night(monkeypatch, calls)
    before = time.time()

    async def fake_pick(**kw):
        calls.append("tonight")
        assert kw["owner_id"] == 1
        assert kw["llm_call"] is tonight.tonight_pick_llm_call
        assert before <= kw["now"] <= time.time()
        return {"day": "2026-09-29", "picked": "stake:42"}

    monkeypatch.setattr(tonight, "run_nightly_pick", fake_pick)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))
    assert calls == ["reflect", "consolidation", "narrative", "passions",
                     "disclosure_harvest", "tonight"]
    assert result["tonight"] == {"day": "2026-09-29", "picked": "stake:42"}


def test_a_failed_pick_never_costs_the_night(monkeypatch):
    calls: list = []
    nc = _stub_the_night(monkeypatch, calls)

    async def boom(**kw):
        raise RuntimeError("pick exploded")

    monkeypatch.setattr(tonight, "run_nightly_pick", boom)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))
    assert result["wrote"] == "2026-09-28"
    assert result["disclosure_harvest"] == {"faded": 0, "planted": None}
    assert result["tonight"] == {"error": "pick exploded"}


def test_a_cancelled_pick_cancels_the_night(monkeypatch):
    nc = _stub_the_night(monkeypatch, [])

    async def cancelled(**kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(tonight, "run_nightly_pick", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))


def test_the_real_step_is_dark_in_the_night_while_the_flag_is_off(monkeypatch, folder):
    """The suite drives _run_once for real in a dozen places: with the
    organ pinned off, the step reads nothing, calls nothing, writes nothing."""
    nc = _stub_the_night(monkeypatch, [])

    async def must_not_call(prompt):
        raise AssertionError("a model call was made with the organ off")

    monkeypatch.setattr(tonight, "tonight_pick_llm_call", must_not_call)
    result = asyncio.run(nc.NightCycleEngine()._run_once("2026-09-28"))
    assert result["tonight"] == {"skipped": "flag_off"}
    assert not folder.exists()


def test_the_health_line_names_a_broken_pick_and_not_a_quiet_one():
    """"No pick tonight" is this step's ordinary answer, so a broken one
    has to name itself or it reads like a run of quiet nights."""
    from ai.night_cycle import _surface_errors

    assert _surface_errors(
        {"wrote": "x", "tonight": {"day": "2026-09-29", "error": "unparseable"}}
    ) == "tonight: unparseable"
    assert _surface_errors(
        {"wrote": "x", "tonight": {"error": "pick exploded"}}
    ) == "tonight: pick exploded"
    assert _surface_errors(
        {"wrote": "x", "tonight": {"day": "2026-09-29", "picked": None, "refused": "not_offered"}}
    ) == "tonight refused: not_offered"
    for healthy in ({"day": "2026-09-29", "picked": None},
                    {"day": "2026-09-29", "picked": "journal:2026-09-27"},
                    {"skipped": "flag_off"},
                    {"skipped": "already_picked", "day": "2026-09-29"}):
        assert _surface_errors({"wrote": "x", "tonight": healthy}) == "", healthy
