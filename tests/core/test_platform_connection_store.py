from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.platform_connection_store import PlatformConnectionStore


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchval_results: list[Any] | None = None,
        fetchrow_results: list[Any] | None = None,
        fetch_results: list[Any] | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_results = list(fetch_results or [])
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

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


def _make_platform_space_row() -> tuple[Any, ...]:
    row = (
        9,
        "slack",
        "C123",
        "channel",
        7,
        "mention_or_reply",
        "Ops",
        json.dumps({"notify": "all"}),
        100.0,
        110.0,
    )
    assert len(row) == 10
    return row


def _make_platform_connection_row() -> tuple[Any, ...]:
    row = (
        1,
        7,
        "telegram",
        "123",
        "sam",
        "Sam",
        True,
        None,
        None,
        100.0,
        110.0,
    )
    assert len(row) == 11
    return row


@pytest.mark.asyncio
async def test_resolve_platform_surface_id_updates_existing_surface_record() -> None:
    connection = _FakeConnection(fetchval_results=[42])
    store = PlatformConnectionStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.resolve_platform_surface_id(
        platform=" Discord ",
        surface_id=" C123 ",
        surface_kind="Thread",
        label=" General ",
        metadata={"lane": "chat"},
    )

    assert result == 42
    assert connection.closed is True
    lookup_query, lookup_args = connection.fetchval_calls[0]
    assert "FROM platform_surfaces" in lookup_query
    assert lookup_args == ("discord", "C123")
    update_query, update_args = connection.execute_calls[0]
    assert "UPDATE platform_surfaces" in update_query
    assert update_args[0] == 42
    assert update_args[1] == "thread"
    assert update_args[2] == "General"
    assert json.loads(update_args[3]) == {"lane": "chat"}
    assert update_args[4] == 2500.0


@pytest.mark.asyncio
async def test_verify_platform_connection_marks_discord_link_verified() -> None:
    connection = _FakeConnection(fetchrow_results=[(7, 2600.0)])
    store = PlatformConnectionStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.verify_platform_connection(
        platform="Discord",
        platform_user_id="123",
        code="abc123",
    )

    assert result == 7
    assert connection.closed is True
    fetch_query, fetch_args = connection.fetchrow_calls[0]
    assert "FROM platform_connections" in fetch_query
    assert fetch_args == ("discord", "123", "abc123")
    assert len(connection.execute_calls) == 2
    _, verify_args = connection.execute_calls[0]
    assert verify_args == (2500.0, "discord", "123")
    _, user_args = connection.execute_calls[1]
    assert user_args == ("123", 7)


@pytest.mark.asyncio
async def test_link_verified_platform_connection_updates_discord_user_and_serializes_metadata() -> None:
    connection = _FakeConnection(fetchval_results=[88])
    store = PlatformConnectionStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.link_verified_platform_connection(
        user_id=7,
        platform="Discord",
        platform_user_id="123",
        platform_username="sam",
        display_name="Sam",
        connection_metadata={"source": "invite"},
    )

    assert result == 88
    assert connection.closed is True
    query, args = connection.fetchval_calls[0]
    assert "ON CONFLICT(platform, platform_user_id) DO UPDATE SET" in query
    assert args[0:5] == (7, "discord", "123", "sam", "Sam")
    assert json.loads(args[5]) == {"source": "invite"}
    assert args[6] == 2500.0
    assert args[7] == 2500.0
    _, user_args = connection.execute_calls[0]
    assert user_args == ("123", 7)


@pytest.mark.asyncio
async def test_list_platform_space_connections_filters_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_platform_space_row()]])
    store = PlatformConnectionStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.list_platform_space_connections(account_user_id=7, platform="Slack")

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "platform = $2" in query
    assert args == (7, "slack")
    assert result == [
        {
            "id": 9,
            "platform": "slack",
            "space_id": "C123",
            "space_kind": "channel",
            "account_user_id": 7,
            "default_response_mode": "mention_or_reply",
            "label": "Ops",
            "metadata": {"notify": "all"},
            "created_at": 100.0,
            "updated_at": 110.0,
        }
    ]


@pytest.mark.asyncio
async def test_get_platform_connections_includes_is_verified_alias() -> None:
    connection = _FakeConnection(fetch_results=[[_make_platform_connection_row()]])
    store = PlatformConnectionStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.get_platform_connections(user_id=7)

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "FROM platform_connections" in query
    assert args == (7,)
    assert result == [
        {
            "id": 1,
            "user_id": 7,
            "platform": "telegram",
            "platform_user_id": "123",
            "platform_username": "sam",
            "display_name": "Sam",
            "verified": True,
            "is_verified": True,
            "verification_code": None,
            "verification_expires_at": None,
            "created_at": 100.0,
            "updated_at": 110.0,
        }
    ]


@pytest.mark.asyncio
async def test_palli_brain_platform_methods_delegate_to_extracted_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.connection_calls: list[dict[str, Any]] = []
            self.surface_calls: list[dict[str, Any]] = []

        async def get_platform_connections(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.connection_calls.append(kwargs)
            return [{"platform": "discord", "is_verified": True}]

        async def resolve_platform_surface_id(self, **kwargs: Any) -> int:
            self.surface_calls.append(kwargs)
            return 91

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._platform_connection_store = stub

    connections = await brain.get_platform_connections(7)
    surface_id = await brain.resolve_platform_surface_id(
        platform="discord",
        surface_id="C123",
        surface_kind="channel",
        label="General",
        metadata={"lane": "chat"},
    )

    assert connections == [{"platform": "discord", "is_verified": True}]
    assert surface_id == 91
    assert stub.connection_calls == [{"user_id": 7}]
    assert stub.surface_calls == [
        {
            "platform": "discord",
            "surface_id": "C123",
            "surface_kind": "channel",
            "label": "General",
            "metadata": {"lane": "chat"},
        }
    ]
