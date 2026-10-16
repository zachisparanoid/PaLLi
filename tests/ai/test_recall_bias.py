# tests/ai/test_recall_bias.py
"""Recall bias (spec §6, read half): congruent-sentiment entries multiply by
recall_bias_boost; never excludes; level/unknown ⇒ no change; flag off or a
stale moment ⇒ ranking byte-identical."""
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio

import ai.inner_memory_index as imi
from ai.inner_monologue_store import MonologueEntry
from core.database import PalliBrain


@pytest_asyncio.fixture
async def brain(tmp_path):
    db = PalliBrain(str(tmp_path / "idx.sqlite3"))
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


def _entry(thought, ts, tag):
    return MonologueEntry(timestamp=ts, thought=thought, situation_snapshot={},
                          tick_metadata={"substantive": True},
                          sentiment={"tag": tag, "read": "", "confidence": 0.8})


@pytest.mark.asyncio
async def test_warm_lean_lifts_warm_entries(brain, monkeypatch):
    now = time.time()
    await imi.index_entry(_entry("the harbor note tired version", now - 60, "tired"), brain_client=brain)
    await imi.index_entry(_entry("the harbor note content version", now - 120, "content"), brain_client=brain)
    monkeypatch.setattr(imi, "_recall_bias_lean", lambda: "warm")
    monkeypatch.setattr(imi, "_recall_bias_boost", lambda: 1.15)
    hits = await imi.query(text="harbor note", max_results=2, brain_client=brain)
    assert "content version" in hits[0].text_content   # newer tired row would win without the lift
    monkeypatch.setattr(imi, "_recall_bias_lean", lambda: None)
    hits = await imi.query(text="harbor note", max_results=2, brain_client=brain)
    assert "tired version" in hits[0].text_content     # flag off ⇒ recency wins again


def test_lean_reader_gates(monkeypatch):
    # The root conftest pins _recall_bias_lean itself (the 3352e3d law), so
    # the gate logic is tested through its _impl alias — the introspection/
    # passions house pattern for gate functions pinned at the root.
    comp_on = SimpleNamespace(moment_recall_bias=True, recall_bias_boost=1.15)
    with patch("core.turn.companion_flags.companion_settings", return_value=comp_on), \
         patch("ai.present_moment_store.read_current",
               return_value=(SimpleNamespace(meta={"lean": "heavy"}), 60.0, False)):
        assert imi._recall_bias_lean_impl() == "heavy"
    with patch("core.turn.companion_flags.companion_settings", return_value=comp_on), \
         patch("ai.present_moment_store.read_current",
               return_value=(SimpleNamespace(meta={"lean": "heavy"}), 60.0, True)):
        assert imi._recall_bias_lean_impl() is None     # stale moment ⇒ no bias
    comp_off = SimpleNamespace(moment_recall_bias=False)
    with patch("core.turn.companion_flags.companion_settings", return_value=comp_off):
        assert imi._recall_bias_lean_impl() is None
