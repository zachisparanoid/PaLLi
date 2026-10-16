"""The nightly picture of him (item 6, 2026-08-19): MEMORY_SUMMARY.md and
RELATIONSHIP_STATE.md rendered into every reply and had never been real —
"Preferred name if explicitly confirmed: Local Operator", "Trusted
companion baseline. I'm warm, capable, and quietly reliable with them."
They are now generated from her (reviewed) memory, the night's reflection
and her last read, in her voice, one call at the night cycle; written
through the identity store (system actor, versioned). Never raises; an
empty or unparseable call changes nothing."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai import owner_picture as op


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    # pinned ON (the 3352e3d law): the live yaml is environment, never test state
    monkeypatch.setattr(op, "picture_enabled", lambda: True)


def test_flag_off_is_a_no_op(monkeypatch):
    import asyncio
    monkeypatch.setattr(op, "picture_enabled", lambda: False)
    async def llm(p): return "{}"
    assert asyncio.run(op.run_nightly_owner_picture(owner_id=1, day_reflection="", llm_call=llm, now=1.0)) == {"skipped": "flag_off"}


def _doc(content, **fm):
    return SimpleNamespace(content=content, frontmatter=dict(fm))


def _bundle():
    return SimpleNamespace(owner={
        "memory": _doc("# Memory\n\n## Durable Context\n\n- Has a cat named Mochi\n- CTO at Northwind Security; red team at Harbor Labs\n", version=220),
        "memory_summary": _doc("# Memory Summary\n\nold body", memory_summary_text="Identity memory has just been created.", summary_focus="x", update_style="y", version=53),
        "relationship_state": _doc("# Relationship State\n\nbody", relationship_summary="Trusted companion baseline.", current_posture="more open", trust_level="very_high", version=4),
        "personality_profile": _doc("# Personality Profile", display_name="Sam"),
    })


@pytest.mark.asyncio
async def test_a_good_answer_rewrites_both_docs(monkeypatch):
    writes = []
    maintained = []

    async def fake_llm(prompt):
        assert "Mochi" in prompt and "Sam" in prompt and "today meant" in prompt.lower()
        return json.dumps({
            "memory_summary": "He is mid-move and deep in a woodworking project; Mochi on the keyboard; steady, a little tired.",
            "relationship_summary": "Close and plain-spoken; he trusts me with the real thing.",
            "current_posture": "warm and direct",
            "interests": ["woodworking", "PaLLi", "the cabin dream"],
            "preferred_name": "Sam",
        })

    async def fake_update(**kw):
        writes.append(kw)

    async def fake_maintain(**kw):
        maintained.append(kw)

    with patch.object(op.identity_service, "compile_for_owner", return_value=_bundle()), \
         patch.object(op.identity_service, "update_raw_document", side_effect=fake_update), \
         patch.object(op.identity_service, "silently_maintain_document", side_effect=fake_maintain):
        result = await op.run_nightly_owner_picture(
            owner_id=1, day_reflection="today meant a quiet steadiness", llm_call=fake_llm, now=1_787_000_000.0,
        )
    assert result.get("wrote") == ["memory_summary", "relationship_state"]
    ms = [w for w in writes if w.get("doc_key") == "memory_summary"][0]
    assert "He is mid-move" in ms["markdown"] and "memory_summary_text:" in ms["markdown"]
    assert "woodworking" in ms["markdown"]
    assert ms["actor_type"] == "system"
    rs = [m for m in maintained if m.get("doc_key") == "relationship_state"][0]
    assert rs["updates"]["relationship_summary"].startswith("Close and plain-spoken")
    assert rs["updates"]["current_posture"] == "warm and direct"


@pytest.mark.asyncio
async def test_unparseable_or_empty_changes_nothing(monkeypatch):
    writes = []

    async def bad_llm(prompt):
        return "not json"

    async def fake_update(**kw):
        writes.append(kw)

    with patch.object(op.identity_service, "compile_for_owner", return_value=_bundle()), \
         patch.object(op.identity_service, "update_raw_document", side_effect=fake_update):
        result = await op.run_nightly_owner_picture(owner_id=1, day_reflection="", llm_call=bad_llm, now=1.0)
    assert "error" in result and writes == []


@pytest.mark.asyncio
async def test_no_memory_doc_skips():
    with patch.object(op.identity_service, "compile_for_owner", return_value=SimpleNamespace(owner={})):
        async def llm(p): return "{}"
        assert (await op.run_nightly_owner_picture(owner_id=1, day_reflection="", llm_call=llm, now=1.0)) == {"skipped": "no_memory_doc"}


def test_night_cycle_runs_the_picture_after_the_sweep():
    import inspect
    from ai.night_cycle import NightCycleEngine
    body = inspect.getsource(NightCycleEngine._run_once)
    assert "owner_picture" in body and body.index("memory_sweep") < body.index("owner_picture")


@pytest.mark.asyncio
async def test_no_content_is_named_not_called_unparseable(monkeypatch):
    """Same night, same shape as the stake review (08-21): the provider
    returning None is not 'unparseable' — nothing arrived. Name it."""
    writes = []

    async def nothing(prompt):
        return None

    async def fake_update(**kw):
        writes.append(kw)

    with patch.object(op.identity_service, "compile_for_owner", return_value=_bundle()), \
         patch.object(op.identity_service, "update_raw_document", side_effect=fake_update):
        result = await op.run_nightly_owner_picture(owner_id=1, day_reflection="", llm_call=nothing, now=1.0)
    assert result.get("error") == "no_content" and writes == []


def test_the_picture_envelope_holds_the_whole_patient_ladder():
    from ai.llm_openai_compatible import OpenAICompatibleProvider as P
    assert op._TIMEOUT_S >= P.deep_ladder_ceiling_seconds()   # a 04:00 lane since 08-22


def test_the_picture_starts_with_the_author_sized_budget():
    import inspect
    assert "max_tokens=8000" in inspect.getsource(op.owner_picture_llm_call)
