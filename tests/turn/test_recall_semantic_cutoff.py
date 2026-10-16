"""The similarity cutoff compares similarity (Batch 3, task 3 — 1.3).

Recall's ``semantic_recall_min_similarity`` used to be compared against the
memory-of-you search's blended ``score`` (0.55·lexical + 0.35·semantic +
bonuses), so a keyword coincidence passed and a true similarity hit could
fail. ``_passes_semantic_cutoff`` compares the result's own ``semantic_score``;
a result that reports no similarity is not a semantic hit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from core.turn import recall
from core.turn.recall import _passes_semantic_cutoff
from core.turn.types import Turn, TurnMetadata
from tests.turn.test_recall_parallel import _flags, _stub_clean


def test_keyword_coincidence_is_dropped_and_true_similarity_kept():
    assert not _passes_semantic_cutoff({"score": 0.53, "semantic_score": 0.10}, min_sim=0.3)
    assert _passes_semantic_cutoff({"score": 0.35, "semantic_score": 0.80}, min_sim=0.3)
    assert _passes_semantic_cutoff({"score": 0.9}, min_sim=0.3) is False  # no similarity known → not a semantic hit


def test_unknown_similarity_is_never_a_hit():
    assert _passes_semantic_cutoff({"score": 0.9, "semantic_score": None}, min_sim=0.3) is False
    assert _passes_semantic_cutoff({"score": 0.9, "semantic_score": "high"}, min_sim=0.3) is False
    assert _passes_semantic_cutoff({}, min_sim=0.0) is False


def test_cutoff_is_inclusive_and_reads_numeric_strings():
    assert _passes_semantic_cutoff({"semantic_score": 0.3}, min_sim=0.3) is True
    assert _passes_semantic_cutoff({"semantic_score": 0.2999}, min_sim=0.3) is False
    assert _passes_semantic_cutoff({"semantic_score": "0.5"}, min_sim=0.3) is True
    assert _passes_semantic_cutoff({"semantic_score": 0.5}, min_sim="0.3") is True


def _turn(content: str) -> Turn:
    # An explicit naive-UTC stamp: the field's default factory is the
    # deprecated ``datetime.utcnow`` and would warn.
    return Turn(
        surface="dashboard",
        owner_id=7,
        content=content,
        images=[],
        metadata=TurnMetadata(),
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


@pytest.mark.asyncio
async def test_recall_drops_keyword_coincidence_and_keeps_true_similarity(monkeypatch):
    """End to end through recall: the search ranks by blended score, and the
    merge keeps ranking order but admits only results whose own similarity
    clears the cutoff (0.3 by default)."""
    _stub_clean(monkeypatch, flags=_flags(semantic_recall=True))
    import core.memory_service as ms
    monkeypatch.setattr(ms.memory_workspace_service, "search", AsyncMock(return_value={
        "results": [
            {"score": 0.61, "semantic_score": 0.12, "snippet": "keyword coincidence"},
            {"score": 0.40, "semantic_score": 0.81, "snippet": "true hit, lower blended score"},
            {"score": 0.38, "semantic_score": 0.30, "snippet": "right at the cutoff"},
            {"score": 0.90, "snippet": "no similarity known"},
        ],
    }))
    ctx = await recall.build_turn_context(_turn("hi"))
    assert ctx.recent_memory_hits == ("true hit, lower blended score", "right at the cutoff")


# ── Fix round 1 (ruling R14): the cutoff judges a real candidate set ──────
# Recall used to ask the search for only ``max_hits`` results, ranked by the
# blended score, and apply the similarity cutoff to that window. The blend's
# bonuses (salience, recency, continuity …) can outrank similarity in the top
# three, so a true match ranked fifth never reached the cutoff. The fake here
# HONOURS ``limit`` — that is what makes the window visible.


def _limited_search(results: list[dict]):
    """A ``search`` fake that records its kwargs and honours ``limit``."""
    captured: dict = {}

    async def fake_search(**kwargs):
        captured.update(kwargs)
        return {"results": list(results)[: int(kwargs["limit"])]}

    return fake_search, captured


_EIGHT_RANKED_ONE_TRUE_MATCH_FIFTH = [
    {"score": 0.80, "semantic_score": 0.10, "snippet": "one"},
    {"score": 0.75, "semantic_score": 0.12, "snippet": "two"},
    {"score": 0.70, "semantic_score": 0.05, "snippet": "three"},
    {"score": 0.65, "semantic_score": 0.20, "snippet": "four"},
    {"score": 0.60, "semantic_score": 0.85, "snippet": "the true match, fifth by blended score"},
    {"score": 0.55, "semantic_score": 0.15, "snippet": "six"},
    {"score": 0.50, "semantic_score": 0.10, "snippet": "seven"},
    {"score": 0.45, "semantic_score": 0.02, "snippet": "eight"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("max_hits", [1, 3, 5])
async def test_recall_asks_the_search_for_a_real_candidate_set(monkeypatch, max_hits):
    _stub_clean(monkeypatch, flags=_flags(semantic_recall=True, semantic_recall_max_hits=max_hits))
    import core.memory_service as ms
    fake_search, captured = _limited_search([])
    monkeypatch.setattr(ms.memory_workspace_service, "search", fake_search)
    await recall.build_turn_context(_turn("hi"))
    assert captured["limit"] >= 8
    assert captured["limit"] >= 4 * max_hits


@pytest.mark.asyncio
async def test_recall_keeps_a_true_match_ranked_fifth_by_blended_score(monkeypatch):
    _stub_clean(monkeypatch, flags=_flags(semantic_recall=True))
    import core.memory_service as ms
    fake_search, _ = _limited_search(_EIGHT_RANKED_ONE_TRUE_MATCH_FIFTH)
    monkeypatch.setattr(ms.memory_workspace_service, "search", fake_search)
    ctx = await recall.build_turn_context(_turn("hi"))
    assert ctx.recent_memory_hits == ("the true match, fifth by blended score",)


@pytest.mark.asyncio
async def test_recall_appends_at_most_max_hits_even_with_eight_passing(monkeypatch):
    _stub_clean(monkeypatch, flags=_flags(semantic_recall=True, semantic_recall_max_hits=3))
    import core.memory_service as ms
    eight_passing = [
        {"score": 0.9 - i * 0.05, "semantic_score": 0.9, "snippet": f"hit {i}"} for i in range(8)
    ]
    fake_search, _ = _limited_search(eight_passing)
    monkeypatch.setattr(ms.memory_workspace_service, "search", fake_search)
    ctx = await recall.build_turn_context(_turn("hi"))
    assert ctx.recent_memory_hits == ("hit 0", "hit 1", "hit 2")
