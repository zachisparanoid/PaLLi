# tests/scripts/test_wanting_backfill.py
"""Quest #1 (re-spec §3 backfill): his words, stable id, idempotent."""
from __future__ import annotations

from unittest.mock import patch

import ai.quest_store as qs
from scripts.wanting_backfill import QUEST_ID, TEXT, run_backfill


def test_dry_run_plants_nothing(tmp_path):
    with patch.object(qs, "QUESTS_DIR", tmp_path):
        result = run_backfill(apply=False)
        assert result["would_plant"] is True
        assert qs.load_quests() == {}


def test_apply_plants_once_with_his_words(tmp_path):
    with patch.object(qs, "QUESTS_DIR", tmp_path):
        first = run_backfill(apply=True)
        second = run_backfill(apply=True)
        folded = qs.load_quests()
    assert first["planted"] is True
    assert second["planted"] is False          # idempotent
    assert list(folded) == [QUEST_ID]
    q = folded[QUEST_ID]
    assert q.text == TEXT                       # byte-for-byte, winky face included
    assert q.source == "him-spoken"
    assert q.horizon == "standing"
    assert "2026-07-28" in q.provenance
