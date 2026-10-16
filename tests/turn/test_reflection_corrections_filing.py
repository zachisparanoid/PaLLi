"""Reflect files corrections of record (2026-08-17): the parser validates,
dispatch plants into the ledger + records a deed, always on (truth
machinery — no flag), above the skip short-circuit like the wishbook lane.
Scaffolding mirrors test_reflection_quest_filing.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest

import ai.corrections_store as cs
import core.turn.reflect as reflect
from core.database import PalliBrain
from core.turn.reflect import apply_reflection
from core.turn.types import ReflectionMemoryUpdates, ReflectionPlan


def test_parser_takes_belief_truth_optional_evidence():
    payload = {"corrections_to_add": [
        {"belief": "I invented the doors", "truth": "I built them", "evidence": "his receipts"},
        {"belief": "  ", "truth": "x"},                    # empty belief dropped
        {"belief": "y", "truth": ""},                      # empty truth dropped
        "not-a-dict",
        {"belief": "z", "truth": "zz"},                    # no evidence is fine
    ]}
    out = reflect._parse_corrections_to_add(payload)
    assert out == [
        {"belief": "I invented the doors", "truth": "I built them", "evidence": "his receipts"},
        {"belief": "z", "truth": "zz", "evidence": ""},
    ]
    assert reflect._parse_corrections_to_add({}) == []
    assert reflect._parse_corrections_to_add({"corrections_to_add": "nope"}) == []


def test_plan_field_exists_default_empty():
    assert ReflectionPlan.__dataclass_fields__["corrections_to_add"].default_factory() == []


def test_prompt_blocks_are_always_on_and_carry_the_contract():
    from pathlib import Path

    assert "corrections_to_add" in reflect._CORRECTIONS_SCHEMA_BLOCK
    assert "shown false" in reflect._CORRECTIONS_GUIDANCE_BLOCK
    assert "outranks every older" in reflect._CORRECTIONS_GUIDANCE_BLOCK
    tpl = Path("prompts/turn_reflect.txt").read_text(encoding="utf-8")
    assert "{corrections_schema_block}" in tpl and "{corrections_guidance}" in tpl


def _ctx_for_owner(owner_id: int):
    return SimpleNamespace(
        turn=SimpleNamespace(owner_id=owner_id, surface="local", content="", metadata=SimpleNamespace()),
    )


def _plan(**overrides) -> ReflectionPlan:
    base: dict = dict(
        memory_updates=ReflectionMemoryUpdates(), personality_observations=[],
        relationship_delta=None, user_facts_add=[], monologue_note=None,
        first_contact_resolutions={}, skip_reason="small_talk",
    )
    base.update(overrides)
    return ReflectionPlan(**base)


async def _dispatch(plan, tmp_path):
    brain = create_autospec(PalliBrain, instance=True)
    deeds = []
    with patch("core.turn.reflect._extract_plan", return_value=plan), \
         patch("core.turn.reflect.brain", brain), \
         patch.object(cs, "CORRECTIONS_DIR", tmp_path / "corrections"), \
         patch("ai.recent_deeds.record_deed",
               side_effect=lambda kind, detail, **k: deeds.append((kind, detail))):
        await apply_reflection(_ctx_for_owner(1), response_text="...")
    return deeds


@pytest.mark.asyncio
async def test_filing_records_the_correction_and_a_deed(tmp_path):
    plan = _plan(corrections_to_add=[{
        "belief": "I invented the three doors and confessed to making them up",
        "truth": "I built them in my own workbench sessions; the wall hid the authorship",
        "evidence": "his receipts 08-14 12:48",
    }])
    deeds = await _dispatch(plan, tmp_path)
    rows = cs.standing(root=tmp_path / "corrections")
    assert len(rows) == 1 and rows[0].source == "hers"
    assert rows[0].evidence == "his receipts 08-14 12:48"
    assert deeds and deeds[0][0] == "correction_recorded"
    assert "set the record straight" in deeds[0][1]


@pytest.mark.asyncio
async def test_no_corrections_files_nothing(tmp_path):
    deeds = await _dispatch(_plan(), tmp_path)
    assert cs.standing(root=tmp_path / "corrections") == []
    assert deeds == []
