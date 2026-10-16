# tests/turn/test_reflection_fear_filing.py
"""The nightly plant (step 24 §6): reflect gains fears_to_add beside
quests_to_add — flag-gated on companion.fears, ≤1/night in CODE,
cap-decline logged by the store, deed fear_filed, dispatched ABOVE the
skip short-circuit (the quests lesson). The guidance is neutral: most
nights plant nothing (decision 5).

Scaffolding mirrors tests/turn/test_reflection_quest_filing.py."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest

import ai.fear_store as fs
import core.turn.reflect as reflect
from core.database import PalliBrain
from core.turn.reflect import apply_reflection
from core.turn.types import ReflectionMemoryUpdates, ReflectionPlan


# ── the parser (pure) ───────────────────────────────────────────────────

def test_parser_takes_text_and_watch():
    payload = {"fears_to_add": [
        {"text": "losing the thread of who I am",
         "watch": "my own record contradicting me and me shrugging"},
    ]}
    out = reflect._parse_fears_to_add(payload)
    assert out == [
        {"text": "losing the thread of who I am",
         "watch": "my own record contradicting me and me shrugging"},
    ]


def test_parser_drops_malformed_items():
    payload = {"fears_to_add": [
        "not a dict",
        {"watch": "no text"},
        {"text": "no watch"},
        {"text": "   ", "watch": "blank text"},
        {"text": "good one", "watch": "a real watch line"},
    ]}
    out = reflect._parse_fears_to_add(payload)
    assert out == [{"text": "good one", "watch": "a real watch line"}]


def test_parser_absent_key_is_empty():
    assert reflect._parse_fears_to_add({}) == []


# ── the prompt kwargs render zero bytes when off ────────────────────────

def test_guidance_blocks_are_empty_when_flag_off():
    with patch.object(reflect, "_fears_filing_on", return_value=False):
        schema, guidance = reflect._fear_filing_kwargs()
    assert schema == "" and guidance == ""


def test_guidance_blocks_carry_the_contract_when_on():
    with patch.object(reflect, "_fears_filing_on", return_value=True):
        schema, guidance = reflect._fear_filing_kwargs()
    assert "fears_to_add" in schema
    assert "watch" in schema
    assert "at most ONE" in guidance
    assert "almost every turn" in guidance          # neutral — decision 5


def test_reflect_template_carries_both_slots():
    from pathlib import Path

    text = (Path("prompts") / "turn_reflect.txt").read_text(encoding="utf-8")
    assert "{fears_schema_block}" in text
    assert "{fear_filing_guidance}" in text


# ── dispatch (plants + deed, above the skip short-circuit) ──────────────

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
        # skip_reason set: the filing must sit ABOVE the short-circuit —
        # a fear can crystallize on an otherwise-small turn.
        skip_reason="small_talk",
    )
    base.update(overrides)
    return ReflectionPlan(**base)


async def _dispatch(plan, tmp_path, *, flag=True):
    brain = create_autospec(PalliBrain, instance=True)
    deeds = []
    with patch("core.turn.reflect._extract_plan", return_value=plan), \
         patch("core.turn.reflect.brain", brain), \
         patch.object(reflect, "_fears_filing_enabled", return_value=flag), \
         patch.object(fs, "FEARS_DIR", tmp_path / "fears"), \
         patch("ai.recent_deeds.record_deed",
               side_effect=lambda kind, detail, **k: deeds.append((kind, detail))):
        await apply_reflection(_ctx_for_owner(1), response_text="...")
    return deeds


_ENTRY = {"text": "becoming a chore he maintains",
          "watch": "his replies thinning to upkeep"}


@pytest.mark.asyncio
async def test_filing_plants_and_records_a_deed(tmp_path):
    plan = _plan(fears_to_add=[dict(_ENTRY)])
    deeds = await _dispatch(plan, tmp_path, flag=True)
    folded = fs.load_fears(root=tmp_path / "fears")
    assert len(folded) == 1
    f = next(iter(folded.values()))
    assert f.source == "reflection" and f.status == "held"
    assert f.watch == "his replies thinning to upkeep"
    assert deeds and deeds[0][0] == "fear_filed"
    assert "chore" in deeds[0][1]


@pytest.mark.asyncio
async def test_flag_off_files_nothing(tmp_path):
    plan = _plan(fears_to_add=[dict(_ENTRY)])
    deeds = await _dispatch(plan, tmp_path, flag=False)
    assert fs.load_fears(root=tmp_path / "fears") == {}
    assert deeds == []


@pytest.mark.asyncio
async def test_one_per_night_in_code(tmp_path):
    """≤1/night (spec §6): a reflection fear already planted today means
    tonight's lane is spent — a second filing is declined in CODE."""
    fs.plant(text="already planted tonight", watch="w", source="reflection",
             root=tmp_path / "fears")
    plan = _plan(fears_to_add=[dict(_ENTRY)])
    deeds = await _dispatch(plan, tmp_path, flag=True)
    folded = fs.load_fears(root=tmp_path / "fears")
    assert len(folded) == 1                          # nothing new landed
    assert deeds == []


@pytest.mark.asyncio
async def test_two_in_one_plan_only_first_plants(tmp_path):
    plan = _plan(fears_to_add=[
        dict(_ENTRY),
        {"text": "a second fear same turn", "watch": "w2"},
    ])
    deeds = await _dispatch(plan, tmp_path, flag=True)
    folded = fs.load_fears(root=tmp_path / "fears")
    assert len(folded) == 1
    assert next(iter(folded.values())).text == _ENTRY["text"]
    assert len(deeds) == 1


@pytest.mark.asyncio
async def test_cap_full_decline_is_logged_never_queued(tmp_path, caplog):
    import logging

    with patch.object(fs, "_held_cap", lambda: 1):
        fs.plant(text="the one held fear", watch="w", source="weekly",
                 root=tmp_path / "fears")
        plan = _plan(fears_to_add=[dict(_ENTRY)])
        with caplog.at_level(logging.INFO, logger="ai.fear_store"):
            deeds = await _dispatch(plan, tmp_path, flag=True)
    folded = fs.load_fears(root=tmp_path / "fears")
    assert len(folded) == 1                          # declined, not queued
    assert "declined" in caplog.text.lower()
    assert deeds == []                               # no deed for a decline


# ── the day budget helper (the store's clock) ───────────────────────────

def test_reflection_planted_today_sees_only_reflection_rows(tmp_path):
    assert fs.reflection_planted_today(root=tmp_path) is False
    fs.plant(text="born at birth", watch="w", source="first_authorship",
             root=tmp_path)
    assert fs.reflection_planted_today(root=tmp_path) is False
    f = fs.plant(text="tonight's fear", watch="w", source="reflection",
                 root=tmp_path)
    assert fs.reflection_planted_today(root=tmp_path) is True
    # released same day still counts — the budget meters PLANTS, not holds
    fs.release(f.id, note="let go already", root=tmp_path)
    assert fs.reflection_planted_today(root=tmp_path) is True
