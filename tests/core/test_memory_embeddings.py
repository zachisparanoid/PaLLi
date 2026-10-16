"""Memory embedding path: off-loop, pre-warmed, out-of-band backlog (2026-06-13).

Root cause of the "PaLLi feels generic / forgets" incident: semantic recall
embedded the query with a SYNCHRONOUS sentence-transformers call on the
asyncio event loop, and on every search it also back-filled embeddings for
every un-embedded memory item inline. With a ~9.3s cold import + ~0.44s/item
× 74 items, every search blew recall's 5s timeout AND froze the whole bot.

These tests pin the fix: embedding runs off the loop, the model pre-warms,
and the per-turn search schedules the backlog drain in the background instead
of blocking on it.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import core.memory_service as ms
from core.memory_service import MemoryWorkspaceService


@pytest.mark.asyncio
async def test_local_embedding_runs_off_the_event_loop():
    """The blocking encode must run in a thread so it doesn't freeze the loop.
    Proof: a concurrent ticker keeps advancing WHILE the (blocking) encode
    runs — so several ticks land before the encode finishes. If the encode
    ran on the loop, 'encode_done' would be first."""
    svc = MemoryWorkspaceService()
    order: list[str] = []

    class FakeModel:
        def encode(self, text, normalize_embeddings=False):
            time.sleep(0.25)  # blocking, like the real sentence-transformers
            order.append("encode_done")
            return [0.1, 0.2, 0.3]

    svc._local_model = FakeModel()
    svc._local_model_name = ms.config.MEMORY_LOCAL_EMBEDDINGS_MODEL

    async def ticker():
        for _ in range(8):
            await asyncio.sleep(0.03)
            order.append("tick")

    vec, _ = await asyncio.gather(svc._embed_text_local("hello"), ticker())
    assert vec == [0.1, 0.2, 0.3]
    assert "encode_done" in order
    assert order.index("encode_done") >= 3, f"encode blocked the loop: {order}"


@pytest.mark.asyncio
async def test_local_model_loads_once_and_is_cached(monkeypatch):
    """The model is constructed once and reused — no reload per embed."""
    svc = MemoryWorkspaceService()
    constructed: list[str] = []

    class FakeST:
        def __init__(self, name):
            constructed.append(name)

        def encode(self, text, normalize_embeddings=False):
            return [0.5]

    monkeypatch.setattr("sentence_transformers.SentenceTransformer", FakeST)
    v1 = await svc._embed_text_local("a")
    v2 = await svc._embed_text_local("b")
    assert v1 == [0.5] and v2 == [0.5]
    assert len(constructed) == 1, f"model rebuilt per call: {constructed}"


@pytest.mark.asyncio
async def test_prewarm_loads_model_for_turn_one(monkeypatch):
    """prewarm_local_embeddings() loads the model at startup so the first
    per-turn recall doesn't pay the cold import (which blew the 5s timeout)."""
    svc = MemoryWorkspaceService()

    class FakeST:
        def __init__(self, name):
            self.name = name

        def encode(self, *a, **k):
            return [1.0]

    monkeypatch.setattr("sentence_transformers.SentenceTransformer", FakeST)
    assert svc._local_model is None
    ready = await svc.prewarm_local_embeddings()
    assert ready is True
    assert svc._local_model is not None


@pytest.mark.asyncio
async def test_backfill_embeds_only_missing_items(monkeypatch):
    """The backlog drain embeds items whose vector is missing/stale and
    skips ones already current (by content_hash)."""
    svc = MemoryWorkspaceService()

    class FakeModel:
        def encode(self, text, normalize_embeddings=False):
            return [0.9]

    svc._local_model = FakeModel()
    svc._local_model_name = ms.config.MEMORY_LOCAL_EMBEDDINGS_MODEL

    items = [
        {"item_id": "a", "content": "alpha", "content_hash": "ha"},
        {"item_id": "b", "content": "bravo", "content_hash": "hb"},
        {"item_id": "c", "content": "charlie", "content_hash": "hc"},
    ]
    existing = [{"item_id": "a", "content_hash": "ha"}]  # only 'a' is current
    upserted: list[str] = []

    async def fake_list_items(**k):
        return items

    async def fake_list_embeddings(**k):
        return existing

    async def fake_upsert(**k):
        upserted.append(k["item_id"])

    monkeypatch.setattr(ms.brain, "list_memory_index_items", fake_list_items)
    monkeypatch.setattr(ms.brain, "list_memory_index_embeddings", fake_list_embeddings)
    monkeypatch.setattr(ms.brain, "upsert_memory_index_embedding", fake_upsert)

    await svc.backfill_owner_embeddings(owner_user_id=7)
    assert sorted(upserted) == ["b", "c"]


@pytest.mark.asyncio
async def test_search_schedules_backfill_without_blocking_the_turn(monkeypatch):
    """The per-turn search must NOT await the full backlog drain — it embeds
    the query and scores against cached vectors, scheduling the backfill as a
    background task. (Was: a synchronous loop that froze the turn.)"""
    svc = MemoryWorkspaceService()

    async def noop_rebuild(**k):
        return {"status": "ok"}

    async def fake_fts(**k):
        return []

    async def fake_list_emb(**k):
        return []

    async def fast_embed(text):
        return [0.1, 0.2]

    monkeypatch.setattr(svc, "rebuild_index", noop_rebuild)
    monkeypatch.setattr(ms.brain, "search_memory_index_fts", fake_fts)
    monkeypatch.setattr(ms.brain, "list_memory_index_embeddings", fake_list_emb)
    monkeypatch.setattr(svc, "_embed_text", fast_embed)

    state = {"scheduled": False, "done": False}

    async def slow_backfill(*, owner_user_id):
        state["scheduled"] = True
        await asyncio.sleep(0.3)
        state["done"] = True

    monkeypatch.setattr(svc, "backfill_owner_embeddings", slow_backfill, raising=False)

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await svc.search(owner_user_id=7, query="hello", limit=3)
    elapsed = loop.time() - t0

    assert elapsed < 0.2, f"search blocked on the backlog ({elapsed:.2f}s)"
    # The backfill is a fire-and-forget background task — give the loop a beat
    # to start and finish it, then confirm it ran (scheduled, not awaited).
    await asyncio.sleep(0.5)
    assert state["scheduled"] is True, "search did not schedule the backfill"
    assert state["done"] is True, "background backfill never ran to completion"


@pytest.mark.asyncio
async def test_search_does_not_block_on_index_rebuild(monkeypatch):
    """search() must NOT await the full index rebuild (~24s for 133 items in
    prod) — that blew recall's 5s timeout even after the embedding fix, so the
    semantic-by-meaning channel still timed out every turn. The rebuild is
    scheduled in the background; the search scores the current index now."""
    svc = MemoryWorkspaceService()

    rebuild = {"started": False, "done": False}

    async def slow_rebuild(*, owner_user_id, **k):
        rebuild["started"] = True
        await asyncio.sleep(0.5)
        rebuild["done"] = True
        return {"status": "ok"}

    async def fake_fts(**k):
        return []

    async def fake_list_emb(**k):
        return []

    async def fast_embed(text):
        return [0.1, 0.2]

    async def noop_backfill(*, owner_user_id):
        return None

    async def fake_capability():
        return {"mode": "hybrid"}

    monkeypatch.setattr(svc, "rebuild_index", slow_rebuild)
    monkeypatch.setattr(ms.brain, "search_memory_index_fts", fake_fts)
    monkeypatch.setattr(ms.brain, "list_memory_index_embeddings", fake_list_emb)
    monkeypatch.setattr(svc, "_embed_text", fast_embed)
    monkeypatch.setattr(svc, "backfill_owner_embeddings", noop_backfill, raising=False)
    monkeypatch.setattr(svc, "_search_capability_status", fake_capability)

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await svc.search(owner_user_id=7, query="hi", limit=3)
    elapsed = loop.time() - t0

    assert elapsed < 0.2, f"search blocked on the index rebuild ({elapsed:.2f}s)"
    await asyncio.sleep(0.8)
    assert rebuild["started"] is True, "search did not schedule the index rebuild"
    assert rebuild["done"] is True, "background index rebuild never completed"


@pytest.mark.asyncio
async def test_the_cold_import_runs_off_the_loop(monkeypatch):
    """DEFECT_RECORD §12 (2026-08-28): the sentence-transformers IMPORT (torch,
    ~9 s cold) ran on the event loop, before the model load was handed to a
    thread — at boot that starved the gateway and his 12:08 message was
    lost. The import must resolve on the worker thread, never on the loop."""
    import sys
    import threading
    import types

    svc = MemoryWorkspaceService()
    loop_thread = threading.get_ident()
    seen: list[int] = []

    class FakeST:
        def __init__(self, name):
            pass

        def encode(self, text, normalize_embeddings=False):
            return [0.25]

    class Probe(types.ModuleType):
        def __getattribute__(self, name):
            if name == "SentenceTransformer":
                seen.append(threading.get_ident())
                return FakeST
            return super().__getattribute__(name)

    monkeypatch.setitem(sys.modules, "sentence_transformers", Probe("sentence_transformers"))
    assert await svc._embed_text_local("a") == [0.25]
    assert seen, "the model class was never resolved"
    assert all(t != loop_thread for t in seen), "the sentence-transformers import ran on the event loop"


def test_no_coroutine_imports_sentence_transformers_on_the_loop():
    """§12 as a class: no `import sentence_transformers` statement may sit
    directly in a coroutine body in memory_service.py — only inside a nested
    plain function that a thread runs. The readiness probe included."""
    import ast
    from pathlib import Path

    src = Path("core/memory_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[int] = []

    def _scan(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # a nested def is somebody else's thread (or its own scan below)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) else [])
                if any(n and n.startswith("sentence_transformers") for n in names):
                    offenders.append(node.lineno)
            for child in ast.iter_child_nodes(node):
                if hasattr(child, "body") and isinstance(getattr(child, "body"), list):
                    _scan(child.body)
                for attr in ("orelse", "finalbody", "handlers"):
                    part = getattr(child, attr, None)
                    if isinstance(part, list):
                        _scan([h for h in part if not isinstance(h, ast.ExceptHandler)])
                        for h in part:
                            if isinstance(h, ast.ExceptHandler):
                                _scan(h.body)

    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef):
            _scan(fn.body)
    assert not offenders, f"sentence_transformers imported on the loop at lines {offenders}"


# --- Final fix wave (2026-09-26), item 3: the remote fallback is sealed -----
# When the local model is unavailable, memory text is posted to OpenAI's
# embeddings endpoint. That is a provider too: the text goes through
# for_provider first, like every other request. Both values are fake.

_FAKE_SSN = "123-" + "45-" + "6789"
_FAKE_KEY = "gsk_" + "Fake0Key1" * 3


@pytest.mark.asyncio
async def test_remote_embedding_never_sends_a_secret_shaped_value(monkeypatch):
    sent: list[dict] = []

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def json(self):
            return {"data": [{"embedding": [0.5, 0.25]}]}

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, *, headers=None, json=None, timeout=None):
            sent.append({"url": url, "json": json})
            return _Response()

    monkeypatch.setattr(ms.aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(ms.config, "OPENAI_API_KEY", "not-a-real-openai-key", raising=False)

    svc = MemoryWorkspaceService()
    vector = await svc._embed_text_remote(f"his ssn is {_FAKE_SSN} and the key is {_FAKE_KEY}")

    assert vector == [0.5, 0.25]
    assert len(sent) == 1
    body = repr(sent[0]["json"])
    assert _FAKE_SSN not in body and _FAKE_KEY not in body
    assert "[MASKED:" in sent[0]["json"]["input"]
