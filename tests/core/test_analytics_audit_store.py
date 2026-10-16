from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from core.analytics_audit_store import AnalyticsAuditStore
from core.database import PalliBrain


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchval_results: list[Any] | None = None,
        fetch_results: list[Any] | None = None,
        fetchval_exception: Exception | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetch_results = list(fetch_results or [])
        self.fetchval_exception = fetchval_exception
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.fetchval_calls.append((query, args))
        if self.fetchval_exception is not None:
            raise self.fetchval_exception
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
        return None

    async def fetch(self, query: str, *args: Any) -> Any:
        self.fetch_calls.append((query, args))
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    async def close(self) -> None:
        self.closed = True


class _FakeBrain:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def get_connection(self) -> _FakeConnection:
        return self._connection


@pytest.mark.asyncio
async def test_store_web_search_serializes_metadata_and_uses_now() -> None:
    connection = _FakeConnection(fetchval_results=[12])
    store = AnalyticsAuditStore(_FakeBrain(connection), now=lambda: 1234.5)

    search_id = await store.store_web_search(
        user_id=7,
        query="weather tomorrow",
        metadata={"results": 3, "provider": "exa"},
    )

    assert connection.closed is True
    assert search_id == 12
    _, args = connection.fetchval_calls[0]
    assert args[0] == 7
    assert args[1] == "weather tomorrow"
    assert args[2] == 1234.5
    assert json.loads(args[3]) == {"results": 3, "provider": "exa"}


@pytest.mark.asyncio
async def test_log_audit_event_persists_owner_visible_fields() -> None:
    connection = _FakeConnection(fetchval_results=[27])
    store = AnalyticsAuditStore(_FakeBrain(connection), now=lambda: 2468.0)

    audit_id = await store.log_audit_event(
        user_id=5,
        action="password_reset_requested",
        ip_address="127.0.0.1",
        user_agent="pytest-agent",
        details="Owner requested a password reset from localhost.",
    )

    assert connection.closed is True
    assert audit_id == 27
    _, args = connection.fetchval_calls[0]
    assert args == (
        5,
        "password_reset_requested",
        "127.0.0.1",
        "pytest-agent",
        "Owner requested a password reset from localhost.",
        2468.0,
    )


@pytest.mark.asyncio
async def test_log_audit_event_returns_negative_one_on_insert_failure(caplog: pytest.LogCaptureFixture) -> None:
    connection = _FakeConnection(fetchval_exception=RuntimeError("write failed"))
    store = AnalyticsAuditStore(_FakeBrain(connection), now=lambda: 100.0)

    with caplog.at_level(logging.ERROR):
        audit_id = await store.log_audit_event(
            user_id=5,
            action="login_failed",
            ip_address="127.0.0.1",
            user_agent="pytest-agent",
            details="Bad password",
        )

    assert connection.closed is True
    assert audit_id == -1
    assert "Failed to log audit event: write failed" in caplog.text
    _, args = connection.fetchval_calls[0]
    assert args == (5, "login_failed", "127.0.0.1", "pytest-agent", "Bad password", 100.0)


@pytest.mark.asyncio
async def test_get_audit_logs_maps_rows_and_limit() -> None:
    connection = _FakeConnection(
        fetch_results=[
            [
                ("login_success", "127.0.0.1", "Firefox", "Owner signed in.", 2000.0),
                ("email_verified", None, "Firefox", "Verification complete.", 1500.0),
            ]
        ]
    )
    store = AnalyticsAuditStore(_FakeBrain(connection))

    logs = await store.get_audit_logs(user_id=11, limit=2)

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "FROM audit_logs" in query
    assert "WHERE user_id = $1" in query
    assert "ORDER BY timestamp DESC" in query
    assert "LIMIT $2" in query
    assert args == (11, 2)
    assert logs == [
        {
            "action": "login_success",
            "ip_address": "127.0.0.1",
            "user_agent": "Firefox",
            "details": "Owner signed in.",
            "timestamp": 2000.0,
        },
        {
            "action": "email_verified",
            "ip_address": None,
            "user_agent": "Firefox",
            "details": "Verification complete.",
            "timestamp": 1500.0,
        },
    ]


@pytest.mark.asyncio
async def test_palli_brain_analytics_audit_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.web_search_calls: list[dict[str, Any]] = []
            self.audit_calls: list[dict[str, Any]] = []
            self.audit_log_calls: list[dict[str, Any]] = []

        async def store_web_search(self, **kwargs: Any) -> int:
            self.web_search_calls.append(kwargs)
            return 41

        async def log_audit_event(self, **kwargs: Any) -> int:
            self.audit_calls.append(kwargs)
            return 43

        async def get_audit_logs(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.audit_log_calls.append(kwargs)
            return [{"action": "login_success"}]

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._analytics_audit_store = stub

    web_search_id = await brain.store_web_search(
        user_id=7,
        query="owner goals",
        metadata={"results": 2},
    )
    audit_id = await brain.log_audit_event(
        user_id=7,
        action="account_deletion_requested",
        ip_address="127.0.0.1",
        user_agent="pytest-agent",
        details="Owner opened the deletion flow.",
    )
    logs = await brain.get_audit_logs(user_id=7, limit=5)

    assert web_search_id == 41
    assert audit_id == 43
    assert logs == [{"action": "login_success"}]
    assert stub.web_search_calls == [
        {
            "user_id": 7,
            "query": "owner goals",
            "metadata": {"results": 2},
        }
    ]
    assert stub.audit_calls == [
        {
            "user_id": 7,
            "action": "account_deletion_requested",
            "ip_address": "127.0.0.1",
            "user_agent": "pytest-agent",
            "details": "Owner opened the deletion flow.",
        }
    ]
    assert stub.audit_log_calls == [{"user_id": 7, "limit": 5}]
