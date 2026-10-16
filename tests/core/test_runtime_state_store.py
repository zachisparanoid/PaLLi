from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.runtime_state_store import RuntimeStateStore


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


def _make_runtime_job_row() -> tuple[Any, ...]:
    row = (
        "job_1",
        7,
        "owner:7:surface:maintenance",
        "ambient_action",
        "nudge",
        "queued",
        "Review the open loop",
        4100.0,
        None,
        None,
        2,
        "",
        "scheduled_by_ambient",
        "job_old",
        "job_replacement",
        "context_events",
        "evt_1",
        json.dumps({"lane": "ambient"}),
        4000.0,
        4050.0,
    )
    assert len(row) == 20
    return row


def _make_context_event_row() -> tuple[Any, ...]:
    row = (
        "evt_1",
        7,
        "home_presence",
        "arrival",
        1234.0,
        "arrival:7",
        "owner:7:surface:maintenance",
        json.dumps({"room": "office"}),
        json.dumps({"confidence": "high"}),
        1235.0,
    )
    assert len(row) == 10
    return row


def _make_operator_state_row() -> tuple[Any, ...]:
    row = (
        7,
        "C:/workspace/pinned",
        "Pinned Workspace",
        "origin",
        "C:/workspace/current",
        "Current Workspace",
        "upstream",
        "action_123",
        4321.0,
        json.dumps({"theme": "focus"}),
        4000.0,
        4050.0,
    )
    assert len(row) == 12
    return row


@pytest.mark.asyncio
async def test_upsert_runtime_session_merges_metadata_and_preserves_thread_state() -> None:
    connection = _FakeConnection(fetchrow_results=[('{"existing": true}', "thread_old", "owner_main", "Main Thread")])
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.upsert_runtime_session(
        session_key="owner:7:surface:discord:chat",
        owner_user_id=7,
        session_type="chat",
        platform="discord",
        channel_id=123,
        status="active",
        label="Discord Chat",
        metadata={"added": "value"},
    )

    assert connection.closed is True
    _, args = connection.execute_calls[0]
    assert args[0] == "owner:7:surface:discord:chat"
    assert args[2:5] == ("thread_old", "owner_main", "Main Thread")
    assert args[9] == "active"
    assert args[10] == "Discord Chat"
    assert json.loads(args[11]) == {"existing": True, "added": "value"}
    assert args[12] == 2500.0
    assert args[13] == 2500.0


@pytest.mark.asyncio
async def test_update_runtime_job_merges_metadata_and_manages_completion_state() -> None:
    connection = _FakeConnection(
        fetchrow_results=[
            (
                "owner:7:surface:maintenance",
                "Review the open loop",
                None,
                111.0,
                444.0,
                2,
                "old error",
                "old reason",
                "job_old",
                "job_replacement",
                "context_events",
                "evt_1",
                '{"existing": true}',
            )
        ]
    )
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.update_runtime_job(
        job_id="job_1",
        status="running",
        increment_attempt=True,
        retry_of_job_id="job_retry",
        metadata={"added": "value"},
    )

    assert connection.closed is True
    _, args = connection.execute_calls[0]
    assert args[0] == "job_1"
    assert args[1] == "running"
    assert args[5] == 111.0
    assert args[6] is None
    assert args[7] == 3
    assert args[10] == "job_retry"
    assert json.loads(args[14]) == {"existing": True, "added": "value"}
    assert args[15] == 2500.0


@pytest.mark.asyncio
async def test_list_runtime_jobs_filters_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_runtime_job_row()]])
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.list_runtime_jobs(
        owner_user_id=7,
        statuses=["queued", "running"],
        job_types=["ambient_action"],
        limit=5,
    )

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "status IN ($2,$3)" in query
    assert "job_type IN ($4)" in query
    assert args == (7, "queued", "running", "ambient_action", 5)
    assert result == [
        {
            "job_id": "job_1",
            "owner_user_id": 7,
            "runtime_session_key": "owner:7:surface:maintenance",
            "job_type": "ambient_action",
            "subtype": "nudge",
            "status": "queued",
            "request_text": "Review the open loop",
            "scheduled_for": 4100.0,
            "started_at": None,
            "completed_at": None,
            "attempt_count": 2,
            "last_error": "",
            "last_transition_reason": "scheduled_by_ambient",
            "retry_of_job_id": "job_old",
            "replacement_job_id": "job_replacement",
            "source_table": "context_events",
            "source_id": "evt_1",
            "metadata": {"lane": "ambient"},
            "created_at": 4000.0,
            "updated_at": 4050.0,
        }
    ]


@pytest.mark.asyncio
async def test_list_context_events_filters_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_context_event_row()]])
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.list_context_events(
        owner_user_id=7,
        event_types=["arrival", "departure"],
        limit=4,
    )

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "event_type IN ($2,$3)" in query
    assert args == (7, "arrival", "departure", 4)
    assert result == [
        {
            "event_id": "evt_1",
            "owner_user_id": 7,
            "source": "home_presence",
            "event_type": "arrival",
            "occurred_at": 1234.0,
            "dedupe_key": "arrival:7",
            "runtime_session_key": "owner:7:surface:maintenance",
            "payload": {"room": "office"},
            "metadata": {"confidence": "high"},
            "created_at": 1235.0,
        }
    ]


@pytest.mark.asyncio
async def test_upsert_home_context_state_merges_metadata_and_preserves_existing_timestamps() -> None:
    connection = _FakeConnection(
        fetchrow_results=[('{"watcher": "ok"}', '{"existing": true}', 111.0, 222.0, 333.0, 444.0)]
    )
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.upsert_home_context_state(
        owner_user_id=7,
        owner_presence_state="home",
        machine_state="active",
        privacy_state="private",
        quiet_hours_active=True,
        last_event_at=555.0,
        source_health={"bluetooth": "stale"},
        metadata={"mode": "night"},
    )

    assert connection.closed is True
    _, args = connection.execute_calls[0]
    assert args[0] == 7
    assert args[1:5] == ("home", "active", "private", True)
    assert args[5:9] == (111.0, 222.0, 333.0, 555.0)
    assert json.loads(args[9]) == {"watcher": "ok", "bluetooth": "stale"}
    assert json.loads(args[10]) == {"existing": True, "mode": "night"}
    assert args[11] == 2500.0


@pytest.mark.asyncio
async def test_update_ambient_action_merges_content_metadata_and_preserves_delivery_times() -> None:
    connection = _FakeConnection(fetchrow_results=[('{"text": "old", "keep": true}', '{"source": "ambient"}', 777.0, None)])
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.update_ambient_action(
        action_id="ambient_1",
        status="completed",
        content={"text": "new", "extra": "yes"},
        metadata={"reason": "done"},
    )

    assert connection.closed is True
    _, args = connection.execute_calls[0]
    assert args[0] == "ambient_1"
    assert args[1] == "completed"
    assert json.loads(args[6]) == {"text": "new", "keep": True, "extra": "yes"}
    assert json.loads(args[7]) == {"source": "ambient", "reason": "done"}
    assert args[8] == 2500.0
    assert args[9] == 777.0
    assert args[10] is None


@pytest.mark.asyncio
async def test_upsert_operator_state_merges_metadata_and_preserves_unset_fields() -> None:
    connection = _FakeConnection(fetchrow_results=[('{"existing": true}', 111.0)])
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.upsert_operator_state(
        owner_user_id=7,
        current_workspace_root="C:/workspace/current",
        current_workspace_label="Current Workspace",
        last_operator_action_id="action_123",
        metadata={"mode": "focus"},
    )

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "COALESCE(EXCLUDED.pinned_workspace_root, operator_state.pinned_workspace_root)" in query
    assert args[0] == 7
    assert args[1] is None
    assert args[4:8] == ("C:/workspace/current", "Current Workspace", None, "action_123")
    assert args[8] is None
    assert json.loads(args[9]) == {"existing": True, "mode": "focus"}
    assert args[10] == 111.0
    assert args[11] == 2500.0


@pytest.mark.asyncio
async def test_replace_operator_state_replaces_scalar_fields_and_merges_metadata() -> None:
    connection = _FakeConnection(fetchrow_results=[('{"existing": true}', 111.0)])
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.replace_operator_state(
        owner_user_id=7,
        pinned_workspace_root="C:/workspace/pinned",
        current_workspace_root="C:/workspace/current",
        metadata={"mode": "replace"},
    )

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "pinned_workspace_root = EXCLUDED.pinned_workspace_root" in query
    assert "COALESCE(EXCLUDED.pinned_workspace_root, operator_state.pinned_workspace_root)" not in query
    assert args[0] == 7
    assert args[1] == "C:/workspace/pinned"
    assert args[2] is None
    assert args[4] == "C:/workspace/current"
    assert json.loads(args[9]) == {"existing": True, "mode": "replace"}
    assert args[10] == 111.0
    assert args[11] == 2500.0


@pytest.mark.asyncio
async def test_get_operator_state_maps_record() -> None:
    connection = _FakeConnection(fetchrow_results=[_make_operator_state_row()])
    store = RuntimeStateStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.get_operator_state(owner_user_id=7)

    assert connection.closed is True
    assert result == {
        "owner_user_id": 7,
        "pinned_workspace_root": "C:/workspace/pinned",
        "pinned_workspace_label": "Pinned Workspace",
        "pinned_remote_name": "origin",
        "current_workspace_root": "C:/workspace/current",
        "current_workspace_label": "Current Workspace",
        "current_remote_name": "upstream",
        "last_operator_action_id": "action_123",
        "last_operator_activity_at": 4321.0,
        "metadata": {"theme": "focus"},
        "created_at": 4000.0,
        "updated_at": 4050.0,
    }


@pytest.mark.asyncio
async def test_palli_brain_runtime_sessions_delegate_to_extracted_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def list_runtime_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(kwargs)
            return [{"session_key": "session_stub"}]

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._runtime_state_store = stub

    result = await brain.list_runtime_sessions(
        owner_user_id=7,
        statuses=["active"],
        limit=3,
        include_archived=True,
    )

    assert result == [{"session_key": "session_stub"}]
    assert stub.calls == [
        {
            "owner_user_id": 7,
            "statuses": ["active"],
            "session_types": None,
            "limit": 3,
            "include_archived": True,
        }
    ]


@pytest.mark.asyncio
async def test_palli_brain_operator_state_methods_delegate_to_runtime_state_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def upsert_operator_state(self, **kwargs: Any) -> None:
            self.calls.append(("upsert", kwargs))

        async def replace_operator_state(self, **kwargs: Any) -> None:
            self.calls.append(("replace", kwargs))

        async def get_operator_state(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("get", kwargs))
            return {"owner_user_id": 7, "current_workspace_root": "C:/workspace/current"}

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._runtime_state_store = stub

    await brain.upsert_operator_state(
        owner_user_id=7,
        current_workspace_root="C:/workspace/current",
        metadata={"mode": "focus"},
    )
    result = await brain.get_operator_state(owner_user_id=7)

    assert result == {"owner_user_id": 7, "current_workspace_root": "C:/workspace/current"}
    assert stub.calls == [
        (
            "upsert",
            {
                "owner_user_id": 7,
                "pinned_workspace_root": None,
                "pinned_workspace_label": None,
                "pinned_remote_name": None,
                "current_workspace_root": "C:/workspace/current",
                "current_workspace_label": None,
                "current_remote_name": None,
                "last_operator_action_id": None,
                "last_operator_activity_at": None,
                "metadata": {"mode": "focus"},
            },
        ),
        ("get", {"owner_user_id": 7}),
    ]
