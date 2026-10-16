"""Phase 4 tests — monologue-fed candidate retrieval & consumed-state dedup."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ai.inner_monologue_store import (
    MonologueEntry,
    read_recent_with_candidates,
    write_entry,
)
from ai.proactive_candidate_source import (
    EligibleCandidate,
    _format_tick_timestamp,
    get_eligible_candidate,
)


@pytest.fixture
def isolated_monologue_dir(tmp_path, monkeypatch):
    """Redirect MONOLOGUE_DIR at the module to a tmp_path so tests don't
    see each other's JSONL writes and don't pollute real data."""
    import ai.inner_monologue_store as store_mod
    monkeypatch.setattr(store_mod, "MONOLOGUE_DIR", tmp_path)
    return tmp_path


def _write_entry(
    store_dir: Path,
    *,
    timestamp: float | None = None,
    thought: str = "x",
    proactive_candidate: str | None = None,
) -> MonologueEntry:
    entry = MonologueEntry(
        timestamp=timestamp if timestamp is not None else time.time(),
        thought=thought,
        situation_snapshot={},
        tick_metadata={"substantive": True},
        sentiment=None,
        proactive_candidate=proactive_candidate,
    )
    write_entry(entry)
    return entry


# ─── read_recent_with_candidates ──────────────────────────────────────


def test_read_recent_with_candidates_returns_only_entries_with_candidates(
    isolated_monologue_dir,
):
    _write_entry(isolated_monologue_dir, proactive_candidate=None)
    _write_entry(isolated_monologue_dir, proactive_candidate="check broker update")
    _write_entry(isolated_monologue_dir, proactive_candidate=None)

    out = read_recent_with_candidates(max_age_hours=24.0)
    assert len(out) == 1
    assert out[0].proactive_candidate == "check broker update"


def test_read_recent_with_candidates_filters_by_staleness(
    isolated_monologue_dir,
):
    """Assertion 4: retrieval ignores stale candidates."""
    long_ago = time.time() - (48 * 3600)  # 2 days ago
    _write_entry(isolated_monologue_dir, timestamp=long_ago, proactive_candidate="stale")
    _write_entry(isolated_monologue_dir, proactive_candidate="fresh")

    out = read_recent_with_candidates(max_age_hours=6.0)
    assert [e.proactive_candidate for e in out] == ["fresh"]


def test_read_recent_with_candidates_newest_first(isolated_monologue_dir):
    earliest = time.time() - 1800
    middle = time.time() - 600
    latest = time.time() - 60
    _write_entry(
        isolated_monologue_dir, timestamp=earliest, proactive_candidate="first",
    )
    _write_entry(
        isolated_monologue_dir, timestamp=latest, proactive_candidate="third",
    )
    _write_entry(
        isolated_monologue_dir, timestamp=middle, proactive_candidate="second",
    )

    out = read_recent_with_candidates(max_age_hours=24.0)
    assert [e.proactive_candidate for e in out] == ["third", "second", "first"]


def test_read_recent_with_candidates_empty_when_none_have_candidates(
    isolated_monologue_dir,
):
    _write_entry(isolated_monologue_dir, proactive_candidate=None)
    _write_entry(isolated_monologue_dir, proactive_candidate=None)

    out = read_recent_with_candidates(max_age_hours=24.0)
    assert out == []


# ─── get_eligible_candidate ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_eligible_candidate_returns_most_recent_unconsumed(
    isolated_monologue_dir,
):
    """Assertion: most-recent unconsumed candidate wins."""
    _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 1200,
        proactive_candidate="older",
    )
    _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 60,
        proactive_candidate="newer",
    )

    with patch("ai.proactive_candidate_source.brain") as mock_brain:
        mock_brain.list_recent_proactive_actions_for_user = AsyncMock(
            return_value=[],
        )
        out = await get_eligible_candidate(owner_user_id=42)

    assert out is not None
    assert out.text == "newer"


@pytest.mark.asyncio
async def test_get_eligible_candidate_skips_consumed_candidates(
    isolated_monologue_dir,
):
    """If most-recent is consumed, walk back to older unconsumed one."""
    newer = _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 60,
        proactive_candidate="newer_consumed",
    )
    _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 1200,
        proactive_candidate="older_fresh",
    )

    consumed_row = {
        "trigger_reason": json.dumps({
            "source": "monologue_candidate",
            "candidate_tick_timestamp": _format_tick_timestamp(newer.timestamp),
        }),
    }

    with patch("ai.proactive_candidate_source.brain") as mock_brain:
        mock_brain.list_recent_proactive_actions_for_user = AsyncMock(
            return_value=[consumed_row],
        )
        out = await get_eligible_candidate(owner_user_id=42)

    assert out is not None
    assert out.text == "older_fresh"


@pytest.mark.asyncio
async def test_get_eligible_candidate_returns_none_when_window_zero(
    isolated_monologue_dir,
):
    """Zero staleness → feature fully disabled (useful for rollback)."""
    _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 60,
        proactive_candidate="x",
    )
    out = await get_eligible_candidate(
        owner_user_id=42, staleness_hours=0.0,
    )
    assert out is None


@pytest.mark.asyncio
async def test_get_eligible_candidate_returns_none_when_no_fresh_candidates(
    isolated_monologue_dir,
):
    long_ago = time.time() - (48 * 3600)
    _write_entry(
        isolated_monologue_dir,
        timestamp=long_ago,
        proactive_candidate="stale",
    )

    out = await get_eligible_candidate(
        owner_user_id=42, staleness_hours=6.0,
    )
    assert out is None


@pytest.mark.asyncio
async def test_get_eligible_candidate_fails_closed_on_db_error(
    isolated_monologue_dir,
):
    """Fail-closed policy: if consumed-check fails, treat as consumed so
    we don't risk sending the same candidate twice on a transient glitch."""
    _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 60,
        proactive_candidate="x",
    )
    with patch("ai.proactive_candidate_source.brain") as mock_brain:
        mock_brain.list_recent_proactive_actions_for_user = AsyncMock(
            side_effect=RuntimeError("db down"),
        )
        out = await get_eligible_candidate(owner_user_id=42)

    assert out is None


@pytest.mark.asyncio
async def test_get_eligible_candidate_returns_none_when_all_consumed(
    isolated_monologue_dir,
):
    """If every fresh candidate has been consumed, return None."""
    a = _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 60,
        proactive_candidate="a",
    )
    b = _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 1800,
        proactive_candidate="b",
    )

    consumed_rows = [
        {"trigger_reason": json.dumps({
            "source": "monologue_candidate",
            "candidate_tick_timestamp": _format_tick_timestamp(a.timestamp),
        })},
        {"trigger_reason": json.dumps({
            "source": "monologue_candidate",
            "candidate_tick_timestamp": _format_tick_timestamp(b.timestamp),
        })},
    ]

    with patch("ai.proactive_candidate_source.brain") as mock_brain:
        mock_brain.list_recent_proactive_actions_for_user = AsyncMock(
            return_value=consumed_rows,
        )
        out = await get_eligible_candidate(owner_user_id=42)

    assert out is None


@pytest.mark.asyncio
async def test_get_eligible_candidate_ignores_non_candidate_rows(
    isolated_monologue_dir,
):
    """Legacy proactive_actions rows (no source field, no candidate ts)
    must not interfere with the consumed-check."""
    _write_entry(
        isolated_monologue_dir,
        timestamp=time.time() - 60,
        proactive_candidate="new candidate",
    )

    rows = [
        {"trigger_reason": None},  # truly legacy
        {"trigger_reason": ""},     # empty
        {"trigger_reason": "not json at all"},  # parse failure
        {"trigger_reason": json.dumps({"source": "fallback_scan"})},
    ]

    with patch("ai.proactive_candidate_source.brain") as mock_brain:
        mock_brain.list_recent_proactive_actions_for_user = AsyncMock(
            return_value=rows,
        )
        out = await get_eligible_candidate(owner_user_id=42)

    assert out is not None
    assert out.text == "new candidate"
