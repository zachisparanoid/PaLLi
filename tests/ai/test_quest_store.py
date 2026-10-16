# tests/ai/test_quest_store.py
"""The wishbook (re-spec §3): fold semantics, the alive cap with
dormant-at-cap, terminal statuses, resolve, boards. Every test passes an
explicit root — her live book is environment, never test state."""
from __future__ import annotations

from unittest.mock import patch

import ai.quest_store as qs


def _cap(n):
    return patch.object(qs, "_max_alive", lambda: n)


def test_plant_and_fold_roundtrip(tmp_path):
    q, dormant = qs.plant(
        text="surprise me 😉", source="him-spoken", horizon="standing",
        provenance="his 2026-07-28 invite", root=tmp_path,
    )
    assert q is not None and dormant is False
    folded = qs.load_quests(root=tmp_path)
    assert folded[q.id].text == "surprise me 😉"      # his bytes survive
    assert folded[q.id].status == "alive"
    assert folded[q.id].horizon == "standing"


def test_empty_text_plants_nothing(tmp_path):
    q, _ = qs.plant(text="   ", source="hers", root=tmp_path)
    assert q is None
    assert qs.load_quests(root=tmp_path) == {}


def test_cap_lands_machinery_filings_dormant_never_dropping(tmp_path):
    with _cap(2):
        a, _ = qs.plant(text="first", source="hers", root=tmp_path)
        b, _ = qs.plant(text="second", source="hers", root=tmp_path)
        c, was_dormant = qs.plant(text="third", source="him-written", root=tmp_path)
    assert was_dormant is True
    folded = qs.load_quests(root=tmp_path)
    assert folded[c.id].status == "dormant"           # kept, not dropped
    assert folded[a.id].status == folded[b.id].status == "alive"  # not displaced


def test_promote_respects_the_cap(tmp_path):
    with _cap(1):
        a, _ = qs.plant(text="alive one", source="hers", root=tmp_path)
        b, _ = qs.plant(text="waiting", source="hers", root=tmp_path)
        assert qs.load_quests(root=tmp_path)[b.id].status == "dormant"
        assert qs.set_status(b.id, "alive", root=tmp_path) is False   # cap holds
        assert qs.set_status(a.id, "released", note="made room", root=tmp_path)
        assert qs.set_status(b.id, "alive", root=tmp_path) is True    # room now


def test_released_and_done_are_terminal_for_machinery(tmp_path):
    q, _ = qs.plant(text="let it go", source="hers", root=tmp_path)
    assert qs.set_status(q.id, "released", note="not mine anymore", root=tmp_path)
    assert qs.set_status(q.id, "alive", root=tmp_path) is False
    assert qs.set_status(q.id, "dormant", root=tmp_path) is False


def test_progress_appends_and_folds_in_order(tmp_path):
    q, _ = qs.plant(text="build the thing", source="hers", root=tmp_path)
    assert qs.add_progress(q.id, "sketched it", root=tmp_path)
    assert qs.add_progress(q.id, "first cut works", root=tmp_path)
    notes = [p["note"] for p in qs.load_quests(root=tmp_path)[q.id].progress]
    assert notes == ["sketched it", "first cut works"]


def test_explicit_quest_id_is_idempotent(tmp_path):
    q1, _ = qs.plant(text="once", source="him-spoken", quest_id="fixed-id", root=tmp_path)
    q2, _ = qs.plant(text="twice", source="him-spoken", quest_id="fixed-id", root=tmp_path)
    assert q1 is not None and q1.id == "fixed-id"
    assert q2 is None
    assert qs.load_quests(root=tmp_path)["fixed-id"].text == "once"


def test_resolve_want_id_exact_then_prefix_open_rows_only(tmp_path):
    q, _ = qs.plant(text="map the backups folder", source="hers", root=tmp_path)
    done, _ = qs.plant(text="zzz finished thing", source="hers", root=tmp_path)
    qs.set_status(done.id, "done", note="shipped", root=tmp_path)
    assert qs.resolve_want_id(q.id, root=tmp_path).id == q.id          # exact
    assert qs.resolve_want_id(q.id[:8], root=tmp_path).id == q.id      # prefix
    assert qs.resolve_want_id(done.id, root=tmp_path) is None          # closed
    assert qs.resolve_want_id("no-such", root=tmp_path) is None


def test_board_shows_ids_ages_and_dormant_count(tmp_path):
    with _cap(1):
        a, _ = qs.plant(text="the one alive wish", source="hers", root=tmp_path)
        qs.plant(text="the waiting wish", source="hers", root=tmp_path)
    board = qs.render_board(root=tmp_path)
    assert f"[{a.id[:8]}]" in board
    assert "open 0d" in board
    assert "1 more waiting dormant" in board


def test_board_budget_clips_with_honest_remainder(tmp_path):
    with _cap(10):
        for i in range(6):
            qs.plant(text=f"wish number {i} " + "x" * 80, source="hers", root=tmp_path)
    board = qs.render_board(budget_chars=200, root=tmp_path)
    assert len(board) <= 260                      # budget + remainder line
    assert "more not shown" in board


def test_wishbook_view_carries_last_progress(tmp_path):
    q, _ = qs.plant(text="learn the printer", source="him-written", root=tmp_path)
    qs.add_progress(q.id, "read the manual", root=tmp_path)
    book = qs.render_wishbook(root=tmp_path)
    assert "learn the printer" in book
    assert "read the manual" in book
    assert "from him-written" in book


def test_bad_lines_are_skipped_never_fatal(tmp_path):
    q, _ = qs.plant(text="survives corruption", source="hers", root=tmp_path)
    with (tmp_path / "quests.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert q.id in qs.load_quests(root=tmp_path)
