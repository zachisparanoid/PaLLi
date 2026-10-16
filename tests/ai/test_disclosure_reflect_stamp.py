# tests/ai/test_disclosure_reflect_stamp.py
"""Organ 4.9 — door 1's other half: reflect stamps a truth SAID.

Spec §3/§5. Door 1 offers ≤2 unsaid truths into her reply prompt in lived
language — no ids, ever (the fourth wall). So the post-reply reflect lane
is where "said once, forever" is actually enforced: reflect is
MACHINE-facing, it gets the SAME two truths carrying their ids, and it
emits `disclosure_said: <id>` only when the reply genuinely shared one.

The defect this file pins shut: with no ids anywhere in the loop the model
has nothing to emit, `mark_said` refuses an invented id, and "said once"
rots silently into "offered until it fades" — a truth she would tell him
every day for two weeks and then lose.

The two lists MUST be one selection (`ds.offered_disclosures`). A block
listing a truth the band never offered is an id she could stamp said
without ever having said it.

Scaffolding mirrors tests/turn/test_reflection_fear_filing.py.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest

import ai.disclosure_store as ds
import core.turn.reflect as reflect
from core.database import PalliBrain
from core.turn.reflect import apply_reflection
from core.turn.types import ReflectionMemoryUpdates, ReflectionPlan


def _seed(root: Path, n: int) -> None:
    for i in range(n):
        ds.plant(text=f"small truth {i}",
                 referent={"source": "journal", "dates": "08-29"}, root=root)


# ── the ONE selection: band and block can never drift ───────────────────

def test_offered_is_the_two_oldest_unsaid(tmp_path: Path):
    _seed(tmp_path, 3)
    rows = ds.offered_disclosures(root=tmp_path)
    assert [d.text for d in rows] == ["small truth 0", "small truth 1"]


def test_offered_drops_a_said_one_and_lets_the_next_in(tmp_path: Path):
    _seed(tmp_path, 3)
    ds.mark_said(ds.offered_disclosures(root=tmp_path)[0].id, via="reply", root=tmp_path)
    assert [d.text for d in ds.offered_disclosures(root=tmp_path)] == [
        "small truth 1", "small truth 2",
    ]


def test_the_band_renders_the_offered_selection(tmp_path: Path, monkeypatch):
    """One function, two doors. If the band ever selects for itself again,
    reflect can be handed an id her prompt never carried — a truth stamped
    said that she never said."""
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    _seed(tmp_path, 3)
    seen: list = []

    real = ds.offered_disclosures

    def spy(**kw):
        rows = real(**kw)
        seen.append([d.id for d in rows])
        return rows

    monkeypatch.setattr(ds, "offered_disclosures", spy)
    band = ds.render_disclosures_band(root=tmp_path)
    assert seen, "the band did not read offered_disclosures — the two doors can drift"
    for d in ds.offered_disclosures(root=tmp_path):
        assert d.text in band


# ── the prompt kwargs: zero bytes off, ids when armed ───────────────────

def test_kwargs_zero_bytes_when_flag_off_and_the_book_is_never_read(tmp_path, monkeypatch):
    """Flag off is not "an empty block" — it is no store read at all, and
    a reflect prompt byte-identical to today's."""
    monkeypatch.setattr(ds, "DISCLOSURES_DIR", tmp_path)
    _seed(tmp_path, 2)
    reads: list = []
    monkeypatch.setattr(ds, "offered_disclosures",
                        lambda **kw: reads.append(kw) or [])
    with patch.object(reflect, "_disclosure_filing_on", return_value=False):
        schema, guidance = reflect._disclosure_filing_kwargs()
    assert schema == "" and guidance == ""
    assert reads == [], "the reflect path read her book with the organ dark"


def test_kwargs_zero_bytes_when_the_book_is_empty(tmp_path, monkeypatch):
    """An empty book is honest — and identical to the flag being off."""
    monkeypatch.setattr(ds, "DISCLOSURES_DIR", tmp_path)
    with patch.object(reflect, "_disclosure_filing_on", return_value=True):
        assert reflect._disclosure_filing_kwargs() == ("", "")


def test_kwargs_carry_the_ids_and_the_law_when_armed(tmp_path, monkeypatch):
    """The whole point of the ruling: the block is machine-facing, so it
    carries the ids her reply prompt deliberately withholds."""
    monkeypatch.setattr(ds, "DISCLOSURES_DIR", tmp_path)
    _seed(tmp_path, 3)
    offered = ds.offered_disclosures(root=tmp_path)
    with patch.object(reflect, "_disclosure_filing_on", return_value=True):
        schema, guidance = reflect._disclosure_filing_kwargs()

    assert "disclosure_said" in schema
    for d in offered:
        assert d.id in guidance and d.text in guidance
    # exactly the two offered — a third id is one the band never showed her
    third = ds.unsaid_disclosures(root=tmp_path)[2]
    assert third.id not in guidance
    # spec §3's law, in the block that can act on it
    assert "paraphrase" in guidance and "near-miss" in guidance
    assert "ONLY when" in guidance


def test_kwargs_degrade_to_empty_when_the_book_raises(tmp_path, monkeypatch):
    """The reflect lane must not lose a whole turn's memory writeback to a
    broken book — the band's law, one lane over."""
    monkeypatch.setattr(ds, "DISCLOSURES_DIR", tmp_path)

    def boom(**kw):
        raise RuntimeError("disk")

    monkeypatch.setattr(ds, "offered_disclosures", boom)
    with patch.object(reflect, "_disclosure_filing_on", return_value=True):
        assert reflect._disclosure_filing_kwargs() == ("", "")


def test_reflect_template_carries_both_slots_with_no_bytes_of_its_own():
    """Adjacency IS the zero-bytes-when-off proof: the two placeholders sit
    flush against their neighbours, so an empty render leaves today's exact
    prompt."""
    text = (Path("prompts") / "turn_reflect.txt").read_text(encoding="utf-8")
    assert "{fears_schema_block}{disclosure_schema_block}" in text
    assert "{fear_filing_guidance}{disclosure_guidance}" in text


_TEMPLATE_KEYS = (
    "user_message", "assistant_reply", "identity_excerpt",
    "memory_durable_context", "memory_active_threads", "relationship_state",
    "unfulfilled_first_contact_targets", "current_time_context",
    "active_open_threads", "quests_schema_block", "quest_filing_guidance",
    "corrections_schema_block", "corrections_guidance", "fears_schema_block",
    "fear_filing_guidance", "disclosure_schema_block", "disclosure_guidance",
    "callback_schema_block", "callback_guidance",
    # her person by name once known (2026-09-29): always filled by the reflection
    "memory_person_short", "memory_person_rule",
    # her inner read of the turn, out of the relationship block and into the
    # per-turn tail so the prefix caches (the owner, 10-05)
    "turn_inner_read",
)


def test_flag_off_renders_the_reflect_prompt_byte_for_byte():
    """Not "an empty section" — literally the prompt of a template that
    never heard of this organ."""
    tpl = (Path("prompts") / "turn_reflect.txt").read_text(encoding="utf-8")
    kw = {k: "" for k in _TEMPLATE_KEYS}
    without = (tpl.replace("{disclosure_schema_block}", "")
                  .replace("{disclosure_guidance}", "")
                  .replace("{callback_schema_block}", "")
                  .replace("{callback_guidance}", ""))
    assert tpl.format(**kw) == without.format(
        **{k: v for k, v in kw.items()
           if not (k.startswith("disclosure") or k.startswith("callback"))})


@pytest.mark.asyncio
async def test_the_ids_actually_reach_the_filled_prompt(tmp_path, monkeypatch):
    """The caring-memory scar, and the exact defect this task was ruled to
    fix: a block computed correctly and never threaded into format_kwargs
    is a model with no id to emit — mark_said then refuses every invented
    id and "said once" rots into "offered until it fades", silently, with
    every unit test green. So run the REAL _extract_plan and read the
    prompt that would have gone to the provider."""
    from datetime import datetime

    monkeypatch.setattr(ds, "DISCLOSURES_DIR", tmp_path)
    monkeypatch.setattr(ds, "vulnerability_enabled", lambda: True)
    # Task 8 pins reflect's own gate off suite-wide too (belt-and-braces
    # over the store's), so arming the store alone no longer arms this
    # lane in a test. That the gate really DOES delegate to the store is
    # proven where the pin cannot reach it — through the import-time
    # capture in tests/ai/test_vulnerability_flags.py.
    monkeypatch.setattr(reflect, "_disclosure_filing_on", lambda: True)
    _seed(tmp_path, 2)
    offered = ds.offered_disclosures(root=tmp_path)

    tpl = (Path("prompts") / "turn_reflect.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(reflect.prompt_loader, "load_prompt", lambda name: tpl)

    sent: list = []

    async def _capture(*, messages, **kw):
        sent.append(messages[0]["content"])
        return '{"skip_reason": "small_talk", "disclosure_said": "%s"}' % offered[0].id

    monkeypatch.setattr(reflect.llm, "chat_completion", _capture)

    ctx = SimpleNamespace(
        turn=SimpleNamespace(owner_id=1, content="rough day",
                             received_at=datetime(2026, 9, 1, 3, 0, 0)),
        compiled_identity=None,
        unfulfilled_first_contact_targets=[],
        turn_read=None,
    )
    plan = await reflect._extract_plan(ctx, "…yeah. Something like that.")

    assert sent, "the reflect lane never called the provider"
    for d in offered:
        assert d.id in sent[0] and d.text in sent[0]
    assert plan is not None and plan.disclosure_said == offered[0].id


# ── the parse ───────────────────────────────────────────────────────────

def test_parse_takes_the_id():
    assert reflect._parse_disclosure_said(
        {"disclosure_said": "  a-small-truth-ab12cd  "}) == "a-small-truth-ab12cd"


def test_parse_absent_or_null_is_empty():
    assert reflect._parse_disclosure_said({}) == ""
    assert reflect._parse_disclosure_said({"disclosure_said": None}) == ""
    assert reflect._parse_disclosure_said({"disclosure_said": ""}) == ""


def test_plan_carries_the_field_from_the_payload():
    plan = reflect._plan_from_payload({"disclosure_said": "some-id-ab12cd"})
    assert plan.disclosure_said == "some-id-ab12cd"
    assert ReflectionPlan.__dataclass_fields__["disclosure_said"].default == ""


# ── dispatch: the stamp itself ──────────────────────────────────────────

def _ctx_for_owner(owner_id: int):
    return SimpleNamespace(
        turn=SimpleNamespace(
            owner_id=owner_id,
            surface="local",
            content="",
            metadata=SimpleNamespace(),
        ),
    )


def _plan(**overrides) -> ReflectionPlan:
    base: dict = dict(
        memory_updates=ReflectionMemoryUpdates(),
        personality_observations=[],
        relationship_delta=None,
        user_facts_add=[],
        monologue_note=None,
        first_contact_resolutions={},
        # skip_reason set: the stamp must sit ABOVE the short-circuit — a
        # small truth rides an otherwise-unremarkable turn by design.
        skip_reason="small_talk",
    )
    base.update(overrides)
    return ReflectionPlan(**base)


async def _dispatch(plan, root: Path, *, flag: bool = True):
    brain = create_autospec(PalliBrain, instance=True)
    deeds: list = []
    with patch("core.turn.reflect._extract_plan", return_value=plan), \
         patch("core.turn.reflect.brain", brain), \
         patch.object(reflect, "_disclosure_filing_enabled", return_value=flag), \
         patch.object(ds, "DISCLOSURES_DIR", root), \
         patch("ai.recent_deeds.record_deed",
               side_effect=lambda kind, detail, **k: deeds.append((kind, detail))):
        await apply_reflection(_ctx_for_owner(1), response_text="...")
    return deeds


@pytest.mark.asyncio
async def test_a_shared_truth_is_stamped_said_with_a_deed(tmp_path):
    _seed(tmp_path, 2)
    d = ds.offered_disclosures(root=tmp_path)[0]
    deeds = await _dispatch(_plan(disclosure_said=d.id), tmp_path, flag=True)

    row = ds.load_disclosures(root=tmp_path)[d.id]
    assert row.said_at is not None and row.said_via == "reply"
    assert [x.id for x in ds.unsaid_disclosures(root=tmp_path)] != [d.id]
    assert deeds and deeds[0][0] == "disclosure_shared"


@pytest.mark.asyncio
async def test_said_once_forever_across_turns(tmp_path):
    """The law this whole task exists for: a truth she has told him is
    never offered again, and a second stamp changes nothing."""
    _seed(tmp_path, 1)
    d = ds.offered_disclosures(root=tmp_path)[0]
    await _dispatch(_plan(disclosure_said=d.id), tmp_path, flag=True)
    assert ds.offered_disclosures(root=tmp_path) == []

    deeds = await _dispatch(_plan(disclosure_said=d.id), tmp_path, flag=True)
    assert deeds == []                                   # no deed for a refusal
    said_rows = [ln for ln in (tmp_path / "disclosures.jsonl")
                 .read_text(encoding="utf-8").splitlines() if '"op": "say"' in ln]
    assert len(said_rows) == 1                           # one stamp, ever


@pytest.mark.asyncio
async def test_an_unknown_id_stamps_nothing_logs_once_and_never_raises(tmp_path, caplog):
    _seed(tmp_path, 1)
    with caplog.at_level(logging.DEBUG, logger="core.turn.reflect"):
        deeds = await _dispatch(_plan(disclosure_said="not-a-real-id-000000"),
                                tmp_path, flag=True)
    assert len(ds.offered_disclosures(root=tmp_path)) == 1   # book untouched
    assert deeds == []
    assert "not-a-real-id-000000" in caplog.text
    assert '"op": "say"' not in (tmp_path / "disclosures.jsonl").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_faded_id_is_refused(tmp_path, monkeypatch):
    """Faded ≠ offerable (spec §1) — the store owns the refusal and reflect
    honours it without a retry."""
    monkeypatch.setattr(ds, "_half_life_days", lambda: 1)
    _seed(tmp_path, 1)
    d = ds.offered_disclosures(root=tmp_path)[0]
    import time as _t
    assert ds.fade_stale(now=_t.time() + 3 * 86400, root=tmp_path) == 1

    deeds = await _dispatch(_plan(disclosure_said=d.id), tmp_path, flag=True)
    assert deeds == []
    assert ds.load_disclosures(root=tmp_path)[d.id].said_at is None


@pytest.mark.asyncio
async def test_flag_off_stamps_nothing_and_never_reads_the_book(tmp_path, monkeypatch):
    _seed(tmp_path, 1)
    d = ds.offered_disclosures(root=tmp_path)[0]
    calls: list = []
    monkeypatch.setattr(ds, "mark_said",
                        lambda *a, **k: calls.append((a, k)) or True)

    deeds = await _dispatch(_plan(disclosure_said=d.id), tmp_path, flag=False)
    assert calls == [] and deeds == []


@pytest.mark.asyncio
async def test_no_id_emitted_is_the_normal_turn(tmp_path, monkeypatch):
    """Most turns share nothing — and that path must not touch the book."""
    _seed(tmp_path, 1)
    calls: list = []
    monkeypatch.setattr(ds, "mark_said",
                        lambda *a, **k: calls.append((a, k)) or True)

    deeds = await _dispatch(_plan(), tmp_path, flag=True)
    assert calls == [] and deeds == []


@pytest.mark.asyncio
async def test_the_other_filings_are_untouched(tmp_path):
    """The reflect lane is shared property: a stamp must not eat the
    corrections filing riding the same plan."""
    _seed(tmp_path, 1)
    d = ds.offered_disclosures(root=tmp_path)[0]
    plan = _plan(disclosure_said=d.id,
                 corrections_to_add=[{"belief": "b", "truth": "t", "evidence": ""}])
    with patch("ai.corrections_store.record_correction",
               return_value=SimpleNamespace(belief="b", truth="t")):
        deeds = await _dispatch(plan, tmp_path, flag=True)
    kinds = [k for k, _ in deeds]
    assert "correction_recorded" in kinds and "disclosure_shared" in kinds
