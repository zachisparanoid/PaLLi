"""Batch 5, task 8 (memory that picks, 2026-09-28; ruling R48): the thread
board sorts by MEANING bins, not exact strings. The writer records urgency
as free text — live, 227 active rows: 24 start "time-sensitive", 99 start
"no urgency", 63 are "when-the-moment-fits", none carry the legacy keys —
and the old sort key knew only high/urgent/medium/normal/low, so every live
row fell into the middle bin and the board was ordered by age alone.
``_urgency_bin`` is presentation into three bins (Law 3: meaning stays
with the writer; ordering is code); the legacy keys still sort.
"""
from __future__ import annotations

import time
import types

import pytest

import ai.present_moment_gather as gather
from ai.present_moment_gather import _urgency_bin


def test_free_text_urgency_falls_into_three_bins():
    assert _urgency_bin("time-sensitive — same-day prescription pickup") == 0
    assert _urgency_bin("when-the-moment-fits") == 1
    assert _urgency_bin("no urgency — they're circling, not deciding") == 2
    assert _urgency_bin("") == 1


def test_bins_are_case_insensitive_on_stripped_text():
    assert _urgency_bin("  Time-Sensitive: the vet closes at five ") == 0
    assert _urgency_bin("NO URGENCY") == 2
    assert _urgency_bin("When-the-moment-fits — a book they might like") == 1
    assert _urgency_bin(None) == 1
    assert _urgency_bin("something the writer made up") == 1


def test_legacy_keys_still_sort():
    assert _urgency_bin("high") == 0 and _urgency_bin("urgent") == 0
    assert _urgency_bin("medium") == 1 and _urgency_bin("normal") == 1
    assert _urgency_bin("low") == 2
    assert _urgency_bin("Low") == 2


def _thread(i, *, urgency, age_days, summary):
    return types.SimpleNamespace(
        id=f"{i:0>8x}" + "0" * 24, kind=types.SimpleNamespace(value="personal"),
        urgency=urgency, created_at=time.time() - age_days * 86400,
        topic_summary=summary, why_it_matters="",
    )


@pytest.mark.asyncio
async def test_the_board_sorts_time_sensitive_first_no_urgency_last_and_ties_youngest_first(monkeypatch):
    rows = [
        _thread(1, urgency="no urgency — they mentioned it once", age_days=2, summary="ALPHA"),
        _thread(2, urgency="when-the-moment-fits", age_days=9, summary="BRAVO"),
        _thread(3, urgency="time-sensitive — the form is due Friday", age_days=30, summary="CHARLIE"),
        _thread(4, urgency="when-the-moment-fits", age_days=1, summary="DELTA"),
        _thread(5, urgency="time-sensitive — prescription pickup", age_days=3, summary="ECHO"),
        _thread(6, urgency="no urgency", age_days=60, summary="FOXTROT"),
        _thread(7, urgency="high", age_days=12, summary="GOLF"),   # a legacy row still lands in bin 0
    ]

    async def _actives(*, owner_user_id):
        return list(rows)

    monkeypatch.setattr(gather, "_list_active_threads", _actives)
    board = await gather._threads(owner_user_id=1)
    order = [name for name in ("ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF")]
    order.sort(key=lambda n: board.index(n))
    # bin 0 youngest first (ECHO 3d, GOLF 12d, CHARLIE 30d), then bin 1
    # (DELTA 1d, BRAVO 9d), then bin 2 (ALPHA 2d, FOXTROT 60d)
    assert order == ["ECHO", "GOLF", "CHARLIE", "DELTA", "BRAVO", "ALPHA", "FOXTROT"]
    # the digest still prints the writer's words, not the bin
    assert "| time-sensitive — prescription pickup |" in board
    assert "| no urgency |" in board
