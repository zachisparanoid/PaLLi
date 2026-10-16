"""The memory-of-you search's similarity scoring (Batch 3, task 3 — 1.3).

``_score_embeddings`` is the synchronous cosine pass the search runs off the
event loop via ``asyncio.to_thread``; numpy when importable, plain Python
otherwise, and the two must agree. ``search()`` then puts each result's own
``semantic_score`` on the result — a float in hybrid mode, ``None`` when no
query embedding existed (lexical-only) — so recall can compare the similarity
cutoff against similarity, not the blended score.
"""

from __future__ import annotations

import math
import threading

import pytest

import core.memory_service as ms
from core.memory_service import _cosine_similarity, _score_embeddings, memory_workspace_service


def test_score_embeddings_is_cosine_clamped_at_zero():
    q = [1.0, 0.0]
    out = _score_embeddings(q, {"a": {"vector": [1.0, 0.0]}, "b": {"vector": [0.0, 1.0]}, "c": {"vector": [-1.0, 0.0]}})
    assert abs(out["a"] - 1.0) < 1e-9 and abs(out["b"]) < 1e-9 and out["c"] == 0.0


def test_score_embeddings_scores_unusable_vectors_zero():
    q = [1.0, 0.0]
    out = _score_embeddings(
        q,
        {
            "short": {"vector": [1.0]},
            "long": {"vector": [1.0, 0.0, 0.0]},
            "empty": {"vector": []},
            "missing": {},
            "none": {"vector": None},
            "zero": {"vector": [0.0, 0.0]},
        },
    )
    assert out == {"short": 0.0, "long": 0.0, "empty": 0.0, "missing": 0.0, "none": 0.0, "zero": 0.0}


def test_score_embeddings_empty_query_scores_everything_zero():
    assert _score_embeddings([], {"a": {"vector": [1.0, 0.0]}}) == {"a": 0.0}
    assert _score_embeddings([1.0, 0.0], {}) == {}


def test_numpy_and_pure_python_paths_agree(monkeypatch):
    assert ms._numpy is not None, "numpy is in the venv; the fast path must be the one the bot runs"
    q = [0.3, -0.7, 0.2, 0.9, -0.1]
    embeddings = {
        "same": {"vector": list(q)},
        "close": {"vector": [0.31, -0.69, 0.21, 0.88, -0.12]},
        "far": {"vector": [-0.3, 0.7, -0.2, -0.9, 0.1]},
        "weak": {"vector": [0.7, 0.2, 0.0, 0.0, 0.0]},
        "short": {"vector": [1.0, 2.0]},
        "empty": {"vector": []},
        "ints": {"vector": [1, 2, 3, 4, 5]},
    }
    fast = _score_embeddings(q, embeddings)
    monkeypatch.setattr(ms, "_numpy", None)
    slow = _score_embeddings(q, embeddings)
    assert set(fast) == set(slow) == set(embeddings)
    for item_id in embeddings:
        assert math.isclose(fast[item_id], slow[item_id], abs_tol=1e-9), item_id
        expected = max(0.0, _cosine_similarity(q, list(embeddings[item_id].get("vector") or [])))
        assert math.isclose(fast[item_id], expected, abs_tol=1e-9), item_id
        assert isinstance(fast[item_id], float) and isinstance(slow[item_id], float)
    assert fast["same"] > fast["close"] > fast["weak"] > 0.0
    assert fast["far"] == 0.0 and fast["short"] == 0.0 and fast["empty"] == 0.0


def _install_search_fakes(monkeypatch, *, query_vector):
    async def fake_rebuild(*, owner_user_id: int):
        return {"status": "ok", "owner_user_id": owner_user_id}

    async def fake_search_fts(*, owner_user_id: int, query: str, limit: int):
        return [
            {
                "item_id": "item_lexical",
                "source_kind": "workflow_memory",
                "source_id": "wfm_1",
                "title": "XML workflow memory",
                "content": "We fixed the XML mapping for mining.",
                "snippet": "We fixed the XML mapping for mining.",
                "salience": "high",
                "tags": ["xml"],
                "related_refs": [],
                "content_hash": "hash_1",
                "metadata": {},
                "created_at": 1.0,
                "updated_at": 1000.0,
                "lexical_score": -0.2,
            }
        ]

    async def fake_list_embeddings(*, owner_user_id: int, limit: int):
        return [
            {"item_id": "item_lexical", "vector": [0.0, 1.0], "content_hash": "hash_1"},
            {"item_id": "item_semantic", "vector": [1.0, 0.0], "content_hash": "hash_2"},
        ]

    async def fake_get_item(*, item_id: str):
        if item_id != "item_semantic":
            return None
        return {
            "item_id": "item_semantic",
            "source_kind": "followthrough_item",
            "source_id": "wff_1",
            "title": "Need to revisit mappings",
            "content": "The mapping still needs another pass.",
            "snippet": "The mapping still needs another pass.",
            "salience": "medium",
            "tags": ["mapping"],
            "related_refs": [],
            "updated_at": 990.0,
        }

    async def fake_embed(text: str):
        return list(query_vector)

    async def fake_backfill(*, owner_user_id: int):
        return None

    async def fake_capability():
        return {"mode": "hybrid" if query_vector else "lexical_only"}

    monkeypatch.setattr(memory_workspace_service, "rebuild_index", fake_rebuild)
    monkeypatch.setattr("core.memory_service.brain.search_memory_index_fts", fake_search_fts)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr("core.memory_service.brain.get_memory_index_item", fake_get_item)

    async def fake_list_items(*, owner_user_id: int, limit: int = 200, source_kinds=None):
        # 2026-09-30: the search loads the owner's items in one query now.
        item = await fake_get_item(item_id="item_semantic")
        return [item] if item else []

    monkeypatch.setattr("core.memory_service.brain.list_memory_index_items", fake_list_items)
    monkeypatch.setattr(memory_workspace_service, "_embed_text", fake_embed)
    monkeypatch.setattr(memory_workspace_service, "backfill_owner_embeddings", fake_backfill)
    monkeypatch.setattr(memory_workspace_service, "_search_capability_status", fake_capability)


@pytest.mark.asyncio
async def test_hybrid_search_results_carry_their_own_similarity(monkeypatch):
    """Hybrid mode: every result carries ``semantic_score`` as a float — the
    item's own cosine similarity, not the blended ``score`` — and the cosine
    pass ran off the event loop's thread."""
    _install_search_fakes(monkeypatch, query_vector=[1.0, 0.0])
    scoring_threads: list[threading.Thread] = []
    real_score = ms._score_embeddings

    def recording_score(query_vector, embeddings):
        scoring_threads.append(threading.current_thread())
        return real_score(query_vector, embeddings)

    monkeypatch.setattr(ms, "_score_embeddings", recording_score)

    result = await memory_workspace_service.search(owner_user_id=7, query="xml mapping", limit=4)

    assert result["mode"] == "hybrid"
    by_id = {row["item_id"]: row for row in result["results"]}
    assert set(by_id) == {"item_lexical", "item_semantic"}
    assert isinstance(by_id["item_semantic"]["semantic_score"], float)
    assert by_id["item_semantic"]["semantic_score"] == 1.0
    assert isinstance(by_id["item_lexical"]["semantic_score"], float)
    assert by_id["item_lexical"]["semantic_score"] == 0.0
    # The blended score is a different number from the similarity.
    assert by_id["item_lexical"]["score"] > by_id["item_lexical"]["semantic_score"]
    assert scoring_threads and all(t is not threading.current_thread() for t in scoring_threads)


@pytest.mark.asyncio
async def test_lexical_only_search_results_carry_no_similarity(monkeypatch):
    """Lexical-only mode (no query embedding): ``semantic_score`` is ``None``
    — "no similarity known" is a different fact from "similarity 0"."""
    _install_search_fakes(monkeypatch, query_vector=[])

    result = await memory_workspace_service.search(owner_user_id=7, query="xml mapping", limit=4)

    assert result["mode"] == "lexical_only"
    assert [row["item_id"] for row in result["results"]] == ["item_lexical"]
    assert result["results"][0]["semantic_score"] is None
    assert result["results"][0]["score"] > 0.0


@pytest.mark.asyncio
async def test_the_search_loads_index_items_in_one_query_not_one_per_embedding(monkeypatch):
    """2026-09-30: the meaning search fetched every embedding's item one at a
    time (a new connection each), about 1,100 round trips once Batch 5 indexed
    her memory lines: ~7 s per search, over recall's 5 s and introspect's 2 s,
    so her automatic meaning search timed out on every turn from 09-29 (56 of
    53 turns; 0 of 65 on 09-28). It must load the owner's items in ONE query
    and rank exactly as before."""
    _install_search_fakes(monkeypatch, query_vector=[1.0, 0.0])
    n = 300
    items = {
        f"item_{i}": {
            "item_id": f"item_{i}", "source_kind": "memory_bullet", "source_id": f"b{i}",
            "title": f"line {i}", "content": f"memory line {i}", "snippet": f"memory line {i}",
            "salience": "low", "tags": [], "related_refs": [], "updated_at": 500.0 + i,
        }
        for i in range(n)
    }
    vectors = {f"item_{i}": [1.0, i / n] for i in range(n)}   # nearer the query as i falls

    async def fake_list_embeddings(*, owner_user_id: int, limit: int):
        return [{"item_id": k, "vector": v, "content_hash": k} for k, v in vectors.items()]

    per_item_calls = {"n": 0}
    list_calls = {"n": 0}

    async def counting_get_item(*, item_id: str):
        per_item_calls["n"] += 1
        return items.get(item_id)

    async def fake_list_items(*, owner_user_id: int, limit: int = 200, source_kinds=None):
        list_calls["n"] += 1
        return list(items.values())[: int(limit)]

    async def no_lexical(**kwargs):
        return []

    monkeypatch.setattr("core.memory_service.brain.search_memory_index_fts", no_lexical)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr("core.memory_service.brain.get_memory_index_item", counting_get_item)
    monkeypatch.setattr("core.memory_service.brain.list_memory_index_items", fake_list_items)

    result = await memory_workspace_service.search(owner_user_id=7, query="memory line", limit=5)

    assert per_item_calls["n"] == 0
    assert list_calls["n"] == 1
    top = [row["item_id"] for row in result["results"]]
    assert len(top) == 5
    # the closest vectors (smallest i) rank first, as the per-item version did
    assert top[0] == "item_0"
    assert all(row["semantic_score"] > 0.99 for row in result["results"])
