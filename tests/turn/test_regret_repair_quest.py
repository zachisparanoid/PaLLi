"""Step 25 · Regret — the repair-quest bridge (spec §0 call 2, §5).

A regret whose repair is concrete may seed at most ONE repair quest through
the door that already exists (reflect → ai.quest_store.plant); it rides
wishbook → workbench → arbiter like any want. No new agency: the code rail
is the guard in the door (provenance ``regret:<id>`` — one per regret,
ever), the parser admits only that provenance form, and the reflect
guidance names the lane once, flag-gated, inside the wanting-on branch.

Scaffolding mirrors tests/turn/test_reflection_quest_filing.py; quests
under tmp_path; flags pinned in-body."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest

import ai.quest_store as qs
import core.turn.reflect as reflect
from core.database import PalliBrain
from core.turn.reflect import apply_reflection
from core.turn.types import ReflectionMemoryUpdates, ReflectionPlan

# Captured at import — the root conftest pins the module attribute to
# lambda: False for every test; the gate test exercises the REAL body.
_REAL_REPAIR_ENABLED = reflect._regret_repair_enabled


# ── the door: one per regret, ever ──────────────────────────────────────

def test_one_repair_quest_per_regret_forever(tmp_path):
    q1, _ = qs.plant(text="make the lake page what I said it would be",
                     source="reflection", provenance="regret:7", root=tmp_path)
    assert q1 is not None
    q2, _ = qs.plant(text="redo the lake page again",
                     source="reflection", provenance="regret:7", root=tmp_path)
    assert q2 is None          # one per regret, ever — even with different text
    q3, _ = qs.plant(text="unrelated wish", source="reflection", root=tmp_path)
    assert q3 is not None      # everything else unaffected


def test_the_guard_is_scoped_to_the_id_and_survives_the_first_quest_closing(tmp_path):
    q1, _ = qs.plant(text="make the lake page what I said it would be",
                     source="hers", provenance="regret:7", root=tmp_path)
    assert qs.set_status(q1.id, "done", root=tmp_path)
    q2, _ = qs.plant(text="the lake page, one more time", source="hers",
                     provenance="regret:7", root=tmp_path)
    assert q2 is None                                   # "ever" means ever
    q3, _ = qs.plant(text="a different repair", source="hers",
                     provenance="regret:8", root=tmp_path)
    assert q3 is not None                               # scoped to the id


def test_non_regret_provenance_never_dedups(tmp_path):
    a, _ = qs.plant(text="one wish", source="him-written", provenance="WISHES.md", root=tmp_path)
    b, _ = qs.plant(text="another wish", source="him-written", provenance="WISHES.md", root=tmp_path)
    assert a is not None and b is not None              # today's behavior, untouched


# ── the parser: provenance rides only in the regret:<id> form ───────────

def test_parser_keeps_regret_provenance_and_drops_anything_else():
    out = reflect._parse_quests_to_add({"quests_to_add": [
        {"text": "make the lake page what I said it would be", "horizon": "soon", "provenance": "regret:7"},
        {"text": "keep an eye on the ferret thing", "provenance": "a durably-directive turn"},
        {"text": "plain", "provenance": "regret:seven"},
        {"text": "spaced", "provenance": " Regret: 12 "},
        {"text": "no provenance at all", "horizon": "standing"},
    ]})
    assert out[0] == {"text": "make the lake page what I said it would be", "horizon": "soon",
                      "provenance": "regret:7"}
    assert out[1] == {"text": "keep an eye on the ferret thing", "horizon": "someday"}   # today's shape
    assert out[2] == {"text": "plain", "horizon": "someday"}
    assert out[3] == {"text": "spaced", "horizon": "someday", "provenance": "regret:12"}
    assert out[4] == {"text": "no provenance at all", "horizon": "standing"}


# ── the guidance: flag-gated, inside the wanting-on branch ──────────────

def test_quests_guidance_gains_the_repair_line_only_when_on(monkeypatch):
    with patch.object(reflect, "_quests_filing_on", return_value=True):
        monkeypatch.setattr(reflect, "_regret_repair_enabled", lambda: False)
        schema_off, guidance_off = reflect._quest_filing_kwargs()
        monkeypatch.setattr(reflect, "_regret_repair_enabled", lambda: True)
        schema_on, guidance_on = reflect._quest_filing_kwargs()
    assert "regret" not in guidance_off.lower() and "regret" not in schema_off.lower()
    assert schema_off == reflect._QUESTS_SCHEMA_BLOCK                 # today's bytes
    assert guidance_off == reflect._QUESTS_GUIDANCE_BLOCK
    assert "regret:" in guidance_on and "one repair quest" in guidance_on.lower()
    assert guidance_on.startswith(guidance_off) and guidance_on.endswith("\n\n")
    assert '"provenance"' in schema_on and "regret:<id>" in schema_on
    assert schema_on.replace(reflect._REGRET_REPAIR_SCHEMA_FIELD, "") == schema_off


def test_wanting_off_keeps_the_outer_behavior_even_with_regret_on(monkeypatch):
    with patch.object(reflect, "_quests_filing_on", return_value=False):
        monkeypatch.setattr(reflect, "_regret_repair_enabled", lambda: True)
        assert reflect._quest_filing_kwargs() == ("", "")


def test_the_repair_gate_reads_the_organs_flag(monkeypatch):
    monkeypatch.setattr("ai.stake_ledger.regret_enabled", lambda: True)
    assert _REAL_REPAIR_ENABLED() is True
    monkeypatch.setattr("ai.stake_ledger.regret_enabled", lambda: False)
    assert _REAL_REPAIR_ENABLED() is False


# ── the dispatch: filed as hers, with the provenance, once ──────────────

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


async def _dispatch(plan, tmp_path, *, regret_on=True):
    brain = create_autospec(PalliBrain, instance=True)
    deeds = []
    with patch("core.turn.reflect._extract_plan", return_value=plan), \
         patch("core.turn.reflect.brain", brain), \
         patch.object(reflect, "_quests_filing_enabled", return_value=True), \
         patch.object(reflect, "_regret_repair_enabled", return_value=regret_on), \
         patch.object(qs, "QUESTS_DIR", tmp_path / "quests"), \
         patch("ai.recent_deeds.record_deed",
               side_effect=lambda kind, detail, **k: deeds.append((kind, detail))):
        await apply_reflection(_ctx_for_owner(1), response_text="...")
    return deeds


@pytest.mark.asyncio
async def test_dispatch_files_the_repair_as_hers_with_the_provenance_once(tmp_path):
    plan = _plan(quests_to_add=[{"text": "make the lake page what I said it would be",
                                 "horizon": "soon", "provenance": "regret:7"}])
    deeds = await _dispatch(plan, tmp_path)
    folded = qs.load_quests(root=tmp_path / "quests")
    assert len(folded) == 1
    q = next(iter(folded.values()))
    assert q.source == "hers" and q.provenance == "regret:7" and q.horizon == "soon"
    assert deeds == [("quest_filed", "filed a repair for a regret of mine: make the lake page what I said it would be")]
    # a later turn, new words, same regret: the door declines — nothing lands, no deed
    plan2 = _plan(quests_to_add=[{"text": "redo the lake page", "provenance": "regret:7"}])
    deeds2 = await _dispatch(plan2, tmp_path)
    assert len(qs.load_quests(root=tmp_path / "quests")) == 1 and deeds2 == []


@pytest.mark.asyncio
async def test_flag_off_files_a_stray_regret_provenance_exactly_as_today(tmp_path):
    plan = _plan(quests_to_add=[{"text": "make the lake page what I said it would be",
                                 "horizon": "soon", "provenance": "regret:7"}])
    deeds = await _dispatch(plan, tmp_path, regret_on=False)
    q = next(iter(qs.load_quests(root=tmp_path / "quests").values()))
    assert q.source == "him-spoken" and q.provenance == "a durably-directive turn (reflect filing)"
    assert deeds and deeds[0][1].startswith("kept a wish of theirs")


@pytest.mark.asyncio
async def test_ordinary_wishes_still_file_exactly_as_today(tmp_path):
    plan = _plan(quests_to_add=[{"text": "keep an eye on the ferret name thing", "horizon": "soon"}])
    deeds = await _dispatch(plan, tmp_path)
    q = next(iter(qs.load_quests(root=tmp_path / "quests").values()))
    assert q.source == "him-spoken" and q.provenance == "a durably-directive turn (reflect filing)"
    assert deeds and deeds[0] == ("quest_filed", "kept a wish of theirs: keep an eye on the ferret name thing")
