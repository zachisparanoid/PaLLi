# tests/ai/test_moment_daydream_sense.py
"""daydream_thought: the humanized phrase exists, and the gather carries
her latest daydream only under wanting+present_moment (no health key
otherwise — byte-identical-off, the threads pattern)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import ai.daydream_store as dds
import ai.present_moment as pm
import ai.present_moment_gather as gather


def test_event_phrase_exists_and_is_lived_not_mechanical():
    phrase = pm._EVENT_PHRASES.get("daydream_thought")
    assert phrase and "daydream" in phrase
    assert "_" not in phrase


def test_daydream_line_is_age_framed(tmp_path):
    with patch.object(dds, "DAYDREAM_DIR", tmp_path):
        dds.write_entry(text="I want to map the backups folder")
        line = asyncio.run(gather._daydream_line())
    assert "map the backups folder" in line
    assert "daydream" in line.lower()
    assert "ago" in line or "just now" in line


def test_gather_carries_it_only_when_wanting_on(tmp_path):
    def comp(wanting):
        return SimpleNamespace(
            present_moment=True, arbiter=False, wanting=wanting,
            self_notes=False, own_work=False,
        )

    async def run(wanting):
        with patch.object(gather, "_companion", return_value=comp(wanting)), \
             patch.object(dds, "DAYDREAM_DIR", tmp_path):
            dds.write_entry(text="a held wish stirring", root=tmp_path)
            return await gather.gather_moment_inputs(owner_user_id=1)

    on = asyncio.run(run(True))
    assert "a held wish stirring" in on.senses_block
    assert on.sense_health.get("daydream") == "ok"

    off = asyncio.run(run(False))
    assert "a held wish stirring" not in off.senses_block
    assert "daydream" not in off.sense_health
