# tests/ai/test_inner_memory_index.py
"""The passive thought-search budget (Batch 3 task 2, 2026-09-27): 40 ms timed
out on 23 of 17 turns on 09-25, so her own past thoughts never reached the
reply. The budget is 1.5 s by default with a 250 ms floor in CODE, so a config
typo (or the old 40) can never re-create the bug."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from ai import inner_memory_index as imi

REPO = Path(__file__).resolve().parents[2]


def _cfg(**fields):
    return lambda: type("C", (), {"enabled": True, **fields})()


def test_query_timeout_default_is_a_real_budget(monkeypatch):
    monkeypatch.setattr(imi, "_config", _cfg(query_timeout_ms=40))
    assert imi._effective_timeout_seconds() >= 0.25  # the floor: 40 ms can never come back
    monkeypatch.setattr(imi, "_config", _cfg())
    assert imi._effective_timeout_seconds() == 1.5


def test_query_timeout_floor_survives_a_typo_and_a_bigger_budget_passes(monkeypatch):
    monkeypatch.setattr(imi, "_config", _cfg(query_timeout_ms=1))
    assert imi._effective_timeout_seconds() == 0.25
    monkeypatch.setattr(imi, "_config", _cfg(query_timeout_ms=3000))
    assert imi._effective_timeout_seconds() == 3.0


def test_query_timeout_unset_values_mean_the_default_not_the_floor(monkeypatch):
    monkeypatch.setattr(imi, "_config", _cfg(query_timeout_ms=0))
    assert imi._effective_timeout_seconds() == 1.5
    monkeypatch.setattr(imi, "_config", _cfg(query_timeout_ms=None))
    assert imi._effective_timeout_seconds() == 1.5


def test_the_no_settings_fallback_ships_the_same_budget(monkeypatch):
    """`_config()` falls back to its own namespace when the settings service
    cannot answer; that namespace must not still say 40."""
    from core.settings_boot import settings_service

    def _broken():
        raise RuntimeError("no settings tonight")

    monkeypatch.setattr(settings_service, "snapshot", _broken)
    assert imi._config().query_timeout_ms == 1500
    assert imi._effective_timeout_seconds() == 1.5


def test_the_schema_and_the_shipped_example_say_the_same_budget():
    """The pydantic default and the example a stranger's first boot copies
    agree; the wrap sets the owner's live yaml by palli_ctl."""
    from core.settings_schema import InnerMemoryConfig

    assert InnerMemoryConfig().query_timeout_ms == 1500
    doc = yaml.safe_load((REPO / "data" / "palli.example.yaml").read_text(encoding="utf-8"))
    assert doc["inner_memory"]["query_timeout_ms"] == 1500


@pytest.mark.asyncio
async def test_query_itself_waits_at_least_the_floor(monkeypatch):
    """The wiring, not the helper: a search that takes 100 ms under a 40 ms
    setting used to come back empty (asyncio.TimeoutError → []); under the
    250 ms floor it returns its hits."""
    hit = imi.IndexHit(
        entry_id="e1", source_kind="monologue", source_path="", timestamp=0.0,
        text_content="the lake page", sentiment_tag="", surfaced_candidate="",
        relevance_score=0.9, age_days=1.0,
    )

    async def slow_query_inner(**_kwargs):
        await asyncio.sleep(0.1)
        return [hit]

    async def no_schema_work(**_kwargs):
        return None

    monkeypatch.setattr(imi, "_config", _cfg(query_timeout_ms=40))
    monkeypatch.setattr(imi, "ensure_schema", no_schema_work)
    monkeypatch.setattr(imi, "_query_inner", slow_query_inner)
    assert await imi.query(text="lake page") == [hit]


@pytest.mark.asyncio
async def test_query_past_its_budget_returns_empty_cleanly_with_one_warning(monkeypatch, caplog):
    """Review Focus 2: a search slower than its budget is cut at the budget
    (the 250 ms floor here), comes back as [] without raising, and says so
    once at WARNING on the index's own logger."""
    import logging
    import time

    async def slower_than_the_budget(**_kwargs):
        await asyncio.sleep(0.5)
        return ["never returned"]

    async def no_schema_work(**_kwargs):
        return None

    monkeypatch.setattr(imi, "_config", _cfg(query_timeout_ms=250))
    monkeypatch.setattr(imi, "ensure_schema", no_schema_work)
    monkeypatch.setattr(imi, "_query_inner", slower_than_the_budget)
    with caplog.at_level(logging.WARNING, logger=imi.logger.name):
        started = time.monotonic()
        hits = await imi.query(text="lake page")
        elapsed = time.monotonic() - started
    assert hits == []
    assert 0.2 <= elapsed < 0.45  # cut at the 250 ms budget, not the 500 ms search
    warnings = [
        r for r in caplog.records
        if r.name == "ai.inner_memory_index" and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1 and "timeout" in warnings[0].getMessage()
