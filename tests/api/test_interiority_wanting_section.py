# tests/api/test_interiority_wanting_section.py
"""The Wanting window (re-spec §10): read-only payload, flag-off honest,
reader failure is error-never-absence."""
from __future__ import annotations

from unittest.mock import patch

import ai.daydream_store as dds
import ai.quest_store as qs
from api.web_interiority import _wanting_section


def test_flag_off_is_enabled_false_and_nothing_read(tmp_path):
    section = _wanting_section(enabled=False)
    assert section == {"enabled": False, "quests": [], "daydreams": []}


def test_payload_carries_quests_and_daydreams(tmp_path):
    with patch.object(qs, "QUESTS_DIR", tmp_path / "q"), \
         patch.object(dds, "DAYDREAM_DIR", tmp_path / "d"):
        quest, _ = qs.plant(text="surprise him 😉", source="him-spoken")
        qs.add_progress(quest.id, "first cut done")
        dds.write_entry(text="tonight: the poem again")
        section = _wanting_section(enabled=True)
    assert section["enabled"] is True
    assert section["quests"][0]["text"] == "surprise him 😉"
    assert section["quests"][0]["status"] == "alive"
    assert section["quests"][0]["progress"][-1]["note"] == "first cut done"
    assert section["daydreams"][0]["text"] == "tonight: the poem again"


def test_reader_failure_is_error_never_absence(tmp_path):
    with patch.object(qs, "load_quests", side_effect=RuntimeError("gone")):
        section = _wanting_section(enabled=True)
    assert section.get("error") is True
