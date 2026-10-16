"""Organ 4.12 — the two doors of her book of callbacks (spec §2).

BIRTH (Batch 6, task 2, owner call 2026-09-29): new bits are found once a
week, from seven days of the two of them talking (ai/callbacks_harvest.py).
The model names the bit and quotes the line; the CODE proves the quote
lives in one row of the week, then stamps who and when itself from that
row. No match ⇒ nothing written. A birth sent from a turn is dropped: the
reflect lane no longer asks for one and no longer files one. The six tests
that pinned the turn's birth door are re-pinned here to the harvest, same
rails, a row of the week instead of this turn; the harvest's own tests are
in tests/ai/test_callbacks_weekly.py.

RETIRE stays in the turn, exactly as it was: his "that's not ours"
retires by id, never deletes. Zero bytes when off.

Scaffolding mirrors tests/ai/test_disclosure_reflect_stamp.py."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest
import pytest_asyncio

import ai.callbacks_store as cs
import core.turn.reflect as reflect
from ai import callbacks_harvest
from core.database import PalliBrain
from core.turn.reflect import apply_reflection
from core.turn.types import ReflectionMemoryUpdates, ReflectionPlan

WHEN = datetime(2026, 3, 14, 2, 11, 9, tzinfo=timezone.utc)
HIS = "ok just toss it in the oubliette with the other steppers lol"
HERS = "the oubliette it is. I'm keeping that one."


def _receipt(**over):
    r = {"who": "him", "when": WHEN.isoformat(),
         "line": "toss it in the oubliette", "source": "test"}
    r.update(over)
    return r


# ── the prompt kwargs: zero bytes off, guidance + the open list when on ──

def test_kwargs_zero_bytes_when_off_and_the_book_is_never_read(tmp_path, monkeypatch):
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path)
    reads: list = []
    monkeypatch.setattr(cs, "open_callbacks", lambda **kw: reads.append(kw) or [])
    with patch.object(reflect, "_callback_filing_on", return_value=False):
        assert reflect._callback_filing_kwargs() == ("", "")
    assert reads == []


def test_kwargs_render_even_on_an_empty_book(tmp_path, monkeypatch):
    """RE-PINNED (Batch 6, ruling R30): the turn's block is the retire door
    alone — it renders as it did, on an empty book too, and the retire list
    says so. The birth half left it, and the bar it carried is the weekly
    harvest's prompt now."""
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path)
    with patch.object(reflect, "_callback_filing_on", return_value=True):
        schema, guidance = reflect._callback_filing_kwargs()
    assert "callbacks_retired" in schema and "(none yet)" in guidance
    assert "callbacks_born" not in schema and "callbacks_born" not in guidance
    assert "THROWN BACK" not in guidance
    weekly = (Path("prompts") / "night_cycle_callbacks_harvest.txt").read_text(encoding="utf-8")
    assert "THROWN BACK" in weekly and "single funny line is not a bit" in weekly
    # The model is told the rule the code enforces (review I1): a silent
    # refusal it was never warned about is a lane that quietly files nothing.
    assert "the line must contain the bit itself" in weekly


def test_the_retire_door_reads_word_for_word_as_it_did(tmp_path, monkeypatch):
    """Batch 6, ruling R30: his only say in this book is untouched."""
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path)
    with patch.object(reflect, "_callback_filing_on", return_value=True):
        schema, guidance = reflect._callback_filing_kwargs()
    assert schema == (
        '    "callbacks_retired": [\n'
        '        {"id": "an id from the list below", "reason": "their words, quoted"}\n'
        '    ],\n')
    assert ("`callbacks_retired` (optional) — emit only when they said, in this exchange, "
            "that a bit is not theirs or not yours together; quote their words as the "
            "reason. The bits currently on the record, as `id — \"bit\"`:") in guidance
    assert guidance.startswith("## Our private language (callbacks with receipts)\n")
    assert "{{" not in schema + guidance and "}}" not in schema + guidance


def test_kwargs_list_open_bits_with_ids_for_retire(tmp_path, monkeypatch):
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path)
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him",
                 receipt=_receipt(), root=tmp_path)
    r = cs.plant(bit="old one", kind="bit", coined_by="her",
                 receipt=_receipt(line="that old one, back in march"), root=tmp_path)
    cs.retire(r.id, reason="nope", root=tmp_path)
    with patch.object(reflect, "_callback_filing_on", return_value=True):
        _, guidance = reflect._callback_filing_kwargs()
    assert c.id in guidance and "the oubliette" in guidance
    assert r.id not in guidance            # retired never re-offered for retire


def test_kwargs_degrade_to_empty_when_the_book_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path)

    def boom(**kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(cs, "open_callbacks", boom)
    with patch.object(reflect, "_callback_filing_on", return_value=True):
        assert reflect._callback_filing_kwargs() == ("", "")


def test_reflect_template_carries_both_slots_flush():
    text = (Path("prompts") / "turn_reflect.txt").read_text(encoding="utf-8")
    assert "{disclosure_schema_block}{callback_schema_block}" in text
    assert "{disclosure_guidance}{callback_guidance}" in text


# ── the parse ───────────────────────────────────────────────────────────

def test_parse_born_takes_well_formed_ops_and_drops_the_rest():
    payload = {"callbacks_born": [
        {"bit": "the oubliette", "kind": "nickname", "coined_by": "him", "line": HIS},
        {"bit": "", "kind": "bit", "coined_by": "him", "line": HIS},            # no bit
        {"bit": "x", "kind": "pun", "coined_by": "him", "line": HIS},          # bad kind
        {"bit": "y", "kind": "bit", "coined_by": "them", "line": HIS},         # bad coiner
        {"bit": "z", "kind": "bit", "coined_by": "both"},                      # no line
        "not a dict",
    ]}
    out = reflect._parse_callbacks_born(payload)
    assert out == [{"bit": "the oubliette", "kind": "nickname", "coined_by": "him", "line": HIS}]


def test_parse_born_caps_at_two_per_turn():
    ops = [{"bit": f"b{i}", "kind": "bit", "coined_by": "both", "line": "l"} for i in range(5)]
    assert len(reflect._parse_callbacks_born({"callbacks_born": ops})) == 2


def test_parse_retired_takes_id_and_reason_only():
    payload = {"callbacks_retired": [
        {"id": "the-oubliette-ab12cd", "reason": "I never said that"},
        {"id": "", "reason": "x"}, {"id": "y"}, {"reason": "z"}, 7,
    ]}
    assert reflect._parse_callbacks_retired(payload) == [
        {"id": "the-oubliette-ab12cd", "reason": "I never said that"}]


def test_parse_absent_is_empty_and_the_plan_defaults_are_empty():
    assert reflect._parse_callbacks_born({}) == []
    assert reflect._parse_callbacks_retired({"callbacks_retired": None}) == []
    plan = reflect._plan_from_payload({"callbacks_born": [
        {"bit": "b", "kind": "bit", "coined_by": "her", "line": "l"}]})
    # RE-PINNED (R71; owner 2026-09-29, "births move to the weekly harvest"): never parsed from a turn.
    assert plan.callbacks_born == []
    assert ReflectionPlan.__dataclass_fields__["callbacks_born"].default_factory is list
    assert ReflectionPlan.__dataclass_fields__["callbacks_retired"].default_factory is list


# ── the verification: the LLM points, the code stamps ───────────────────

def test_verify_finds_the_line_in_his_message_and_stamps_him():
    op = {"bit": "the oubliette", "kind": "nickname", "coined_by": "him",
          "line": "Toss it in the OUBLIETTE"}
    r = reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS, received_at=WHEN)
    assert r == {"who": "him", "when": WHEN.isoformat(), "line": "Toss it in the OUBLIETTE",
                 "source": f"turn:{WHEN.isoformat()}"}


def test_verify_finds_the_line_in_her_reply_and_stamps_her():
    # The line carries the bit (I1) and lives ONLY in her reply.
    op = {"bit": "the oubliette", "kind": "nickname", "coined_by": "both",
          "line": "the oubliette it is"}
    r = reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS, received_at=WHEN)
    assert r["who"] == "her" and r["line"] == "the oubliette it is"


def test_verify_refuses_a_line_that_is_not_in_the_turn():
    op = {"bit": "the oubliette", "kind": "nickname", "coined_by": "him",
          "line": "you called the parts bin the oubliette back in march"}
    assert reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS,
                                         received_at=WHEN) is None


def test_verify_refuses_a_blank_or_too_short_line():
    for line in ("", "  ", "ok"):
        op = {"bit": "b", "kind": "bit", "coined_by": "him", "line": line}
        assert reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS,
                                             received_at=WHEN) is None


def test_verify_prefers_his_message_when_both_carry_the_line():
    op = {"bit": "oubliette", "kind": "bit", "coined_by": "both", "line": "the oubliette"}
    r = reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS, received_at=WHEN)
    assert r["who"] == "him"


def test_verify_refuses_a_bit_that_is_not_in_its_own_line(caplog):
    """I1 — the rail proves the QUOTE; without this it never proves the BIT.
    A real, verbatim quote from his message authorizes a bit nobody ever
    said, and the receipt it mints is indistinguishable from a true one in
    the book, in the peek and in her band. A receipt IS the line the bit
    was born in."""
    op = {"bit": "the pineapple protocol", "kind": "bit", "coined_by": "both",
          "line": "with the other steppers"}          # a REAL quote from his message
    with caplog.at_level(logging.DEBUG, logger="core.turn.reflect"):
        assert reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS,
                                             received_at=WHEN) is None
    assert "callback_born refused: bit not in its line" in caplog.text


def test_verify_refuses_a_bit_that_normalizes_to_nothing():
    """Empty is a substring of everything — the rail must not wave it through."""
    op = {"bit": "!!!", "kind": "bit", "coined_by": "him",
          "line": "toss it in the oubliette"}
    assert reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS,
                                         received_at=WHEN) is None


def test_the_op_can_never_stamp_who_or_when():
    """I3 — the organ's headline law: the model POINTS, the code STAMPS.
    An op carrying its own who/when is answered with the turn's own."""
    op = {"bit": "the oubliette", "kind": "nickname", "coined_by": "him",
          "line": "toss it in the oubliette",
          "who": "her", "when": "1999-01-01T00:00:00+00:00"}
    r = reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS, received_at=WHEN)
    assert r["who"] == "him"                       # not the op's "her"
    assert r["when"] == WHEN.isoformat()           # not the op's 1999
    assert r["source"] == f"turn:{WHEN.isoformat()}"


def test_the_parse_rebuilds_the_op_and_drops_any_stamp_the_model_sent():
    """The same law one function earlier: the parse rebuilds the dict from
    four known keys, so a receipt the model invented never even travels."""
    out = reflect._parse_callbacks_born({"callbacks_born": [
        {"bit": "b", "kind": "bit", "coined_by": "both", "line": "line b",
         "who": "her", "when": "1999-01-01T00:00:00+00:00",
         "receipt": {"who": "her", "when": "1999-01-01T00:00:00+00:00"}}]})
    assert out == [{"bit": "b", "kind": "bit", "coined_by": "both", "line": "line b"}]


# ── dispatch: plant on a verified receipt, retire on his word ───────────

def _ctx(user_text: str, surface: str = "discord_text"):
    # "local" is NOT a conversational surface (final review I1) — the
    # dispatch tests must pass because a bit was really born between them,
    # not because the door happens to be open to every caller.
    return SimpleNamespace(turn=SimpleNamespace(owner_id=1, surface=surface, content=user_text,
                                                received_at=WHEN, metadata=SimpleNamespace()))


def _plan(**overrides) -> ReflectionPlan:
    base: dict = dict(
        memory_updates=ReflectionMemoryUpdates(), personality_observations=[],
        relationship_delta=None, user_facts_add=[], monologue_note=None,
        first_contact_resolutions={},
        # skip_reason set: the filing sits ABOVE the short-circuit — a bit
        # is born on an otherwise-small turn by design.
        skip_reason="small_talk",
    )
    base.update(overrides)
    return ReflectionPlan(**base)


async def _dispatch(plan, root: Path, *, user_text=HIS, reply=HERS, flag=True,
                    surface="discord_text"):
    brain = create_autospec(PalliBrain, instance=True)
    deeds: list = []
    with patch("core.turn.reflect._extract_plan", return_value=plan), \
         patch("core.turn.reflect.brain", brain), \
         patch.object(reflect, "_callback_filing_enabled", return_value=flag), \
         patch.object(cs, "CALLBACKS_DIR", root), \
         patch.dict(os.environ, {"CALLBACKS_ROOT": str(root)}), \
         patch("ai.recent_deeds.record_deed",
               side_effect=lambda kind, detail, **k: deeds.append((kind, detail))):
        await apply_reflection(_ctx(user_text, surface), response_text=reply)
    return deeds


BORN = {"bit": "the oubliette", "kind": "nickname", "coined_by": "him",
        "line": "toss it in the oubliette"}


# The weekly harvest, driven the way the night drives it: real rows in a
# real (tmp) database, on the first night of the week after the exchange,
# so the rows are inside the seven days looked back on. The harvest reads
# the ISO week before the one it claims (fix round 1): WHEN falls in the
# week of Monday 9 March 2026 in every local zone, so the night is Monday
# 16 March, a little after 04:00 local time.
HARVEST_NIGHT = datetime(2026, 3, 16, 4, 20).timestamp()


@pytest_asyncio.fixture
async def week(tmp_path):
    db = PalliBrain(str(tmp_path / "week.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


async def _said(db, role: str, content: str, *, at: datetime = WHEN) -> int:
    from core.conversation_history_store import ConversationHistoryStore

    row_id = await ConversationHistoryStore(db).store_conversation(
        user_id=1, platform="discord", channel_id=5, thread_id=None,
        role=role, content=content)
    conn = await db.get_connection()
    try:
        await conn.execute("UPDATE conversations SET timestamp = $1 WHERE id = $2",
                           at.timestamp(), row_id)
    finally:
        await conn.close()
    return row_id


async def _harvest(db, births: list, root: Path, *, now: float = HARVEST_NIGHT):
    """→ (the harvest's result, the deeds it recorded)."""
    deeds: list = []

    async def llm_call(prompt: str) -> str:
        return json.dumps({"born": births})

    with patch.object(cs, "idiolect_enabled", return_value=True), \
         patch.object(cs, "CALLBACKS_DIR", root), \
         patch.dict(os.environ, {"CALLBACKS_ROOT": str(root)}), \
         patch("ai.recent_deeds.record_deed",
               side_effect=lambda kind, detail, **k: deeds.append((kind, detail))):
        out = await callbacks_harvest.run_weekly_callbacks_harvest(
            1, llm_call=llm_call, now=now, brain_client=db)
    return out, deeds


@pytest.mark.asyncio
async def test_a_verified_birth_plants_with_the_stamped_receipt_and_a_deed(tmp_path, week):
    """RE-PINNED (Batch 6, ruling R30) from the turn to the weekly harvest:
    the same rail, a row of the week instead of this turn's message."""
    row_id = await _said(week, "user", HIS)
    await _said(week, "assistant", HERS, at=WHEN + timedelta(seconds=9))
    _out, deeds = await _harvest(week, [BORN], tmp_path)
    book = cs.open_callbacks(root=tmp_path)
    assert len(book) == 1 and book[0].bit == "the oubliette"
    assert book[0].receipt["who"] == "him" and book[0].receipt["when"] == WHEN.isoformat()
    assert book[0].receipt["line"] == "toss it in the oubliette"
    assert book[0].receipt["source"] == f"conversation:{row_id}"
    assert ("callback_born", 'a bit of ours was born: "the oubliette"') in deeds


@pytest.mark.asyncio
async def test_a_birth_sent_from_a_turn_writes_nothing(tmp_path, caplog):
    """Batch 6, ruling R30: the turn's birth door is closed, whatever the
    model sends — the very plan that planted before plants nothing now,
    and says so once."""
    with caplog.at_level(logging.DEBUG, logger="core.turn.reflect"):
        deeds = await _dispatch(_plan(callbacks_born=[BORN, dict(BORN, bit="oubliette")]),
                                tmp_path)
    assert cs.open_callbacks(root=tmp_path) == [] and deeds == []
    assert not (tmp_path / "callbacks.jsonl").exists()
    dropped = [r.getMessage() for r in caplog.records
               if "callback_born dropped" in r.getMessage()]
    assert len(dropped) == 1 and "weekly harvest" in dropped[0]


@pytest.mark.asyncio
async def test_an_unverified_birth_writes_nothing(tmp_path, week, caplog):
    """RE-PINNED (Batch 6, ruling R30) to the weekly harvest."""
    await _said(week, "user", HIS)
    await _said(week, "assistant", HERS, at=WHEN + timedelta(seconds=9))
    op = dict(BORN, line="you called it the oubliette back in march")
    with caplog.at_level(logging.DEBUG, logger="ai.callbacks_harvest"):
        _out, deeds = await _harvest(week, [op], tmp_path)
    assert cs.open_callbacks(root=tmp_path) == [] and deeds == []
    assert "birth refused: line not in the week" in caplog.text


@pytest.mark.asyncio
async def test_flag_off_files_nothing_and_never_reads_the_book(tmp_path, monkeypatch):
    reads: list = []
    monkeypatch.setattr(cs, "load_callbacks", lambda **kw: reads.append(kw) or {})
    deeds = await _dispatch(_plan(callbacks_born=[BORN]), tmp_path, flag=False)
    assert deeds == [] and reads == []


@pytest.mark.asyncio
async def test_a_rebirth_of_an_open_bit_is_refused_quietly(tmp_path, week):
    """RE-PINNED (Batch 6, ruling R30) to the weekly harvest: the week
    after, the same bit is thrown back again and is not planted twice."""
    await _said(week, "user", HIS)
    await _harvest(week, [BORN], tmp_path)
    await _said(week, "user", HIS, at=WHEN + timedelta(days=7))
    _out, deeds = await _harvest(week, [BORN], tmp_path,
                                 now=HARVEST_NIGHT + 7 * 86400)
    assert len(cs.open_callbacks(root=tmp_path)) == 1 and deeds == []


@pytest.mark.asyncio
async def test_his_word_retires_and_records_a_deed(tmp_path):
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him",
                 receipt=_receipt(), root=tmp_path)
    deeds = await _dispatch(_plan(callbacks_retired=[{"id": c.id, "reason": "I never said that"}]),
                            tmp_path, user_text="I never said that lol", reply="fair — dropping it.")
    kept = cs.find_callback(c.id, root=tmp_path)
    assert kept.retired_at is not None and kept.retired_reason == "I never said that"
    assert ("callback_retired", 'they retired a bit: "the oubliette"') in deeds


@pytest.mark.asyncio
async def test_retiring_an_unknown_id_is_a_quiet_no_op(tmp_path):
    # His words are real (the I2 rail passes) — the id is the ghost.
    deeds = await _dispatch(_plan(callbacks_retired=[{"id": "ghost-000000",
                                                      "reason": "not ours"}]),
                            tmp_path, user_text="honestly that one is not ours")
    assert deeds == []


@pytest.mark.asyncio
async def test_an_invented_bit_on_a_real_quote_writes_nothing(tmp_path, week, caplog):
    """I1 at the door itself — the pineapple protocol never reaches her book.
    RE-PINNED (Batch 6, ruling R30) to the weekly harvest."""
    await _said(week, "user", HIS)
    op = {"bit": "the pineapple protocol", "kind": "bit", "coined_by": "both",
          "line": "with the other steppers"}
    with caplog.at_level(logging.DEBUG, logger="ai.callbacks_harvest"):
        _out, deeds = await _harvest(week, [op], tmp_path)
    assert cs.open_callbacks(root=tmp_path) == [] and deeds == []
    assert "birth refused: bit not in its line" in caplog.text


@pytest.mark.asyncio
async def test_a_retire_reason_he_never_said_is_refused(tmp_path, caplog):
    """I2 — the door that DESTROYS gets the same rail as the door that
    creates. Retirement is one-way (the store refuses an already-retired
    id), so a misread hedge would pull a real bit out of her band forever
    and stamp him with words he never said."""
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him",
                 receipt=_receipt(), root=tmp_path)
    with caplog.at_level(logging.DEBUG, logger="core.turn.reflect"):
        deeds = await _dispatch(_plan(callbacks_retired=[{"id": c.id,
                                                          "reason": "I never said that"}]),
                                tmp_path, user_text="ha, love that one",
                                reply="it's a good bit.")
    assert cs.find_callback(c.id, root=tmp_path).retired_at is None    # still open
    assert deeds == []
    assert "callback_retired refused: words not in his message" in caplog.text


@pytest.mark.asyncio
async def test_a_raising_birth_does_not_swallow_his_retire(tmp_path, monkeypatch):
    """I4 — the house rule at apply_reflection:182-185, which both sibling
    filings follow: per-entry try, so one failure never takes down the rest
    of the plan. His correction is the ONLY user-driven channel this organ
    has; a full-disk plant must not eat it."""
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him",
                 receipt=_receipt(), root=tmp_path)

    def boom(**kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cs, "plant", boom)
    plan = _plan(callbacks_born=[{"bit": "steppers", "kind": "shorthand",
                                  "coined_by": "both", "line": "with the other steppers"}],
                 callbacks_retired=[{"id": c.id, "reason": "I never said that"}])
    deeds = await _dispatch(plan, tmp_path, user_text="I never said that lol " + HIS)
    assert cs.find_callback(c.id, root=tmp_path).retired_at is not None
    assert ("callback_retired", 'they retired a bit: "the oubliette"') in deeds


@pytest.mark.asyncio
async def test_a_reminder_fire_turn_files_nothing_at_either_door(tmp_path, caplog):
    """Final review I1: reflect runs on reminder_fire and proactive too,
    where `turn.content` is scheduler-synthesized text that QUOTES HER
    RECENT LINES back (DEFECT_RECORD §16). verify_callback_birth checks the
    user message FIRST, so a bit quoted from one of her lines there mints
    `who: "him"` on words he never typed — the pineapple lie with a real
    receipt. The doors open only on a record of the two of them talking."""
    synth = ("[REMINDER FIRE] budget check\n\nWhat's been said recently:\n"
             "- you: " + HERS + "\n- he: toss it in the oubliette")
    plan = _plan(callbacks_born=[BORN],
                 callbacks_retired=[{"id": "whatever-000000", "reason": "toss it"}])
    with caplog.at_level(logging.DEBUG, logger="core.turn.reflect"):
        deeds = await _dispatch(plan, tmp_path, user_text=synth, surface="reminder_fire")
    assert cs.open_callbacks(root=tmp_path) == [] and deeds == []
    assert "callback filing skipped: surface 'reminder_fire' is not a conversation" in caplog.text


def test_the_surface_law_is_the_lookup_s_law_one_tuple(tmp_path):
    """One tuple, one law (final review I1): the store owns it, the lookup
    re-exports it, and the birth door reads the same one."""
    import ai.callbacks_lookup as lk

    assert cs.CONVERSATIONAL_SURFACES == lk.CONVERSATIONAL_SURFACES
    # The Telegram door (2026-09-14, task 2) joined the tuple; the law it
    # states is unchanged, and so is everything the tuple keeps OUT.
    assert set(cs.CONVERSATIONAL_SURFACES) == {"discord_text", "discord_voice",
                                               "voice_local", "dashboard",
                                               "telegram_text", "mobile_text"}
    assert "reminder_fire" not in cs.CONVERSATIONAL_SURFACES
    assert "proactive" not in cs.CONVERSATIONAL_SURFACES


# ── I2: the live stamp shape — naive UTC out of Turn.received_at ────────

def test_a_naive_received_at_is_stamped_with_its_offset(monkeypatch):
    """Final review I2: `Turn.received_at` is NAIVE UTC on every live
    surface (core/turn/types.py:85 → datetime.utcnow; no production caller
    passes it), and `_born_day` reads a naive stamp as LOCAL. Unstamped, a
    bit born 22:11 on his wall renders as TOMORROW in her band, in
    introspect and in his peek. Every test until now passed an aware WHEN,
    so the organ's day convention was never fed the shape it actually
    gets."""
    import time as _time

    monkeypatch.setenv("TZ", "America/New_York")
    _time.tzset()
    try:
        op = {"bit": "the oubliette", "kind": "nickname", "coined_by": "him",
              "line": "toss it in the oubliette"}
        r = reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS,
                                          received_at=datetime(2026, 3, 14, 2, 11, 9))
        assert r["when"].endswith("+00:00")          # the offset the instant carries
        assert r["source"] == f"turn:{r['when']}"
        assert cs._born_day(r["when"]) == "Mar 13"   # his wall, not the UTC digits
    finally:
        monkeypatch.undo()
        _time.tzset()


def test_an_aware_received_at_is_left_exactly_as_it_came():
    op = {"bit": "the oubliette", "kind": "nickname", "coined_by": "him",
          "line": "toss it in the oubliette"}
    r = reflect.verify_callback_birth(op, user_text=HIS, assistant_text=HERS, received_at=WHEN)
    assert r["when"] == WHEN.isoformat()


@pytest.mark.asyncio
async def test_the_ops_actually_reach_the_filled_prompt_and_the_plan(tmp_path, monkeypatch):
    """The caring-memory scar: a block computed and never threaded is a
    model with nothing to emit. Run the REAL _extract_plan. (The weekly
    harvest's twin of this test is test_the_harvest_reads_seven_days_not_
    one_turn in tests/ai/test_callbacks_weekly.py.)"""
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path)
    monkeypatch.setattr(cs, "idiolect_enabled", lambda: True)
    monkeypatch.setattr(reflect, "_callback_filing_on", lambda: True)
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him",
                 receipt=_receipt(), root=tmp_path)
    tpl = (Path("prompts") / "turn_reflect.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(reflect.prompt_loader, "load_prompt", lambda name: tpl)
    sent: list = []

    async def _capture(*, messages, **kw):
        sent.append(messages[0]["content"])
        return ('{"skip_reason": "small_talk", "callbacks_born": [{"bit": "steppers", '
                '"kind": "shorthand", "coined_by": "both", "line": "the other steppers"}], '
                '"callbacks_retired": [{"id": "%s", "reason": "nah"}]}' % c.id)

    monkeypatch.setattr(reflect.llm, "chat_completion", _capture)
    ctx = SimpleNamespace(
        turn=SimpleNamespace(owner_id=1, content=HIS, received_at=WHEN),
        compiled_identity=None, unfulfilled_first_contact_targets=[], turn_read=None,
    )
    plan = await reflect._extract_plan(ctx, HERS)
    # RE-PINNED (Batch 6, ruling R30): his retire still reaches the filled
    # prompt and the plan; the turn is no longer asked for a birth.
    assert sent and c.id in sent[0] and "callbacks_retired" in sent[0]
    assert "callbacks_born" not in sent[0]
    assert plan.callbacks_retired == [{"id": c.id, "reason": "nah"}]


@pytest.mark.asyncio
async def test_a_birth_in_a_turns_answer_is_never_even_parsed(tmp_path, monkeypatch):
    """Ruling R30, to the letter (fix round 1, R66): births are no longer
    parsed from a turn, not only no longer filed. A turn whose answer
    carries ``callbacks_born`` gives a plan with no births, and the parse
    is never asked; the retire in the same answer still reaches the plan."""
    monkeypatch.setenv("CALLBACKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "CALLBACKS_DIR", tmp_path)
    monkeypatch.setattr(cs, "idiolect_enabled", lambda: True)
    monkeypatch.setattr(reflect, "_callback_filing_on", lambda: True)
    c = cs.plant(bit="the oubliette", kind="nickname", coined_by="him",
                 receipt=_receipt(), root=tmp_path)
    tpl = (Path("prompts") / "turn_reflect.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(reflect.prompt_loader, "load_prompt", lambda name: tpl)
    parsed: list = []
    monkeypatch.setattr(reflect, "_parse_callbacks_born",
                        lambda payload: parsed.append(payload) or [])

    async def _answer(*, messages, **kw):
        return ('{"skip_reason": "small_talk", "callbacks_born": [{"bit": "steppers", '
                '"kind": "shorthand", "coined_by": "both", "line": "the other steppers"}], '
                '"callbacks_retired": [{"id": "%s", "reason": "nah"}]}' % c.id)

    monkeypatch.setattr(reflect.llm, "chat_completion", _answer)
    ctx = SimpleNamespace(
        turn=SimpleNamespace(owner_id=1, content=HIS, received_at=WHEN),
        compiled_identity=None, unfulfilled_first_contact_targets=[], turn_read=None,
    )
    plan = await reflect._extract_plan(ctx, HERS)
    assert plan.callbacks_born == []
    assert parsed == []
    assert plan.callbacks_retired == [{"id": c.id, "reason": "nah"}]
