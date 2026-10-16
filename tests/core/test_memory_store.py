from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.memory_store import MemoryStore


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchrow_results: list[Any] | None = None,
        fetch_results: list[Any] | None = None,
    ) -> None:
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_results = list(fetch_results or [])
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.fetchrow_calls.append((query, args))
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return None

    async def fetch(self, query: str, *args: Any) -> Any:
        self.fetch_calls.append((query, args))
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return "OK"

    async def close(self) -> None:
        self.closed = True


class _FakeBrain:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def get_connection(self) -> _FakeConnection:
        return self._connection

    @staticmethod
    def _load_json(raw: Any, fallback: Any) -> Any:
        if raw is None:
            return fallback
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return fallback

    @staticmethod
    def _placeholders(count: int, start: int = 1) -> str:
        return ",".join(f"${index}" for index in range(start, start + count))


def _make_memory_index_item_row() -> tuple[Any, ...]:
    row = (
        "item_1",
        7,
        "workflow_memory",
        "mem_1",
        "Workflow memory",
        "Fixed the XML mapping workflow.",
        "Fixed the XML mapping workflow.",
        "high",
        json.dumps(["workflow", "completed"]),
        json.dumps(["workflow:wf_1"]),
        "hash_1",
        json.dumps({"source": "operator"}),
        1000.0,
        1100.0,
    )
    assert len(row) == 14
    return row


def _make_memory_index_search_row() -> tuple[Any, ...]:
    row = _make_memory_index_item_row() + (-0.42,)
    assert len(row) == 15
    return row


@pytest.mark.asyncio
async def test_store_user_memory_serializes_tags_and_timestamps() -> None:
    connection = _FakeConnection(fetchrow_results=[(1,)])
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.store_user_memory(
        user_id=7,
        memory_type="preference",
        content="Keep replies short.",
        importance=0.9,
        tags=["preference", "communication"],
    )

    assert connection.closed is True
    _, args = connection.fetchrow_calls[0]
    assert args[0:6] == (7, "preference", "Keep replies short.", 0.9, 2500.0, 2500.0)
    assert json.loads(args[6]) == ["preference", "communication"]
    assert args[7:] == (2500.0, 1.0, "{}")


# Phase B / B4 step 5 — delete_user_memory_by_source pins the SQL
# shape so the rebuild path scrubs only its own derived rows.
class _FakeBrainWithRowcount(_FakeBrain):
    @staticmethod
    def _rowcount(status: str) -> int:
        # Mirror the real PalliBrain rowcount parser: split on space,
        # take the trailing integer if present (postgres-style
        # "DELETE 5"), else 0.
        if not status:
            return 0
        parts = status.split()
        if not parts:
            return 0
        last = parts[-1]
        return int(last) if last.isdigit() else 0


class _FakeConnectionWithStatus(_FakeConnection):
    def __init__(self, *, status: str = "OK", **kwargs):
        super().__init__(**kwargs)
        self._status = status

    async def execute(self, query: str, *args: Any) -> str:  # type: ignore[override]
        self.execute_calls.append((query, args))
        return self._status


@pytest.mark.asyncio
async def test_delete_user_memory_by_source_scrubs_via_source_detail() -> None:
    """Pin the SQL shape: WHERE user_id = $1 AND source_detail->>'source' = $2.

    The runtime adapter (``core/database.py::_adapt_sql``) translates
    postgres ``->>`` to sqlite ``json_extract`` so the same query
    string runs on both backends. The store-level test pins the
    pre-translation shape; the brain's adapter is tested separately
    in ``tests/core/test_sqlite_database_bootstrap.py``.
    """
    connection = _FakeConnectionWithStatus(status="DELETE 3")
    store = MemoryStore(_FakeBrainWithRowcount(connection), now=lambda: 2500.0)

    deleted = await store.delete_user_memory_by_source(
        user_id=42,
        source="preferences_doc",
    )

    assert connection.closed is True
    assert deleted == 3
    query, args = connection.execute_calls[0]
    # Pin the WHERE clause shape exactly so a future refactor
    # doesn't accidentally widen the scrub.
    assert "DELETE FROM user_memory" in query
    assert "WHERE user_id = $1" in query
    assert "source_detail->>'source' = $2" in query
    assert args == (42, "preferences_doc")


@pytest.mark.asyncio
async def test_delete_user_memory_by_source_skips_empty_marker() -> None:
    """Empty source marker is a no-op — never issue a DELETE that
    could match every row in the table.

    Defense-in-depth: the rebuilder always passes a non-empty constant,
    but a future caller passing ``""`` or ``None`` should fail closed,
    not delete everything.
    """
    connection = _FakeConnectionWithStatus(status="DELETE 0")
    store = MemoryStore(_FakeBrainWithRowcount(connection), now=lambda: 2500.0)

    deleted = await store.delete_user_memory_by_source(user_id=42, source="")
    assert deleted == 0
    assert connection.execute_calls == []
    assert connection.closed is False


@pytest.mark.asyncio
async def test_list_memory_index_items_filters_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_memory_index_item_row()]])
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.list_memory_index_items(
        owner_user_id=7,
        source_kinds=["workflow_memory", "followthrough_item"],
        limit=5,
    )

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "source_kind IN ($2, $3)" in query
    assert args == (7, "workflow_memory", "followthrough_item", 5)
    assert result == [
        {
            "item_id": "item_1",
            "owner_user_id": 7,
            "source_kind": "workflow_memory",
            "source_id": "mem_1",
            "title": "Workflow memory",
            "content": "Fixed the XML mapping workflow.",
            "snippet": "Fixed the XML mapping workflow.",
            "salience": "high",
            "tags": ["workflow", "completed"],
            "related_refs": ["workflow:wf_1"],
            "content_hash": "hash_1",
            "metadata": {"source": "operator"},
            "created_at": 1000.0,
            "updated_at": 1100.0,
        }
    ]


@pytest.mark.asyncio
async def test_search_memory_index_fts_maps_lexical_score() -> None:
    connection = _FakeConnection(fetch_results=[[_make_memory_index_search_row()]])
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.search_memory_index_fts(owner_user_id=7, query="xml mapping", limit=4)

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "memory_index_fts MATCH $2" in query
    # IM-CRIT-4: owner_user_id must bind as int to match canonical
    # memory_index_items.owner_user_id (BIGINT). Binding as TEXT made
    # the JOIN at search_memory_index_fts return zero rows in production.
    assert args == (7, "xml mapping", 4)
    assert result == [
        {
            "item_id": "item_1",
            "owner_user_id": 7,
            "source_kind": "workflow_memory",
            "source_id": "mem_1",
            "title": "Workflow memory",
            "content": "Fixed the XML mapping workflow.",
            "snippet": "Fixed the XML mapping workflow.",
            "salience": "high",
            "tags": ["workflow", "completed"],
            "related_refs": ["workflow:wf_1"],
            "content_hash": "hash_1",
            "metadata": {"source": "operator"},
            "created_at": 1000.0,
            "updated_at": 1100.0,
            "lexical_score": -0.42,
        }
    ]


@pytest.mark.asyncio
async def test_search_memory_index_fts_sanitizes_hyphenated_terms() -> None:
    connection = _FakeConnection(fetch_results=[[_make_memory_index_search_row()]])
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.search_memory_index_fts(owner_user_id=7, query="companion check-in", limit=4)

    _, args = connection.fetch_calls[0]
    # IM-CRIT-4: owner_user_id must bind as int (see sibling test).
    assert args == (7, "companion check in", 4)


@pytest.mark.asyncio
async def test_upsert_memory_index_fts_binds_owner_user_id_as_int() -> None:
    """IM-CRIT-4 regression: owner_user_id must bind as int.

    The canonical row store ``memory_index_items.owner_user_id`` is BIGINT.
    Prior to this fix the FTS write path bound ``str(owner_user_id)``,
    so JOINs that compared the FTS column to ``memory_index_items``
    silently returned zero rows whenever the FTS side held TEXT and
    the canonical side held INTEGER. This test pins the integer-bind
    contract on the write path.
    """
    connection = _FakeConnection()
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.upsert_memory_index_fts(
        item_id="item_1",
        owner_user_id=42,
        source_kind="workflow_memory",
        title="Workflow memory",
        content="Fixed the XML mapping workflow.",
        tags=["workflow", "completed"],
    )

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "INSERT INTO memory_index_fts" in query
    assert args[0] == 42
    assert isinstance(args[0], int)
    assert args[1:] == (
        "item_1",
        "workflow_memory",
        "Workflow memory",
        "Fixed the XML mapping workflow.",
        "workflow completed",
    )


@pytest.mark.asyncio
async def test_upsert_memory_index_fts_casts_string_owner_user_id_to_int() -> None:
    """IM-CRIT-4 Option A symmetry: a string owner_user_id is coerced to int.

    Callers may pass ``owner_user_id`` as a string (e.g. when crossing
    a JSON / form boundary). The store must normalize it to an int
    before binding so a later read path that filters by the same id
    matches regardless of which type the caller used.
    """
    connection = _FakeConnection()
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.upsert_memory_index_fts(
        item_id="item_1",
        owner_user_id="42",  # type: ignore[arg-type]
        source_kind="workflow_memory",
        title="Workflow memory",
        content="Fixed the XML mapping workflow.",
        tags=["workflow"],
    )

    _, args = connection.execute_calls[0]
    assert args[0] == 42
    assert isinstance(args[0], int)


@pytest.mark.asyncio
async def test_clear_memory_index_fts_dual_binds_owner_user_id_for_self_heal() -> None:
    """Code-review issue #1: clear must DELETE both int and TEXT rows.

    Pre-fix deployments wrote ``owner_user_id`` as TEXT (e.g. ``'7'``).
    After f1dc505 the write path binds int, so a single-bind DELETE
    (int=7) leaves the legacy ``'7'`` TEXT row in place because FTS5
    columns have no type affinity and SQLite WHERE does not coerce
    TEXT-vs-INT equality. The clear path therefore dual-binds (``int``
    OR ``str``) so the next ``rebuild_index`` self-heals legacy rows
    instead of accumulating orphans alongside fresh int rows.
    """
    connection = _FakeConnection()
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.clear_memory_index_fts(owner_user_id=42)

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "DELETE FROM memory_index_fts" in query
    # Dual-bind: int catches post-fix rows, str catches pre-fix legacy rows.
    assert "owner_user_id = $1" in query
    assert "owner_user_id = $2" in query
    assert args == (42, "42")
    assert isinstance(args[0], int)
    assert isinstance(args[1], str)


@pytest.mark.asyncio
async def test_search_memory_index_fts_casts_string_owner_user_id_to_int() -> None:
    """IM-CRIT-4 Option A symmetry: search path coerces str owner_user_id."""
    connection = _FakeConnection(fetch_results=[[_make_memory_index_search_row()]])
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.search_memory_index_fts(
        owner_user_id="42",  # type: ignore[arg-type]
        query="xml mapping",
        limit=4,
    )

    _, args = connection.fetch_calls[0]
    assert args[0] == 42
    assert isinstance(args[0], int)


@pytest.mark.asyncio
async def test_search_memory_index_fts_handles_owner_user_id_zero() -> None:
    """Edge case: owner_user_id=0 must bind as int 0, not be lost to falsy checks.

    A future caller using ``if owner_user_id:`` would silently drop the
    zero owner. The cast itself (``int(0)``) preserves the value; this
    pins that the bind round-trips faithfully.
    """
    connection = _FakeConnection(fetch_results=[[_make_memory_index_search_row()]])
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.search_memory_index_fts(owner_user_id=0, query="x", limit=1)

    _, args = connection.fetch_calls[0]
    assert args[0] == 0
    assert isinstance(args[0], int)


@pytest.mark.asyncio
async def test_clear_memory_index_fts_self_heals_legacy_text_rows(tmp_path) -> None:
    """End-to-end self-heal: pre-seed a TEXT-typed FTS row, run clear, row is gone.

    Reproduces the reviewer's repro: insert a row with ``owner_user_id``
    bound as TEXT (the pre-fix shape), call ``clear_memory_index_fts``
    (post-fix dual-bind), and assert the row is removed. Without the
    dual-bind this would leave the TEXT row in place because FTS5
    columns have no type affinity.
    """
    brain = PalliBrain(db_path=str(tmp_path / "self-heal.sqlite3"))
    await brain.initialize()
    try:
        # Pre-seed: simulate a pre-fix write that bound owner_user_id as TEXT.
        conn = await brain.get_connection()
        try:
            await conn.execute(
                "INSERT INTO memory_index_fts (owner_user_id, item_id, source_kind, title, content, tags) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                "7",  # legacy TEXT bind
                "legacy_item",
                "workflow_memory",
                "Legacy",
                "legacy content",
                "",
            )
        finally:
            await conn.close()

        # Sanity: confirm pre-seed row exists when queried with TEXT.
        conn = await brain.get_connection()
        try:
            rows_before_text = await conn.fetch(
                "SELECT item_id FROM memory_index_fts WHERE owner_user_id = $1",
                "7",
            )
            rows_before_int = await conn.fetch(
                "SELECT item_id FROM memory_index_fts WHERE owner_user_id = $1",
                7,
            )
        finally:
            await conn.close()
        assert [tuple(r)[0] for r in rows_before_text] == ["legacy_item"]
        # FTS5 has no column affinity: int=7 does NOT match the TEXT '7' row.
        assert list(rows_before_int) == []

        # Run the post-fix clear path.
        await brain.clear_memory_index_fts(owner_user_id=7)

        # Assert: legacy TEXT row is gone after clear.
        conn = await brain.get_connection()
        try:
            rows_after_text = await conn.fetch(
                "SELECT item_id FROM memory_index_fts WHERE owner_user_id = $1",
                "7",
            )
            rows_after_int = await conn.fetch(
                "SELECT item_id FROM memory_index_fts WHERE owner_user_id = $1",
                7,
            )
        finally:
            await conn.close()
        assert list(rows_after_text) == []
        assert list(rows_after_int) == []
    finally:
        await brain.close()


@pytest.mark.asyncio
async def test_delete_memory_index_items_uses_shared_placeholders_for_all_tables() -> None:
    connection = _FakeConnection()
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.delete_memory_index_items(item_ids=["item_1", "item_2"])

    assert connection.closed is True
    assert [call[0] for call in connection.execute_calls] == [
        "DELETE FROM memory_index_items WHERE item_id IN ($1,$2)",
        "DELETE FROM memory_index_embeddings WHERE item_id IN ($1,$2)",
        "DELETE FROM memory_index_fts WHERE item_id IN ($1,$2)",
    ]
    assert all(call[1] == ("item_1", "item_2") for call in connection.execute_calls)


@pytest.mark.asyncio
async def test_upsert_memory_index_embedding_serializes_vector_and_timestamps() -> None:
    connection = _FakeConnection()
    store = MemoryStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.upsert_memory_index_embedding(
        item_id="item_1",
        owner_user_id=7,
        provider="local",
        model="all-MiniLM-L6-v2",
        vector=[0.1, 0.2, 0.3],
        content_hash="hash_1",
    )

    assert connection.closed is True
    _, args = connection.execute_calls[0]
    assert args[0:4] == ("item_1", 7, "local", "all-MiniLM-L6-v2")
    assert json.loads(args[4]) == [0.1, 0.2, 0.3]
    assert args[5:] == ("hash_1", 2500.0, 2500.0)


@pytest.mark.asyncio
async def test_palli_brain_memory_index_list_delegates_to_extracted_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def list_memory_index_items(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(kwargs)
            return [{"item_id": "item_stub"}]

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._memory_store = stub

    result = await brain.list_memory_index_items(
        owner_user_id=7,
        source_kinds=["workflow_memory"],
        limit=3,
    )

    assert result == [{"item_id": "item_stub"}]
    assert stub.calls == [{"owner_user_id": 7, "source_kinds": ["workflow_memory"], "limit": 3}]
