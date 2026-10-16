from __future__ import annotations

import json
from typing import Any

import pytest

from core.continuity_thread_store import ContinuityThreadStore
from core.database import PalliBrain


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


def _make_continuity_thread_row() -> tuple[Any, ...]:
    row = (
        "thread_1",
        7,
        "owner_main",
        "active",
        "Main Thread",
        "C:/repo",
        "Repo",
        "agent_1",
        "session_1",
        "workflow_1",
        2,
        4100.0,
        json.dumps({"scope_key": "owner:discord:123", "note": "warm"}),
        4000.0,
        4050.0,
        None,
    )
    assert len(row) == 16
    return row


@pytest.mark.asyncio
async def test_upsert_continuity_thread_merges_metadata_and_serializes_fields() -> None:
    connection = _FakeConnection(fetchrow_results=[('{"scope_key": "owner:discord:123", "existing": true}', 2)])
    store = ContinuityThreadStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.upsert_continuity_thread(
        thread_id="thread_1",
        owner_user_id=7,
        thread_kind="owner_main",
        status="active",
        label="Main Thread",
        workspace_root="C:/repo",
        workspace_label="Repo",
        root_agent_session_key="agent_1",
        last_runtime_session_key="session_1",
        last_workflow_id="workflow_1",
        reset_count=5,
        metadata={"added": "value"},
    )

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "ON CONFLICT (thread_id) DO UPDATE SET" in query
    assert "workspace_root = COALESCE(EXCLUDED.workspace_root, continuity_threads.workspace_root)" in query
    assert args[0:11] == (
        "thread_1",
        7,
        "owner_main",
        "active",
        "Main Thread",
        "C:/repo",
        "Repo",
        "agent_1",
        "session_1",
        "workflow_1",
        5,
    )
    assert args[11] == 2500.0
    assert json.loads(args[12]) == {"scope_key": "owner:discord:123", "existing": True, "added": "value"}
    assert args[13] == 2500.0
    assert args[14] is None


@pytest.mark.asyncio
async def test_update_continuity_thread_merges_metadata_and_preserves_archive_state() -> None:
    connection = _FakeConnection(fetchrow_results=[('{"scope_key": "owner:discord:123", "existing": true}', 900.0, 3)])
    store = ContinuityThreadStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.update_continuity_thread(
        thread_id="thread_1",
        status="archived",
        last_activity=777.0,
        metadata={"added": "value"},
    )

    assert connection.closed is True
    _, args = connection.execute_calls[0]
    assert args[0] == "thread_1"
    assert args[1] == "archived"
    assert args[8] == 3
    assert args[9] == 777.0
    assert json.loads(args[10]) == {"scope_key": "owner:discord:123", "existing": True, "added": "value"}
    assert args[11] == 2500.0
    assert args[12] == 900.0


@pytest.mark.asyncio
async def test_find_continuity_thread_by_scope_filters_and_maps_record() -> None:
    connection = _FakeConnection(fetchrow_results=[_make_continuity_thread_row()])
    store = ContinuityThreadStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.find_continuity_thread_by_scope(
        owner_user_id=7,
        thread_kind="owner_main",
        scope_key="owner:discord:123",
        include_archived=True,
    )

    assert connection.closed is True
    query, args = connection.fetchrow_calls[0]
    assert "json_extract(metadata, '$.scope_key') = $3" in query
    assert args == (7, "owner_main", "owner:discord:123", True)
    assert result == {
        "thread_id": "thread_1",
        "owner_user_id": 7,
        "thread_kind": "owner_main",
        "status": "active",
        "label": "Main Thread",
        "workspace_root": "C:/repo",
        "workspace_label": "Repo",
        "root_agent_session_key": "agent_1",
        "last_runtime_session_key": "session_1",
        "last_workflow_id": "workflow_1",
        "reset_count": 2,
        "last_activity": 4100.0,
        "metadata": {"scope_key": "owner:discord:123", "note": "warm"},
        "created_at": 4000.0,
        "updated_at": 4050.0,
        "archived_at": None,
    }


@pytest.mark.asyncio
async def test_list_continuity_threads_filters_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_continuity_thread_row()]])
    store = ContinuityThreadStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.list_continuity_threads(
        owner_user_id=7,
        statuses=["active", "paused"],
        limit=5,
        include_archived=False,
    )

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "archived_at IS NULL" in query
    assert "status IN ($2,$3)" in query
    assert args == (7, "active", "paused", 5)
    assert result == [
        {
            "thread_id": "thread_1",
            "owner_user_id": 7,
            "thread_kind": "owner_main",
            "status": "active",
            "label": "Main Thread",
            "workspace_root": "C:/repo",
            "workspace_label": "Repo",
            "root_agent_session_key": "agent_1",
            "last_runtime_session_key": "session_1",
            "last_workflow_id": "workflow_1",
            "reset_count": 2,
            "last_activity": 4100.0,
            "metadata": {"scope_key": "owner:discord:123", "note": "warm"},
            "created_at": 4000.0,
            "updated_at": 4050.0,
            "archived_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_palli_brain_continuity_threads_delegate_to_extracted_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def list_continuity_threads(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(kwargs)
            return [{"thread_id": "thread_stub"}]

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._continuity_thread_store = stub

    result = await brain.list_continuity_threads(
        owner_user_id=7,
        statuses=["active"],
        limit=3,
        include_archived=True,
    )

    assert result == [{"thread_id": "thread_stub"}]
    assert stub.calls == [
        {
            "owner_user_id": 7,
            "statuses": ["active"],
            "limit": 3,
            "include_archived": True,
        }
    ]
