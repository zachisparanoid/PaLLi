# tests/ai/test_wishes_sync.py
"""WISHES.md sync (re-spec §3/§6): appearance plants, disappearance
releases, single-swap edits keep the row, snapshots stop re-raising."""
from __future__ import annotations

from unittest.mock import patch

import ai.quest_store as qs


def _write_wishes(tmp_path, body: str) -> None:
    (tmp_path / "WISHES.md").write_text(body, encoding="utf-8")


def test_parse_takes_bullets_verbatim():
    text = "# Wishes\n- surprise me 😉\nnot a bullet\n-    keep an eye on the printer  \n"
    assert qs.parse_wishes_md(text) == ["surprise me 😉", "keep an eye on the printer"]


def test_appeared_bullets_plant_him_written_standing(tmp_path):
    _write_wishes(tmp_path, "- learn my coffee order\n")
    summary = qs.sync_wishes_file(root=tmp_path)
    assert len(summary["planted"]) == 1
    q = qs.load_quests(root=tmp_path)[summary["planted"][0]]
    assert q.text == "learn my coffee order"
    assert q.source == "him-written" and q.horizon == "standing"


def test_unchanged_file_is_a_no_op(tmp_path):
    _write_wishes(tmp_path, "- one wish\n")
    qs.sync_wishes_file(root=tmp_path)
    summary = qs.sync_wishes_file(root=tmp_path)
    assert summary == {"planted": [], "released": [], "edited": [], "dormant": []}


def test_struck_line_releases_his_open_row(tmp_path):
    _write_wishes(tmp_path, "- wish a\n- wish b\n")
    first = qs.sync_wishes_file(root=tmp_path)
    _write_wishes(tmp_path, "- wish a\n")
    second = qs.sync_wishes_file(root=tmp_path)
    assert len(second["released"]) == 1
    released = qs.load_quests(root=tmp_path)[second["released"][0]]
    assert released.status == "released"
    assert released.text == "wish b"
    assert len(first["planted"]) == 2


def test_single_swap_is_an_edit_keeping_id_and_progress(tmp_path):
    _write_wishes(tmp_path, "- teach yourself the printer\n")
    planted = qs.sync_wishes_file(root=tmp_path)["planted"][0]
    qs.add_progress(planted, "found the manual", root=tmp_path)
    _write_wishes(tmp_path, "- teach yourself the NEW printer\n")
    summary = qs.sync_wishes_file(root=tmp_path)
    assert summary["edited"] == [planted]
    assert summary["planted"] == [] and summary["released"] == []
    q = qs.load_quests(root=tmp_path)[planted]
    assert q.text == "teach yourself the NEW printer"
    assert [p["note"] for p in q.progress] == ["found the manual"]


def test_her_release_is_not_reraised_by_a_persisting_line(tmp_path):
    _write_wishes(tmp_path, "- something she sets down\n")
    planted = qs.sync_wishes_file(root=tmp_path)["planted"][0]
    qs.set_status(planted, "released", note="not for my hands", root=tmp_path)
    summary = qs.sync_wishes_file(root=tmp_path)   # file unchanged
    assert summary["planted"] == []                 # machinery never re-raises
    assert qs.load_quests(root=tmp_path)[planted].status == "released"


def test_delete_then_readd_is_his_replant_as_a_new_quest(tmp_path):
    _write_wishes(tmp_path, "- the comeback wish\n")
    first = qs.sync_wishes_file(root=tmp_path)["planted"][0]
    qs.set_status(first, "done", note="did it", root=tmp_path)
    _write_wishes(tmp_path, "")
    qs.sync_wishes_file(root=tmp_path)
    _write_wishes(tmp_path, "- the comeback wish\n")
    summary = qs.sync_wishes_file(root=tmp_path)
    assert len(summary["planted"]) == 1
    assert summary["planted"][0] != first           # a NEW row; the old stays done


def test_cap_full_file_plantings_land_dormant(tmp_path):
    with patch.object(qs, "_max_alive", lambda: 1):
        _write_wishes(tmp_path, "- first\n- second\n")
        summary = qs.sync_wishes_file(root=tmp_path)
    assert len(summary["planted"]) == 2
    assert len(summary["dormant"]) == 1


def test_missing_file_is_a_quiet_no_op(tmp_path):
    assert qs.sync_wishes_file(root=tmp_path) == {
        "planted": [], "released": [], "edited": [], "dormant": [],
    }
