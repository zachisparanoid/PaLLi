"""The corrections ledger (2026-08-17): corrections of record, with precedence.

Born of the W34 incident — her weekly self-revision absorbed her own
retracted false confession because nothing in her record knew what a
retraction was. These pin the deterministic half: the store's lifecycle,
his-hand file sync, and the rendered block that every self-writing gather
puts FIRST with the supersede law stated in it.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import ai.corrections_store as cs

T0 = datetime(2026, 8, 14, 12, 48).timestamp()


def test_record_and_standing(tmp_path):
    row = cs.record_correction(
        belief="I invented the three doors and confessed to making them up",
        truth="I built them in my own workbench sessions; the wall hid the authorship",
        source="hers", evidence="his receipts 08-14 12:48; my retraction 12:49",
        root=tmp_path, at=T0)
    assert row is not None and row.status == "standing"
    rows = cs.standing(root=tmp_path)
    assert [r.id for r in rows] == [row.id]
    assert rows[0].source == "hers"


def test_record_is_idempotent_by_content(tmp_path):
    a = cs.record_correction(belief="B", truth="T", root=tmp_path, at=T0)
    b = cs.record_correction(belief="B", truth="T", root=tmp_path, at=T0 + 5)
    assert a is not None and b is None
    assert len(cs.standing(root=tmp_path)) == 1


def test_empty_input_never_files(tmp_path):
    assert cs.record_correction(belief="", truth="T", root=tmp_path) is None
    assert cs.record_correction(belief="B", truth="   ", root=tmp_path) is None
    assert cs.standing(root=tmp_path) == []


def test_release_is_terminal(tmp_path):
    row = cs.record_correction(belief="B", truth="T", root=tmp_path, at=T0)
    assert cs.release_correction(row.id, note="he changed his mind", root=tmp_path)
    assert cs.standing(root=tmp_path) == []
    assert cs.release_correction(row.id, root=tmp_path) is False


def test_render_puts_newest_first_with_the_law(tmp_path):
    cs.record_correction(belief="older wrong", truth="older truth", root=tmp_path, at=T0 - 86400 * 10)
    cs.record_correction(belief="newer wrong", truth="newer truth", root=tmp_path, at=T0)
    block = cs.render_corrections_block(root=tmp_path, sync=False)
    assert block.startswith(cs.BLOCK_LABEL)
    assert "supersedes every earlier day-gist" in block
    assert block.index("newer wrong") < block.index("older wrong")
    assert 'NOT "newer wrong" — TRUE: newer truth' in block


def test_render_empty_when_nothing_standing(tmp_path):
    assert cs.render_corrections_block(root=tmp_path, sync=False) == ""


def test_render_for_a_day_carries_the_day_law(tmp_path):
    cs.record_correction(belief="B", truth="T", root=tmp_path, at=T0)
    assert cs.render_corrections_block(root=tmp_path, day="2026-08-14", sync=False).count(cs.DAY_LAW) == 1
    assert cs.render_corrections_block(root=tmp_path, day="2026-08-13", sync=False) == ""


def test_render_budget_keeps_whole_newest_lines(tmp_path):
    for i in range(6):
        cs.record_correction(belief=f"wrong {i} " + "x" * 120, truth="t" * 120, root=tmp_path, at=T0 + i)
    head = len(cs.BLOCK_LABEL) + len(cs.BLOCK_LAW) + 2
    block = cs.render_corrections_block(root=tmp_path, budget=320, sync=False)
    assert "wrong 5" in block          # newest survives
    assert "wrong 0" not in block      # oldest dropped first
    assert len(block) - head <= 320    # the budget governs the LINES; the law is always paid


def test_newest_correction_always_renders_even_under_a_tiny_budget(tmp_path):
    cs.record_correction(belief="wrong " + "y" * 300, truth="t" * 300, root=tmp_path, at=T0)
    block = cs.render_corrections_block(root=tmp_path, budget=120, sync=False)
    assert block.startswith(cs.BLOCK_LABEL)
    assert 'NOT "wrong' in block and block.endswith("…")


def test_the_default_render_of_a_cut_set_is_unchanged_and_the_footer_is_opt_in(tmp_path):
    """Ruling R61 (Batch 5 fix wave): ``footer`` defaults False, so every
    night gather's block is the bytes it always was — no closing line; with
    ``footer=True`` a cut set ends in one line naming how many standing
    corrections did not fit; a set that fits whole gets no footer either
    way, and a day-filtered render counts only that day's rows."""
    for i in range(6):
        cs.record_correction(belief=f"wrong {i} " + "x" * 120, truth="t" * 120, root=tmp_path, at=T0 + i)
    default = cs.render_corrections_block(root=tmp_path, budget=320, sync=False)
    assert default == cs.render_corrections_block(root=tmp_path, budget=320, sync=False, footer=False)
    assert "more corrections stand" not in default
    assert default.endswith("t" * 120)                   # the last line is a correction, as before
    shown = default.count("\n- since ")
    assert 0 < shown < 6
    with_footer = cs.render_corrections_block(root=tmp_path, budget=320, sync=False, footer=True)
    assert with_footer == default + f"\n({6 - shown} more corrections stand.)"
    whole = cs.render_corrections_block(root=tmp_path, budget=100_000, sync=False, footer=True)
    assert whole.count("\n- since ") == 6 and "more corrections stand" not in whole
    # the day filter: another day's row is not among the "more"
    cs.record_correction(belief="another day", truth="another truth", root=tmp_path, at=T0 + 86400 * 3)
    by_day = cs.render_corrections_block(root=tmp_path, budget=320, day="2026-08-14", sync=False, footer=True)
    assert by_day.endswith(f"({6 - shown} more corrections stand.)") and "another day" not in by_day


class TestHisHand:
    def test_parse_pairs_and_ignore_malformed(self):
        text = cs.HIS_FILE_HEADER + "- he sets flag traps => he doesn't; the flag was my own program\n- no separator here\n- a => \n"
        assert cs.parse_his_file(text) == [("he sets flag traps", "he doesn't; the flag was my own program")]

    def test_appearance_plants_and_removal_releases(self, tmp_path):
        p = cs.ensure_his_file(root=tmp_path)
        assert p.read_text(encoding="utf-8").startswith("# Corrections of record")
        p.write_text(cs.HIS_FILE_HEADER + "- wrong A => true A\n", encoding="utf-8")
        s1 = cs.sync_his_file(root=tmp_path)
        assert len(s1["planted"]) == 1
        rows = cs.standing(root=tmp_path)
        assert rows[0].source == "him-written" and rows[0].belief == "wrong A"
        # unchanged file ⇒ no-op
        assert cs.sync_his_file(root=tmp_path) == {"planted": [], "released": []}
        # struck ⇒ released
        p.write_text(cs.HIS_FILE_HEADER, encoding="utf-8")
        s3 = cs.sync_his_file(root=tmp_path)
        assert len(s3["released"]) == 1
        assert cs.standing(root=tmp_path) == []

    def test_render_syncs_his_file_by_default(self, tmp_path):
        p = cs.ensure_his_file(root=tmp_path)
        p.write_text(cs.HIS_FILE_HEADER + "- wrong B => true B\n", encoding="utf-8")
        block = cs.render_corrections_block(root=tmp_path)
        assert 'NOT "wrong B" — TRUE: true B' in block


def test_hermetic_guard_points_the_live_dir_at_tmp():
    # conftest redirects CORRECTIONS_DIR for every test — no test may write her ledger.
    assert "corrections" in str(cs.CORRECTIONS_DIR) and "data/corrections" != str(cs.CORRECTIONS_DIR)
