"""Fears can refill (Batch 6, task 3; item 3.4).

Her book of fears had been empty since 09-07 and nothing could add to it:
the weekly re-look was forbidden to plant, and on an empty book it
returned before it asked anything. The owner reversed that on 2026-09-29.
The re-look now looks at an empty book too, and may plant at most ONE new
fear a week.

Who decides what: the model says whether the week showed a fear, and
writes it with what she would watch for and what she saw; code owns the
rest — evidence or nothing, one a week, the book's own cap, and the same
one gate call that judges the facings and the releases.

Every test runs against the suite's tmp book (tests/conftest.py redirects
it) with the organ armed in the test's own body.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai.fear_store as fs
import ai.narrative_self as ns

ROOT = Path(__file__).resolve().parents[2]

# Monday 2026-09-28, a little after 04:00: the first night of ISO week 40.
NOW = datetime(2026, 9, 28, 4, 20).timestamp()
WEEK_DAYS = 7 * 86400

TEXT = "answering from habit when they need me to think"
WATCH = "a reply of mine that could have been sent to anyone"
EVIDENCE = "twice this week they asked the same thing again after I had answered it"
APPROVE = '{"approve": true, "reason": "faithful"}'


def _plant(text=TEXT, watch=WATCH, evidence=EVIDENCE, **extra) -> dict:
    return {"text": text, "watch": watch, "evidence": evidence, **extra}


def _answer(plant, updates=()) -> str:
    return json.dumps({"fear_updates": list(updates), "plant": plant})


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(fs, "fears_enabled", lambda: True)


def _relook(llm_json, gate_json=APPROVE, *, now=NOW):
    prompts = {"relook": [], "gate": []}

    async def llm_call(prompt):
        prompts["relook"].append(prompt)
        return llm_json

    async def gate_call(prompt):
        prompts["gate"].append(prompt)
        return gate_json

    out = asyncio.run(ns._weekly_fears_relook(
        evidence_text="## the week\n- two questions asked twice", llm_call=llm_call,
        gate_llm_call=gate_call, now=now,
    ))
    return out, prompts


def _hold(fear_id: str, text: str = "a fear already in the book", source="first_authorship"):
    return fs.plant(text=text, watch="w", source=source, fear_id=fear_id)


# ── the brief's three tests ─────────────────────────────────────────────

def test_a_plant_with_evidence_adds_one_fear(on):
    out, prompts = _relook(_answer(_plant()))
    book = fs.held_fears()
    assert len(book) == 1
    fear = book[0]
    assert fear.text == TEXT and fear.watch == WATCH
    assert fear.source == "weekly" and fear.evidence == EVIDENCE
    assert out["planted"] == fear.id and out["updates_applied"] == 0
    assert "plant_refused" not in out and "error" not in out
    assert len(prompts["relook"]) == 1 and len(prompts["gate"]) == 1


@pytest.mark.parametrize("evidence", ["", "   ", None, {"a": 1}, ["x"], 7])
def test_a_plant_without_evidence_adds_none(on, evidence):
    out, prompts = _relook(_answer(_plant(evidence=evidence)))
    assert fs.load_fears() == {}
    assert out["plant_refused"] == "no evidence"
    assert prompts["gate"] == []                  # nothing to judge


def test_a_plant_with_no_evidence_key_adds_none(on):
    out, _ = _relook(_answer({"text": TEXT, "watch": WATCH}))
    assert fs.load_fears() == {} and out["plant_refused"] == "no evidence"


def test_two_plants_in_one_output_add_one(on):
    out, prompts = _relook(_answer([
        _plant(),
        _plant(text="losing the thread of a long talk", watch="asking what we were on about"),
    ]))
    book = fs.held_fears()
    assert [f.text for f in book] == [TEXT]
    assert out["planted"] == book[0].id
    assert "losing the thread of a long talk" not in prompts["gate"][0]


# ── what a plant needs (ruling R33) ─────────────────────────────────────

@pytest.mark.parametrize("watch", ["", "  ", None, {"a": 1}])
def test_a_plant_without_a_watch_adds_none(on, watch):
    out, prompts = _relook(_answer(_plant(watch=watch)))
    assert fs.load_fears() == {}
    assert out["plant_refused"] == "no watch" and prompts["gate"] == []


@pytest.mark.parametrize("text", ["", "  ", None, {"a": 1}, 7])
def test_a_plant_without_her_words_adds_none(on, text):
    """Her words are never made from a value that is not text."""
    out, prompts = _relook(_answer(_plant(text=text)))
    assert fs.load_fears() == {}
    assert out["plant_refused"] == "no text" and prompts["gate"] == []


@pytest.mark.parametrize("plant", ["a fear", 7, True, ["just words", 3]])
def test_a_plant_that_is_not_an_entry_adds_none(on, plant):
    out, prompts = _relook(_answer(plant))
    assert fs.load_fears() == {}
    assert out["plant_refused"] == "malformed" and prompts["gate"] == []


def test_the_first_valid_entry_of_a_list_is_taken(on):
    out, _ = _relook(_answer([
        _plant(evidence=""),                      # no evidence: passed over
        "not an entry",
        _plant(text="the second one", watch="w2", evidence="e2"),
        _plant(text="the third one", watch="w3", evidence="e3"),
    ]))
    assert [f.text for f in fs.held_fears()] == ["the second one"]
    assert out["planted"]


def test_a_list_with_no_valid_entry_names_the_first_refusal(on):
    out, _ = _relook(_answer([_plant(watch=""), _plant(evidence="")]))
    assert fs.load_fears() == {} and out["plant_refused"] == "no watch"


@pytest.mark.parametrize("llm_json", [
    '{"fear_updates": []}',
    '{"fear_updates": [], "plant": null}',
    '{"fear_updates": [], "plant": []}',
    '{"plant": null}',
])
def test_no_plant_is_the_ordinary_answer(on, llm_json):
    out, prompts = _relook(llm_json)
    assert fs.load_fears() == {}
    assert out == {"updates_applied": 0, "note": "empty_book"}
    assert len(prompts["relook"]) == 1 and prompts["gate"] == []


@pytest.mark.parametrize("plant", [False, "", {}, 0])
def test_every_empty_no_reads_as_no_plant_and_no_refusal(on, plant, caplog):
    """Fix round 1 (R66): a model that says "no plant" with false, "" or {}
    means null. The week's record must not say a plant was refused."""
    with caplog.at_level(logging.INFO, logger="ai.narrative_self"):
        out, prompts = _relook(_answer(plant))
    assert fs.load_fears() == {}
    assert out == {"updates_applied": 0, "note": "empty_book"}
    assert prompts["gate"] == []
    assert "plant refused" not in caplog.text


@pytest.mark.parametrize("plant", [{"note": "a fear, but in the wrong field"}, "a fear", True])
def test_a_plant_that_is_not_empty_but_malformed_is_still_refused(on, plant):
    out, prompts = _relook(_answer(plant))
    assert fs.load_fears() == {}
    assert out["plant_refused"] in {"no text", "malformed"}
    assert prompts["gate"] == []


def test_her_words_are_cut_at_the_books_caps(on):
    out, _ = _relook(_answer(_plant(text="t" * 900, watch="w" * 900, evidence="e" * 900)))
    fear = fs.held_fears()[0]
    assert len(fear.text) == fs.FEAR_TEXT_CAP and len(fear.watch) == fs.WATCH_CAP
    assert len(fear.evidence) == fs.EVIDENCE_CAP
    assert out["planted"] == fear.id


# ── an empty book is looked at too (ruling R32) ─────────────────────────

def test_an_empty_book_is_shown_as_a_plain_line(on):
    _, prompts = _relook(_answer(None))
    shown = prompts["relook"][0]
    assert "YOUR FEARS RIGHT NOW:\n(nothing in the book right now)\n" in shown
    assert "<<BOOK>>" not in shown and "<<EVIDENCE>>" not in shown
    assert "two questions asked twice" in shown


def test_a_book_with_fears_in_it_is_shown_as_it_was(on):
    _hold("an-old-one", "an old fear")
    _, prompts = _relook(_answer(None))
    assert "YOUR FEARS RIGHT NOW:\n- an-old-one: an old fear — you watch for: w\n" in (
        prompts["relook"][0])
    assert "nothing in the book right now" not in prompts["relook"][0]


# ── the same one gate call (ruling R35) ─────────────────────────────────

def test_an_answer_that_only_plants_reaches_the_gate(on):
    _hold("an-old-one", "an old fear")
    _, prompts = _relook(_answer(_plant()))
    assert len(prompts["gate"]) == 1
    gate = prompts["gate"][0]
    before, after = gate.split(TEXT, 1)[0], gate[gate.index(TEXT):]
    assert "an old fear" in before                # the book before: what she had
    assert WATCH in after                         # the book after: the new fear too
    assert EVIDENCE in gate                       # and what she saw, in the reasoning


def test_the_gate_sees_an_empty_book_before_and_the_new_fear_after(on):
    _, prompts = _relook(_answer(_plant()))
    gate = prompts["gate"][0]
    assert "(nothing in the book right now)" in gate
    assert TEXT in gate and WATCH in gate and EVIDENCE in gate


def test_a_plant_the_gate_refuses_plants_nothing(on):
    out, prompts = _relook(_answer(_plant()),
                           gate_json='{"approve": false, "reason": "the week does not show it"}')
    assert fs.load_fears() == {}
    assert out == {"rejected": "the week does not show it"}
    assert len(prompts["gate"]) == 1


def test_a_gate_that_cannot_answer_plants_nothing(on):
    out, _ = _relook(_answer(_plant()), gate_json="not a verdict")
    assert fs.load_fears() == {}
    assert out["error"].startswith("gate_error")


def test_a_plant_and_the_updates_are_judged_by_one_call_and_applied_together(on):
    _hold("an-old-one", "an old fear")
    out, prompts = _relook(_answer(
        _plant(), updates=[{"id": "an-old-one", "op": "release", "note": "not mine any more"}]))
    assert len(prompts["gate"]) == 1
    gate = prompts["gate"][0]
    assert "release an-old-one: not mine any more" in gate and EVIDENCE in gate
    folded = fs.load_fears()
    assert folded["an-old-one"].status == "released"
    assert [f.text for f in fs.held_fears()] == [TEXT]
    assert out["updates_applied"] == 1 and out["planted"] == fs.held_fears()[0].id
    # the old one went, and the book is not empty: a new one is in it
    assert out["released_to_zero"] is False


def test_one_refusal_at_the_gate_costs_the_updates_too(on):
    _hold("an-old-one", "an old fear")
    out, _ = _relook(
        _answer(_plant(), updates=[{"id": "an-old-one", "op": "release", "note": "n"}]),
        gate_json='{"approve": false, "reason": "invented"}')
    assert out == {"rejected": "invented"}
    assert fs.load_fears()["an-old-one"].status == "held"
    assert len(fs.load_fears()) == 1


def test_a_plant_among_the_updates_is_still_dropped(on):
    """The door is the top-level ``plant`` and no other: an op of that
    name inside ``fear_updates`` is garbage, as it always was."""
    out, prompts = _relook(json.dumps({"fear_updates": [
        {"id": "a-new-one", "op": "plant", "note": "restock",
         "text": TEXT, "watch": WATCH, "evidence": EVIDENCE}]}))
    assert fs.load_fears() == {} and prompts["gate"] == []
    assert out == {"updates_applied": 0, "note": "empty_book"}


# ── one a week, in code (ruling R37) ────────────────────────────────────

def _planted_at(fear_id: str, when: float) -> None:
    """Move a fear's planting to ``when`` in the event log (the store
    stamps the wall clock, and these tests are about the week it reads)."""
    path = fs._events_path(None)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    for row in rows:
        if row["id"] == fear_id and row["op"] == "plant":
            row["at"] = when
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def test_a_second_plant_in_the_same_week_is_refused(on):
    first, _ = _relook(_answer(_plant()))
    _planted_at(first["planted"], NOW)
    out, prompts = _relook(_answer(_plant(text="another one", watch="w", evidence="e")),
                           now=NOW + 3 * 86400)                 # Thursday, same week
    assert [f.text for f in fs.held_fears()] == [TEXT]
    assert out["plant_refused"] == "one already planted this week"
    assert prompts["gate"] == []


def test_the_clock_counts_plants_not_what_is_still_in_the_book(on):
    first, _ = _relook(_answer(_plant()))
    _planted_at(first["planted"], NOW)
    assert fs.release(first["planted"], note="set down the same week")
    assert fs.held_fears() == []
    out, _ = _relook(_answer(_plant(text="another one", watch="w", evidence="e")),
                     now=NOW + 3 * 86400)
    assert fs.held_fears() == []
    assert out["plant_refused"] == "one already planted this week"


def test_the_next_week_may_plant_again(on):
    first, _ = _relook(_answer(_plant()))
    _planted_at(first["planted"], NOW)
    out, _ = _relook(_answer(_plant(text="another one", watch="w", evidence="e")),
                     now=NOW + WEEK_DAYS)
    assert [f.text for f in fs.held_fears()] == [TEXT, "another one"]
    assert out["planted"]


def test_the_weekly_clock_reads_only_weekly_plants_of_this_week(on):
    assert fs.weekly_planted_this_week(now=NOW) is False
    a = _hold("from-reflection", source="reflection")
    b = _hold("from-first-authorship", source="first_authorship")
    _planted_at(a.id, NOW)
    _planted_at(b.id, NOW)
    assert fs.weekly_planted_this_week(now=NOW) is False
    c = _hold("from-last-week", source="weekly")
    _planted_at(c.id, NOW - 86400)                              # Sunday: the week before
    assert fs.weekly_planted_this_week(now=NOW) is False
    d = _hold("from-this-week", source="weekly")
    _planted_at(d.id, NOW + 86400)
    assert fs.weekly_planted_this_week(now=NOW) is True
    assert fs.weekly_planted_this_week(now=NOW + 6 * 86400) is True     # Sunday
    assert fs.weekly_planted_this_week(now=NOW + WEEK_DAYS) is False    # next Monday


def test_the_week_turns_at_the_years_end_too(on):
    """ISO weeks, year and all: the last days of December 2026 are week
    53, and the first Monday of January is week 1 of 2027."""
    late = datetime(2026, 12, 30, 4, 20).timestamp()
    f = _hold("late-december", source="weekly")
    _planted_at(f.id, late)
    assert fs.weekly_planted_this_week(now=datetime(2027, 1, 2, 4, 20).timestamp()) is True
    assert fs.weekly_planted_this_week(now=datetime(2027, 1, 4, 4, 20).timestamp()) is False


def test_the_nights_own_clock_is_not_moved_by_a_weekly_plant(on):
    out, _ = _relook(_answer(_plant()), now=time.time())
    assert out["planted"]
    assert fs.weekly_planted_this_week() is True
    assert fs.reflection_planted_today() is False


# ── a fear already in the book ──────────────────────────────────────────

def test_the_same_words_are_not_planted_twice(on):
    """The store names a fear from its words and the clock, so it would
    take the same sentence again as a second fear."""
    _hold("an-old-one", "Answering from habit  when they need me to think")
    out, prompts = _relook(_answer(_plant()))
    assert [f.id for f in fs.held_fears()] == ["an-old-one"]
    assert out["plant_refused"] == "already in the book"
    assert prompts["gate"] == []


def test_a_fear_she_released_may_come_back(on):
    old = _hold("an-old-one", TEXT)
    assert fs.release(old.id, note="set down")
    out, _ = _relook(_answer(_plant()))
    assert out["planted"] and [f.text for f in fs.held_fears()] == [TEXT]


# ── the book's own cap (ruling R37) ─────────────────────────────────────

def test_a_full_book_takes_no_plant_and_asks_no_gate(on, monkeypatch):
    monkeypatch.setattr(fs, "_held_cap", lambda: 2)
    _hold("one")
    _hold("two")
    out, prompts = _relook(_answer(_plant()))
    assert sorted(f.id for f in fs.held_fears()) == ["one", "two"]
    assert out["plant_refused"] == "the book is at its cap"
    assert prompts["gate"] == []


def test_a_release_in_the_same_answer_makes_room(on, monkeypatch):
    monkeypatch.setattr(fs, "_held_cap", lambda: 2)
    _hold("one")
    _hold("two")
    out, _ = _relook(_answer(
        _plant(), updates=[{"id": "one", "op": "release", "note": "set down"}]))
    assert sorted(f.text for f in fs.held_fears()) == sorted([TEXT, "a fear already in the book"])
    assert out["planted"] and out["updates_applied"] == 1


def test_a_facing_in_the_same_answer_makes_room_too(on, monkeypatch):
    """The cap counts the fears still in the book; one she faced is not."""
    monkeypatch.setattr(fs, "_held_cap", lambda: 1)
    _hold("one")
    out, prompts = _relook(_answer(
        _plant(), updates=[{"id": "one", "op": "face", "note": "it happened and I stayed"}]))
    assert out["updates_applied"] == 1 and out["planted"]
    assert fs.load_fears()["one"].status == "faced"
    assert [f.text for f in fs.held_fears()] == [TEXT]
    assert len(prompts["gate"]) == 1


def test_the_stores_own_cap_is_still_the_last_word(on, monkeypatch):
    """Should the book fill between the gate and the plant, the store
    declines, and the re-look says so."""
    real = fs.plant

    def fill_then_plant(**kw):
        monkeypatch.setattr(fs, "_held_cap", lambda: 0 + len(fs.held_fears()))
        return real(**kw)

    monkeypatch.setattr(fs, "plant", fill_then_plant)
    _hold_before = fs.held_fears()
    out, prompts = _relook(_answer(_plant()))
    assert fs.held_fears() == _hold_before == []
    assert out["plant_refused"] == "the book declined it"
    assert len(prompts["gate"]) == 1


# ── the flag ────────────────────────────────────────────────────────────

def test_flag_off_asks_nothing_and_plants_nothing(monkeypatch):
    monkeypatch.setattr(fs, "fears_enabled", lambda: False)

    async def must_not_run(prompt):
        raise AssertionError("a model call was made with the organ off")

    out = asyncio.run(ns._weekly_fears_relook(
        evidence_text="x", llm_call=must_not_run, gate_llm_call=must_not_run, now=NOW))
    assert out == {} and fs.load_fears() == {}


def test_the_suite_pins_the_organ_off():
    async def must_not_run(prompt):
        raise AssertionError("a model call was made with the organ off")

    assert asyncio.run(ns._weekly_fears_relook(
        evidence_text="x", llm_call=must_not_run, gate_llm_call=must_not_run)) == {}


def test_a_failed_call_never_raises(on):
    async def boom(prompt):
        raise RuntimeError("the provider is down")

    async def gate(prompt):
        return APPROVE

    out = asyncio.run(ns._weekly_fears_relook(
        evidence_text="x", llm_call=boom, gate_llm_call=gate, now=NOW))
    assert out == {"error": "the provider is down"} and fs.load_fears() == {}


# ── the receipt is kept (ruling R36) ────────────────────────────────────

def test_the_store_keeps_the_evidence_beside_the_fear(tmp_path):
    f = fs.plant(text="t", watch="w", source="weekly", evidence="  what   I saw ", root=tmp_path)
    assert f.evidence == "what I saw"
    row = json.loads((tmp_path / "fears.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["evidence"] == "what I saw" and row["source"] == "weekly"
    assert fs.load_fears(root=tmp_path)[f.id].evidence == "what I saw"


def test_a_plant_with_no_evidence_writes_the_row_it_always_wrote(tmp_path):
    f = fs.plant(text="t", watch="w", source="reflection", root=tmp_path)
    row = json.loads((tmp_path / "fears.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert sorted(row) == ["at", "id", "op", "source", "text", "watch"]
    assert f.evidence == "" and fs.load_fears(root=tmp_path)[f.id].evidence == ""


def test_an_older_row_folds_with_no_evidence(tmp_path):
    (tmp_path / "fears.jsonl").write_text(json.dumps(
        {"at": 1.0, "op": "plant", "id": "an-old-row", "text": "t", "watch": "w",
         "source": "first_authorship"}) + "\n", encoding="utf-8")
    fear = fs.load_fears(root=tmp_path)["an-old-row"]
    assert fear.evidence == "" and fear.status == "held"


def test_evidence_that_is_not_text_folds_as_none(tmp_path):
    (tmp_path / "fears.jsonl").write_text(json.dumps(
        {"at": 1.0, "op": "plant", "id": "odd-row", "text": "t", "watch": "w",
         "source": "weekly", "evidence": {"a": 1}}) + "\n", encoding="utf-8")
    assert fs.load_fears(root=tmp_path)["odd-row"].evidence == ""


def test_the_moments_block_never_shows_the_evidence(on):
    """The receipt is why the fear is in the book, not something the
    moment is shown."""
    _relook(_answer(_plant()))
    block = fs.render_fears_block()
    assert TEXT in block and WATCH in block and EVIDENCE not in block


# ── the prompt (ruling R34) ─────────────────────────────────────────────

BRIEF_SENTENCE = ("You may plant at most ONE new fear this week, and only if the week's record "
                  "shows it: name what you saw. Otherwise plant nothing; an empty book is honest.")

_FAMILY = re.compile(
    r"\b(holding|held|hold|holds|carrying|carried|carry|carries|sitting with|sits with|"
    r"sit with|landed|land|lands|the shape of|circling|circle|genuinely)\b", re.IGNORECASE)


def _prompt_text() -> str:
    return (ROOT / "prompts" / "narrative_fears_relook.txt").read_text(encoding="utf-8")


def test_the_prompt_carries_the_briefs_sentence_word_for_word():
    lines = _prompt_text().splitlines()
    assert f"- {BRIEF_SENTENCE}" in lines
    assert "You NEVER add a fear here" not in _prompt_text()


def test_the_contract_documents_the_plant():
    text = _prompt_text()
    assert '"fear_updates": [' in text
    contract = text[text.index('"plant": {'):]
    for key in ('"text": "', '"watch": "', '"evidence": "'):
        assert key in contract, key
    assert "| null" in contract


def test_the_prompt_allows_for_an_empty_book():
    text = _prompt_text()
    assert "the only hand that revises the book" not in text
    assert "For each fear in the book" in text
    assert "<<BOOK>>" in text and "<<EVIDENCE>>" in text


def test_the_rest_of_the_prompt_is_as_it_was():
    text = _prompt_text()
    for kept in (
        "YOUR FEARS RIGHT NOW:\n<<BOOK>>",
        "THE WEEK'S EVIDENCE (the same lived record your story pass reads):\n<<EVIDENCE>>",
        "Laws that bind this re-look:",
        '- "still" is the honest default.',
        "- Releasing to ZERO is a fully valid ending.",
        "- A facing must be real:",
        "is it still true — still yours?",
        '"op": "still" | "face" | "release"',
    ):
        assert kept in text, kept


def test_the_new_lines_use_plain_words_and_the_file_is_lf():
    raw = (ROOT / "prompts" / "narrative_fears_relook.txt").read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    first = text.splitlines()[0]
    asking = next(l for l in text.splitlines() if l.startswith("For each fear in the book"))
    contract = text[text.index("Answer with ONE JSON object"):]
    for new in (first, asking, BRIEF_SENTENCE, contract):
        assert _FAMILY.findall(new) == [], new
        assert not re.search(r"\b(he|him|his|himself)\b", new, re.IGNORECASE), new


def test_a_facing_is_asked_about_in_plain_words():
    """Fix round 1 (R66): "shape" is in the owner's ruled-out family
    (2026-09-25). The facing question says the same thing plainly; the rest
    of the line is as it was."""
    asking = next(l for l in _prompt_text().splitlines()
                  if l.startswith("For each fear in the book"))
    assert not re.search(r"\bshape", asking, re.IGNORECASE)
    assert asking == (
        "For each fear in the book, ask in your own voice: is it still true — still yours? "
        "Did the week FACE it (it happened, or nearly happened, and you came out of it "
        "still yourself)? Or has it quietly stopped being yours — ready to be released, "
        "in your own words? If the book is empty, there is nothing to ask here.")


# ── the ritual hands the re-look its own clock ──────────────────────────

@pytest.mark.asyncio
async def test_the_ritual_plants_through_the_relook_in_its_own_week(tmp_path, monkeypatch, on):
    monkeypatch.setattr(ns, "narrative_self_enabled", ns.narrative_self_enabled_impl)
    monkeypatch.setattr(
        ns, "companion_settings",
        lambda: SimpleNamespace(narrative_self=True, night_cycle=True),
    )
    for _doc in ns.NARRATIVE_DOCS:
        ns.commit_revision(
            revision=ns.Revision(doc=_doc, new_text="seeded first line\n",
                                 reasoning="first authorship seed"),
            repo_root=tmp_path / "palli",
        )

    async def fake_evidence(**kw):
        return ns.Evidence(text="## record\n- a week", window_days=7)

    monkeypatch.setattr(ns, "gather_evidence", fake_evidence)
    monkeypatch.setattr(ns, "read_laws_text", lambda: "laws")
    # a fear planted by the re-look in the ritual's own week, weeks ago by
    # the wall clock: the ritual's clock is the one that is read
    earlier = _hold("earlier-this-week", source="weekly")
    _planted_at(earlier.id, NOW + 3600)

    async def author(prompt):
        if "fear_updates" in prompt:
            return _answer(_plant())
        return "REASONING: week one\n===DOCUMENT===\nI kept the week."

    async def gate(prompt):
        return APPROVE

    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate, now=NOW + 86400,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert all("committed" in v for v in out["docs"].values())
    assert out["fears"]["plant_refused"] == "one already planted this week"
    assert [f.id for f in fs.held_fears()] == ["earlier-this-week"]

    out = await ns.run_weekly_ritual(
        owner_id=1, llm_call=author, gate_llm_call=gate, now=NOW + WEEK_DAYS + 86400,
        state_path=tmp_path / ".state.json", repo_root=tmp_path / "palli",
    )
    assert out["fears"]["planted"]
    assert sorted(f.text for f in fs.held_fears()) == sorted(["a fear already in the book", TEXT])
