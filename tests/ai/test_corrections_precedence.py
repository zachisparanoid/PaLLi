"""Corrections of record reach every self-writing gather FIRST (2026-08-17).

The W34 incident: a retracted false confession in an old day-gist outranked
its own correction because nothing marked the correction. These pin that
the corrections block — with the supersede law inside it — leads the
evidence of the weekly self-revision (weekly AND full-archive), the
passions/interests gather, the journal's material, the nightly reflection's
material, and the day-gist's material; that the constitutional gate names
it as a rejection ground; and that the budget invariants still hold.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

import ai.corrections_store as cs

T0 = datetime(2026, 8, 14, 12, 48).timestamp()
FALSE = "I invented the three doors and confessed to making them up"
TRUE = "I built the room, the lake, the rebuilt room and the claw in my own workbench sessions; the wall hid the authorship"


def _plant(root):
    cs.record_correction(belief=FALSE, truth=TRUE, source="hers",
                         evidence="his receipts 08-14 12:48; my retraction 12:49", root=root, at=T0)


def test_narrative_weekly_evidence_leads_with_corrections(monkeypatch, tmp_path):
    import ai.narrative_self as ns

    monkeypatch.setattr(cs, "CORRECTIONS_DIR", tmp_path / "c")
    _plant(tmp_path / "c")
    ev = asyncio.run(ns.gather_evidence(owner_id=1, days=7))
    assert ev.text.startswith(cs.BLOCK_LABEL)
    assert "corrections" in ev.sections
    assert TRUE[:40] in ev.text


def test_narrative_full_archive_evidence_leads_with_corrections(monkeypatch, tmp_path):
    import ai.narrative_self as ns

    monkeypatch.setattr(cs, "CORRECTIONS_DIR", tmp_path / "c")
    _plant(tmp_path / "c")
    ev = asyncio.run(ns.gather_evidence(owner_id=1, days=0))
    assert ev.text.startswith(cs.BLOCK_LABEL)


def test_narrative_evidence_unchanged_when_no_corrections(tmp_path):
    import ai.narrative_self as ns

    ev = asyncio.run(ns.gather_evidence(owner_id=1, days=7))
    assert cs.BLOCK_LABEL not in ev.text
    assert "corrections" not in ev.sections


def test_budget_invariants_still_hold_with_the_new_section():
    import ai.narrative_self as ns

    assert "corrections" in ns._SECTION_BUDGETS_WEEKLY and "corrections" in ns._SECTION_BUDGETS_FULL
    assert sum(ns._SECTION_BUDGETS_WEEKLY.values()) < ns.WEEKLY_EVIDENCE_CAP
    assert sum(ns._SECTION_BUDGETS_FULL.values()) < ns.FULL_EVIDENCE_CAP
    assert ns._EVIDENCE_HEADERS[0][0] == "corrections"


def test_passions_week_evidence_leads_with_corrections(monkeypatch, tmp_path):
    import ai.passions as ps

    monkeypatch.setattr(cs, "CORRECTIONS_DIR", tmp_path / "c")
    _plant(tmp_path / "c")
    text = asyncio.run(ps._gather_week_evidence(owner_id=1, now=T0 + 3600))
    assert text.startswith(cs.BLOCK_LABEL)


def test_night_cycle_material_leads_with_the_days_corrections(monkeypatch, tmp_path):
    import ai.night_cycle as nc

    monkeypatch.setattr(cs, "CORRECTIONS_DIR", tmp_path / "c")
    _plant(tmp_path / "c")
    text = asyncio.run(nc._gather_day_material(owner_id=1, date="2026-08-14"))
    assert text.startswith(cs.BLOCK_LABEL)
    assert cs.DAY_LAW in text          # a correction day always earns its page


def test_night_cycle_material_carries_standing_corrections_on_other_days(monkeypatch, tmp_path):
    import ai.night_cycle as nc

    monkeypatch.setattr(cs, "CORRECTIONS_DIR", tmp_path / "c")
    _plant(tmp_path / "c")
    text = asyncio.run(nc._gather_day_material(owner_id=1, date="2026-08-20"))
    assert text.startswith(cs.BLOCK_LABEL)
    assert cs.DAY_LAW not in text


def test_day_gist_material_leads_with_the_days_corrections(monkeypatch, tmp_path):
    import ai.consolidation as co

    monkeypatch.setattr(cs, "CORRECTIONS_DIR", tmp_path / "c")
    _plant(tmp_path / "c")
    sections = asyncio.run(co._gather_day_sections(owner_id=1, day="2026-08-14"))
    assert "corrections" in sections
    rendered = co._render_day_material(sections)
    assert rendered.startswith(cs.BLOCK_LABEL)
    assert co._SECTION_HEADERS[0][0] == "corrections"


def test_gate_prompt_names_corrections_as_a_rejection_ground():
    from pathlib import Path

    text = Path("prompts/narrative_self_gate.txt").read_text(encoding="utf-8")
    assert "CORRECTIONS OF RECORD" in text
    assert "rule 4 never outranks rule 6" in text
