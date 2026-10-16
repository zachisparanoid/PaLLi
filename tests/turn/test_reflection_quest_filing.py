# tests/turn/test_reflection_quest_filing.py
"""Reflect filing (re-spec §3): parser validates, dispatch plants + deed
under the flags, off files nothing, cap lands dormant.

Scaffolding mirrors tests/turn/test_reflection_open_threads_dispatch.py
(the house pattern): real ReflectionPlan, _extract_plan + brain patched.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest

import ai.quest_store as qs
import core.turn.reflect as reflect
from core.database import PalliBrain
from core.turn.reflect import apply_reflection
from core.turn.types import ReflectionMemoryUpdates, ReflectionPlan


# ── the parser (pure) ───────────────────────────────────────────────────

def test_parser_takes_text_and_optional_horizon():
    payload = {"quests_to_add": [
        {"text": "keep an eye on the printer situation", "horizon": "soon"},
        {"text": "surprise him with something"},
    ]}
    out = reflect._parse_quests_to_add(payload)
    assert out == [
        {"text": "keep an eye on the printer situation", "horizon": "soon"},
        {"text": "surprise him with something", "horizon": "someday"},
    ]


def test_parser_drops_malformed_items():
    payload = {"quests_to_add": [
        "not a dict",
        {"horizon": "soon"},                      # no text
        {"text": "   "},                          # blank text
        {"text": "good one", "horizon": "weird"},  # bad horizon → someday
    ]}
    out = reflect._parse_quests_to_add(payload)
    assert out == [{"text": "good one", "horizon": "someday"}]


def test_parser_absent_key_is_empty():
    assert reflect._parse_quests_to_add({}) == []


# ── the prompt kwargs render zero bytes when off ────────────────────────

def test_guidance_blocks_are_empty_when_flag_off():
    with patch.object(reflect, "_quests_filing_on", return_value=False):
        schema, guidance = reflect._quest_filing_kwargs()
    assert schema == "" and guidance == ""


def test_guidance_blocks_carry_the_contract_when_on():
    with patch.object(reflect, "_quests_filing_on", return_value=True):
        schema, guidance = reflect._quest_filing_kwargs()
    assert "quests_to_add" in schema
    assert "durably directive" in guidance
    assert "close to their own words" in guidance


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
        # a wish can ride an otherwise-small turn (the scout-flagged trap).
        skip_reason="small_talk",
    )
    base.update(overrides)
    return ReflectionPlan(**base)


async def _dispatch(plan, tmp_path, *, flag=True):
    brain = create_autospec(PalliBrain, instance=True)
    deeds = []
    with patch("core.turn.reflect._extract_plan", return_value=plan), \
         patch("core.turn.reflect.brain", brain), \
         patch.object(reflect, "_quests_filing_enabled", return_value=flag), \
         patch.object(qs, "QUESTS_DIR", tmp_path / "quests"), \
         patch("ai.recent_deeds.record_deed",
               side_effect=lambda kind, detail, **k: deeds.append((kind, detail))):
        await apply_reflection(_ctx_for_owner(1), response_text="...")
    return deeds


@pytest.mark.asyncio
async def test_filing_plants_and_records_a_deed(tmp_path):
    plan = _plan(quests_to_add=[{"text": "keep an eye on the ferret name thing",
                                 "horizon": "soon"}])
    deeds = await _dispatch(plan, tmp_path, flag=True)
    folded = qs.load_quests(root=tmp_path / "quests")
    assert len(folded) == 1
    q = next(iter(folded.values()))
    assert q.source == "him-spoken" and q.horizon == "soon"
    assert deeds and deeds[0][0] == "quest_filed"
    assert "ferret name" in deeds[0][1]


@pytest.mark.asyncio
async def test_flag_off_files_nothing(tmp_path):
    plan = _plan(quests_to_add=[{"text": "should not land", "horizon": "someday"}])
    deeds = await _dispatch(plan, tmp_path, flag=False)
    assert qs.load_quests(root=tmp_path / "quests") == {}
    assert deeds == []


@pytest.mark.asyncio
async def test_cap_full_filing_lands_dormant_with_deed(tmp_path):
    with patch.object(qs, "_max_alive", lambda: 1):
        qs.plant(text="already carrying", source="hers", root=tmp_path / "quests")
        plan = _plan(quests_to_add=[{"text": "one more from him",
                                     "horizon": "someday"}])
        deeds = await _dispatch(plan, tmp_path, flag=True)
    folded = qs.load_quests(root=tmp_path / "quests")
    filed = [q for q in folded.values() if q.text == "one more from him"]
    assert filed and filed[0].status == "dormant"      # kept, never dropped
    assert deeds                                        # still acknowledged
