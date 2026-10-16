from __future__ import annotations

from typing import Any

import pytest

from core.account_purge_store import AccountPurgeStore, _ACCOUNT_PURGE_TABLES
from core.database import PalliBrain


class _FakeTransaction:
    def __init__(self) -> None:
        self.started = False
        self.committed = False
        self.rolled_back = False

    async def start(self) -> None:
        self.started = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchrow_results: list[Any] | None = None,
        fetch_results: list[Any] | None = None,
        execute_results: list[Any] | None = None,
    ) -> None:
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_results = list(fetch_results or [])
        self.execute_results = list(execute_results or [])
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False
        self.transaction_handle = _FakeTransaction()

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

    async def execute(self, query: str, *args: Any) -> Any:
        self.execute_calls.append((query, args))
        if self.execute_results:
            return self.execute_results.pop(0)
        return "DELETE 0"

    def transaction(self) -> _FakeTransaction:
        return self.transaction_handle

    async def close(self) -> None:
        self.closed = True


class _FakeBrain:
    def __init__(self, *connections: _FakeConnection) -> None:
        self._connections = list(connections)
        self.connection_calls = 0

    async def get_connection(self) -> _FakeConnection:
        index = min(self.connection_calls, len(self._connections) - 1)
        self.connection_calls += 1
        return self._connections[index]


@pytest.mark.asyncio
async def test_purge_conversations_for_user_ids_counts_safe_shared_and_legacy_rows() -> None:
    connection = _FakeConnection(
        fetch_results=[[("discord", "thread-1"), ("web", "owner-main")]],
        execute_results=["DELETE 2", "DELETE 1", "DELETE 3"],
    )
    store = AccountPurgeStore(_FakeBrain(connection))

    deleted = await store.purge_conversations_for_user_ids(connection, [7])

    assert connection.closed is False
    assert deleted == 6
    safe_query, safe_args = connection.fetch_calls[0]
    assert "FROM conversations" in safe_query
    assert "GROUP BY platform, channel_id" in safe_query
    assert safe_args == (7, 7)
    full_thread_delete_query, full_thread_delete_args = connection.execute_calls[0]
    assert "WHERE (platform, channel_id) IN (($1, $2),($3, $4))" in full_thread_delete_query
    assert full_thread_delete_args == ("discord", "thread-1", "web", "owner-main")
    shared_rows_delete_query, shared_rows_delete_args = connection.execute_calls[1]
    assert "DELETE FROM conversations WHERE user_id IN ($1)" in shared_rows_delete_query
    assert shared_rows_delete_args == (7,)
    legacy_delete_query, legacy_delete_args = connection.execute_calls[2]
    assert "json_extract(metadata, '$.target_user_id')" in legacy_delete_query
    assert legacy_delete_args == (7,)


@pytest.mark.asyncio
async def test_purge_user_data_reuses_conversation_purge_and_counts_other_tables() -> None:
    class _PurgingStore(AccountPurgeStore):
        def __init__(self, brain: Any) -> None:
            super().__init__(brain, now=lambda: 4000.0)
            self.purge_calls: list[tuple[Any, list[int]]] = []

        async def purge_conversations_for_user_ids(self, conn: Any, user_ids: list[int]) -> int:
            self.purge_calls.append((conn, user_ids))
            return 6

    connection = _FakeConnection(execute_results=["DELETE 2", "DELETE 1"])
    store = _PurgingStore(_FakeBrain(connection))

    deleted = await store.purge_user_data(
        [7, 7, "skip-me"],
        ["conversations", "user_memory", "", "audit_logs"],
    )

    assert connection.closed is True
    assert deleted == {
        "conversations": 6,
        "user_memory": 2,
        "audit_logs": 1,
    }
    assert store.purge_calls == [(connection, [7])]
    first_query, first_args = connection.execute_calls[0]
    assert first_query == "DELETE FROM user_memory WHERE user_id IN ($1)"
    assert first_args == (7,)
    second_query, second_args = connection.execute_calls[1]
    assert second_query == "DELETE FROM audit_logs WHERE user_id IN ($1)"
    assert second_args == (7,)


@pytest.mark.asyncio
async def test_soft_delete_account_rolls_back_when_user_is_missing() -> None:
    connection = _FakeConnection(fetchrow_results=[None])
    store = AccountPurgeStore(_FakeBrain(connection), now=lambda: 4000.0)

    result = await store.soft_delete_account(5)

    assert connection.closed is True
    assert result == {"ok": False, "reason": "not_found"}
    assert connection.transaction_handle.started is True
    assert connection.transaction_handle.rolled_back is True
    assert connection.transaction_handle.committed is False


@pytest.mark.asyncio
async def test_soft_delete_account_purges_owned_rows_and_scrubs_user_record() -> None:
    class _SoftDeleteStore(AccountPurgeStore):
        def __init__(self, brain: Any) -> None:
            super().__init__(brain, now=lambda: 5000.0)
            self.purge_calls: list[tuple[Any, int, list[int]]] = []

        async def _purge_owned_account_rows(
            self,
            conn: Any,
            *,
            account_user_id: int,
            owned_user_ids: list[int],
        ) -> dict[str, int]:
            self.purge_calls.append((conn, account_user_id, owned_user_ids))
            deleted = {table: 0 for table in _ACCOUNT_PURGE_TABLES}
            deleted["conversations"] = 13
            deleted["discord_guild_accounts"] = 0
            deleted["account_features"] = 0
            return deleted

    connection = _FakeConnection(
        fetchrow_results=[
            {
                "id": 5,
                "email": "owner@example.com",
                "palli_user_id": 71,
                "discord_user_id": "88",
            }
        ],
        execute_results=["UPDATE 1"],
    )
    store = _SoftDeleteStore(_FakeBrain(connection))

    result = await store.soft_delete_account(5)

    assert connection.closed is True
    assert connection.transaction_handle.started is True
    assert connection.transaction_handle.committed is True
    assert connection.transaction_handle.rolled_back is False
    assert store.purge_calls == [(connection, 5, [5, 71, 88])]
    assert result["ok"] is True
    assert result["old_email"] == "owner@example.com"
    assert result["new_email"] == "deleted+5@trypalli.com"
    assert result["deleted"]["conversations"] == 13
    assert set(_ACCOUNT_PURGE_TABLES).issubset(result["deleted"].keys())
    assert result["deleted"]["discord_guild_accounts"] == 0
    assert result["deleted"]["account_features"] == 0
    scrub_query, scrub_args = connection.execute_calls[-1]
    assert "UPDATE users" in scrub_query
    assert "password_hash = NULL" in scrub_query
    assert scrub_args == ("deleted+5@trypalli.com", 5000.0, 5)


@pytest.mark.asyncio
async def test_palli_brain_account_purge_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.purge_calls: list[tuple[Any, list[int]]] = []
            self.purge_user_data_calls: list[dict[str, Any]] = []
            self.soft_delete_calls: list[dict[str, Any]] = []

        async def purge_conversations_for_user_ids(self, conn: Any, user_ids: list[int]) -> int:
            self.purge_calls.append((conn, user_ids))
            return 6

        async def purge_user_data(self, **kwargs: Any) -> dict[str, int]:
            self.purge_user_data_calls.append(kwargs)
            return {"conversations": 6}

        async def soft_delete_account(self, **kwargs: Any) -> dict[str, Any]:
            self.soft_delete_calls.append(kwargs)
            return {"ok": True}

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._account_purge_store = stub

    conn_marker = object()
    purged = await brain._purge_conversations_for_user_ids_internal(conn_marker, [5, 71])
    deleted_counts = await brain.purge_user_data([5, 71], ["conversations"])
    deleted = await brain.soft_delete_account(41)

    assert purged == 6
    assert deleted_counts == {"conversations": 6}
    assert deleted == {"ok": True}
    assert stub.purge_calls == [(conn_marker, [5, 71])]
    assert stub.purge_user_data_calls == [{"user_ids": [5, 71], "tables": ["conversations"]}]
    assert stub.soft_delete_calls == [{"user_id": 41}]
