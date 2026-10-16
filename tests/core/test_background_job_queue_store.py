from __future__ import annotations

import json
from typing import Any

import pytest

from core.background_job_queue_store import BackgroundJobQueueStore
from core.database import PalliBrain


class _FakeTransaction:
    def __init__(self, connection: "_FakeConnection") -> None:
        self._connection = connection

    async def __aenter__(self) -> "_FakeTransaction":
        self._connection.transaction_entries += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._connection.transaction_exits += 1


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchval_results: list[Any] | None = None,
        fetchrow_results: list[Any] | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.close_count = 0
        self.transaction_entries = 0
        self.transaction_exits = 0

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.fetchval_calls.append((query, args))
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
        return None

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.fetchrow_calls.append((query, args))
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return "OK"

    async def close(self) -> None:
        self.close_count += 1

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)


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


@pytest.mark.asyncio
async def test_queue_job_persists_json_payload_and_returns_id() -> None:
    connection = _FakeConnection(fetchval_results=[17])
    store = BackgroundJobQueueStore(_FakeBrain(connection), now=lambda: 2500.0)

    job_id = await store.queue_job(
        job_type="send_test_ping",
        payload={"channel_id": 1234, "delivery": "discord"},
    )

    assert job_id == 17
    assert connection.close_count == 1
    query, args = connection.fetchval_calls[0]
    assert "INSERT INTO job_queue" in query
    assert args[0] == "send_test_ping"
    assert json.loads(args[1]) == {"channel_id": 1234, "delivery": "discord"}
    assert args[2] == 2500.0


@pytest.mark.asyncio
async def test_pop_job_filters_by_type_marks_processing_and_maps_payload() -> None:
    connection = _FakeConnection(
        fetchrow_results=[(17, "send_test_ping", json.dumps({"channel_id": 1234, "delivery": "discord"}))]
    )
    store = BackgroundJobQueueStore(_FakeBrain(connection), now=lambda: 2600.0)

    job = await store.pop_job(job_type="send_test_ping")

    assert job == {
        "id": 17,
        "type": "send_test_ping",
        "payload": {"channel_id": 1234, "delivery": "discord"},
    }
    assert connection.close_count == 1
    assert connection.transaction_entries == 1
    assert connection.transaction_exits == 1

    select_query, select_args = connection.fetchrow_calls[0]
    assert "WHERE status = 'pending' AND job_type = $1" in select_query
    assert "ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED" in select_query
    assert select_args == ("send_test_ping",)

    update_query, update_args = connection.execute_calls[0]
    assert "UPDATE job_queue SET status = 'processing'" in update_query
    assert update_args == (2600.0, 17)


@pytest.mark.asyncio
async def test_pop_job_returns_none_without_pending_jobs() -> None:
    connection = _FakeConnection(fetchrow_results=[None])
    store = BackgroundJobQueueStore(_FakeBrain(connection), now=lambda: 2600.0)

    job = await store.pop_job()

    assert job is None
    assert connection.close_count == 1
    assert connection.transaction_entries == 1
    assert connection.transaction_exits == 1
    assert connection.execute_calls == []
    select_query, select_args = connection.fetchrow_calls[0]
    assert "AND job_type" not in select_query
    assert select_args == ()


@pytest.mark.asyncio
async def test_palli_brain_background_job_queue_methods_delegate_to_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def queue_job(self, *, job_type: str, payload: dict[str, Any]) -> int:
            self.calls.append(("queue", {"job_type": job_type, "payload": payload}))
            return 17

        async def pop_job(self, *, job_type: str | None = None) -> dict[str, Any] | None:
            self.calls.append(("pop", {"job_type": job_type}))
            return {"id": 17, "type": job_type or "send_test_ping", "payload": {"channel_id": 1234}}

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._background_job_queue_store = stub

    job_id = await brain.queue_job("send_test_ping", {"channel_id": 1234})
    job = await brain.pop_job("send_test_ping")

    assert job_id == 17
    assert job == {"id": 17, "type": "send_test_ping", "payload": {"channel_id": 1234}}
    assert stub.calls == [
        ("queue", {"job_type": "send_test_ping", "payload": {"channel_id": 1234}}),
        ("pop", {"job_type": "send_test_ping"}),
    ]


@pytest.mark.asyncio
async def test_background_job_queue_sqlite_round_trip_preserves_order_and_type_filter(tmp_path) -> None:
    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))

    try:
        first_ping_id = await brain.queue_job("send_test_ping", {"channel_id": 111})
        verification_id = await brain.queue_job("send_verification_dm", {"discord_user_id": "owner_1"})
        second_ping_id = await brain.queue_job("send_test_ping", {"channel_id": 222})

        first_ping = await brain.pop_job("send_test_ping")
        second_ping = await brain.pop_job("send_test_ping")
        no_more_pings = await brain.pop_job("send_test_ping")
        verification = await brain.pop_job("send_verification_dm")

        assert first_ping == {
            "id": first_ping_id,
            "type": "send_test_ping",
            "payload": {"channel_id": 111},
        }
        assert second_ping == {
            "id": second_ping_id,
            "type": "send_test_ping",
            "payload": {"channel_id": 222},
        }
        assert no_more_pings is None
        assert verification == {
            "id": verification_id,
            "type": "send_verification_dm",
            "payload": {"discord_user_id": "owner_1"},
        }
    finally:
        await brain.close()
