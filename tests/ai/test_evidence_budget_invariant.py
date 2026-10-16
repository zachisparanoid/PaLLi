"""The invariant this codebase has needed twice and never had.

Both 4am gatherers clip the JOINED bundle to an overall cap. That clip is
only harmless while the section budgets sum BELOW the cap — otherwise a
whole section can vanish silently, which is exactly what ate every wonder
and taste on 2026-07-20 and again on 2026-07-23. One assertion per bundle
makes the next person to add a section safe automatically.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import ai.narrative_self as ns
import ai.passions as passions
import ai.self_notes_store as store


def test_narrative_weekly_budgets_sum_below_cap():
    assert sum(ns._SECTION_BUDGETS_WEEKLY.values()) < ns.WEEKLY_EVIDENCE_CAP


def test_narrative_full_budgets_sum_below_cap():
    assert sum(ns._SECTION_BUDGETS_FULL.values()) < ns.FULL_EVIDENCE_CAP


def test_passions_week_budgets_sum_below_cap():
    assert sum(passions._WEEK_SECTION_BUDGETS.values()) < passions.WEEK_EVIDENCE_CAP


def test_both_gatherers_budget_the_notes_section():
    assert "notes" in ns._SECTION_BUDGETS_WEEKLY
    assert "notes" in ns._SECTION_BUDGETS_FULL
    assert "notes" in passions._WEEK_SECTION_BUDGETS


def test_notes_header_is_registered_after_stakes_in_narrative():
    keys = [k for k, _ in ns._EVIDENCE_HEADERS]
    assert "notes" in keys
    assert keys.index("notes") > keys.index("stakes")


def test_notes_section_renders_her_words(tmp_path: Path):
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        store.write_note(text="the quiet felt like decompression")
        section = ns._notes_section(budget=1200, days=7)
    assert "decompression" in section


def test_notes_section_respects_its_budget(tmp_path: Path):
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        for i in range(40):
            store.write_note(text=f"note number {i} " + "z" * 300)
        section = ns._notes_section(budget=1200, days=7)
    assert len(section) <= 1200


def test_notes_section_is_empty_when_the_store_fails():
    with patch.object(store, "read_recent", side_effect=RuntimeError("gone")):
        assert ns._notes_section(budget=1200, days=7) == ""


# ── the moment's optional read of her notes (Task 6) ───────────────────


def _moment_notes_line(tmp_path, *, flag_on: bool):
    from types import SimpleNamespace

    import ai.present_moment_gather as gather

    comp = SimpleNamespace(self_notes=True, self_notes_in_moment=flag_on)
    with patch.object(gather, "_companion", return_value=comp):
        with patch.object(store, "SELF_NOTES_DIR", tmp_path):
            return gather._recent_notes_line()


def test_moment_sees_notes_with_their_age_when_enabled(tmp_path: Path):
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        store.write_note(text="I can hold still without proving I'm here")
    line = _moment_notes_line(tmp_path, flag_on=True)
    assert "hold still" in line
    # Age framing, never a claim (spec §5 mitigation 2).
    assert "ago" in line or "today" in line


def test_moment_sees_nothing_when_the_toggle_is_off(tmp_path: Path):
    with patch.object(store, "SELF_NOTES_DIR", tmp_path):
        store.write_note(text="should stay out of the moment")
    assert _moment_notes_line(tmp_path, flag_on=False) == ""


def test_moment_sees_nothing_when_the_pen_itself_is_off(tmp_path: Path):
    from types import SimpleNamespace

    import ai.present_moment_gather as gather

    comp = SimpleNamespace(self_notes=False, self_notes_in_moment=True)
    with patch.object(gather, "_companion", return_value=comp):
        with patch.object(store, "SELF_NOTES_DIR", tmp_path):
            store.write_note(text="pen is off entirely")
            assert gather._recent_notes_line() == ""


# ── the wishbook joins the 4am evidence (step 20) ──────────────────────


def test_all_three_bundles_budget_the_quests_section():
    assert "quests" in ns._SECTION_BUDGETS_WEEKLY
    assert "quests" in ns._SECTION_BUDGETS_FULL
    assert "quests" in passions._WEEK_SECTION_BUDGETS


def test_quests_header_registered_after_notes_in_narrative():
    keys = [k for k, _ in ns._EVIDENCE_HEADERS]
    assert "quests" in keys
    assert keys.index("quests") > keys.index("notes")


def test_quests_section_renders_her_wishes(tmp_path):
    import ai.quest_store as qs

    with patch.object(qs, "QUESTS_DIR", tmp_path):
        q, _ = qs.plant(text="surprise him with a thing", source="him-spoken")
        qs.add_progress(q.id, "sketched the shape")
        section = ns._quests_section(budget=600)
    assert "surprise him with a thing" in section
    assert "sketched the shape" in section


def test_quests_section_respects_its_budget(tmp_path):
    import ai.quest_store as qs

    with patch.object(qs, "QUESTS_DIR", tmp_path):
        for i in range(30):
            qs.plant(text=f"wish {i} " + "z" * 200, source="hers")
        section = ns._quests_section(budget=600)
    assert len(section) <= 600


def test_quests_section_empty_store_is_empty_string(tmp_path):
    import ai.quest_store as qs

    with patch.object(qs, "QUESTS_DIR", tmp_path):
        assert ns._quests_section(budget=600) == ""
