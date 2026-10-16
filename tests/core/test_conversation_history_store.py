from __future__ import annotations

import json
from typing import Any

import pytest

from core.conversation_history_store import ConversationHistoryStore
from core.database import PalliBrain


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
        self.user_interaction_updates: list[tuple[Any, int, str]] = []

    async def get_connection(self) -> _FakeConnection:
        return self._connection

    async def _update_user_interaction_internal(self, conn: Any, user_id: int, platform: str) -> None:
        self.user_interaction_updates.append((conn, user_id, platform))

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

    @staticmethod
    def _pair_placeholders(count: int, start: int = 1) -> str:
        return ",".join(f"(${index}, ${index + 1})" for index in range(start, start + count * 2, 2))


@pytest.mark.asyncio
async def test_store_conversation_sets_display_username_and_updates_user_interactions() -> None:
    connection = _FakeConnection(fetchval_results=[17])
    brain = _FakeBrain(connection)
    store = ConversationHistoryStore(brain, now=lambda: 1234.5)

    conversation_id = await store.store_conversation(
        user_id=7,
        platform="discord",
        channel_id=42,
        surface_id=" dm-42 ",
        thread_id=" owner:7:thread:main ",
        role="user",
        content="hello there",
        metadata={"username": "Sam", "is_dm": True},
    )

    assert connection.closed is True
    assert conversation_id == 17
    _, args = connection.fetchval_calls[0]
    assert args[0] == 7
    assert args[1] == "discord"
    assert args[2] == 42
    assert args[3] == "dm-42"
    assert args[4] == "owner:7:thread:main"
    assert args[5] == "user"
    assert args[6] == "Sam"
    assert args[7] == "hello there"
    assert args[8] == 1234.5
    assert json.loads(args[9]) == {"username": "Sam", "is_dm": True}
    assert brain.user_interaction_updates == [(connection, 7, "discord")]


@pytest.mark.asyncio
async def test_export_user_conversations_merges_safe_owned_and_legacy_rows() -> None:
    connection = _FakeConnection(
        fetch_results=[
            [("discord", 42)],
            [
                (1, 7, "discord", 42, "user", "Sam", "hi", 1000.0, json.dumps({"is_dm": True})),
                (2, 7, "discord", 42, "assistant", "Me", "hello", 1001.0, json.dumps({"source": "reply"})),
            ],
            [
                (1, 7, "discord", 42, "user", "Sam", "hi", 1000.0, json.dumps({"is_dm": True})),
                (3, 7, "web", 99, "user", "Sam", "dashboard", 1002.0, json.dumps({"surface": "mission-control"})),
            ],
            [
                (4, None, "discord", 777, "assistant", "Me", "remembering this", 1003.0, json.dumps({"target_user_id": 7})),
            ],
        ]
    )
    store = ConversationHistoryStore(_FakeBrain(connection), now=lambda: 2000.0)

    exported = await store.export_user_conversations([7, 7, "skip"])

    assert connection.closed is True
    assert exported["exported_at"] == 2000.0
    assert exported["user_ids"] == [7]
    assert [thread["channel_id"] for thread in exported["threads"]] == [777, 99, 42]
    assert exported["threads"][0]["messages"][0]["metadata"] == {"target_user_id": 7}
    assert exported["threads"][1]["full_transcript"] is False
    assert exported["threads"][2]["full_transcript"] is True
    assert [message["id"] for message in exported["threads"][2]["messages"]] == [1, 2]


@pytest.mark.asyncio
async def test_get_conversation_history_builds_filters_and_returns_chronological_rows() -> None:
    connection = _FakeConnection(
        fetch_results=[
            [
                (11, 7, "discord", 42, "dm-42", "thread-1", "assistant", "Me", "later", 2000.0, json.dumps({"kind": "reply"})),
                (10, 7, "discord", 42, "dm-42", "thread-1", "user", "Sam", "earlier", 1000.0, json.dumps({"kind": "prompt"})),
            ]
        ]
    )
    store = ConversationHistoryStore(_FakeBrain(connection))

    history = await store.get_conversation_history(
        user_id=7,
        platform="discord",
        channel_id=42,
        surface_id="dm-42",
        thread_id="thread-1",
        limit=2,
        include_metadata=True,
    )

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "user_id = $1" in query
    assert "platform = $2" in query
    assert "channel_id = $3" in query
    assert "surface_id = $4" in query
    assert "thread_id = $5" in query
    assert args == (7, "discord", 42, "dm-42", "thread-1", 2)
    assert [item["id"] for item in history] == [10, 11]
    assert history[0]["metadata"] == {"kind": "prompt"}
    assert history[1]["surface_id"] == "dm-42"


@pytest.mark.asyncio
async def test_get_thread_conversation_history_around_message_id_returns_context_window() -> None:
    connection = _FakeConnection(
        fetchrow_results=[
            (12, 7, "discord", 42, "dm-42", "thread-1", "assistant", "Me", "anchored reply", 1200.0, json.dumps({"kind": "reply"})),
        ],
        fetch_results=[
            [
                (11, 7, "discord", 42, "dm-42", "thread-1", "user", "Sam", "right before", 1100.0, json.dumps({"kind": "prompt"})),
                (10, 7, "discord", 42, "dm-42", "thread-1", "assistant", "Me", "older context", 1000.0, json.dumps({"kind": "reply"})),
            ],
            [
                (13, 7, "discord", 42, "dm-42", "thread-1", "user", "Sam", "newer follow-up", 1300.0, json.dumps({"kind": "prompt"})),
            ],
        ],
    )
    store = ConversationHistoryStore(_FakeBrain(connection))

    history = await store.get_thread_conversation_history(
        thread_id=" thread-1 ",
        around_message_id=12,
        limit=4,
        include_metadata=True,
    )

    assert connection.closed is True
    anchor_query, anchor_args = connection.fetchrow_calls[0]
    assert "WHERE thread_id = $1 AND id = $2" in anchor_query
    assert anchor_args == ("thread-1", 12)

    older_query, older_args = connection.fetch_calls[0]
    assert "timestamp < $2" in older_query
    assert "ORDER BY timestamp DESC, id DESC" in older_query
    assert older_args == ("thread-1", 1200.0, 12, 4)

    newer_query, newer_args = connection.fetch_calls[1]
    assert "timestamp > $2" in newer_query
    assert "ORDER BY timestamp ASC, id ASC" in newer_query
    assert newer_args == ("thread-1", 1200.0, 12, 4)

    assert [item["id"] for item in history] == [10, 11, 12, 13]
    assert history[2]["metadata"] == {"kind": "reply"}


@pytest.mark.asyncio
async def test_search_thread_conversation_history_filters_by_thread_text_and_date() -> None:
    connection = _FakeConnection(
        fetch_results=[
            [
                (11, 7, "discord", 42, "dm-42", "thread-2", "assistant", "Me", "Follow up tomorrow", 2000.0, json.dumps({"kind": "reply"})),
                (10, 7, "discord", 42, "dm-42", "thread-1", "user", "Sam", "Follow up on the invoice", 1500.0, json.dumps({"kind": "prompt"})),
            ]
        ]
    )
    store = ConversationHistoryStore(_FakeBrain(connection))

    results = await store.search_thread_conversation_history(
        thread_ids=["thread-1", "thread-2", "thread-1", ""],
        query="follow up",
        after=1200.0,
        before=2200.0,
        limit=3,
        include_metadata=True,
    )

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "thread_id IN ($1,$2)" in query
    assert "LOWER(COALESCE(content, '') || ' ' || COALESCE(username, '')) LIKE $3" in query
    assert "LOWER(COALESCE(content, '') || ' ' || COALESCE(username, '')) LIKE $4" in query
    assert "timestamp >= $5" in query
    assert "timestamp <= $6" in query
    assert "ORDER BY timestamp DESC LIMIT $7" in query
    assert args == ("thread-1", "thread-2", "%follow%", "%up%", 1200.0, 2200.0, 3)
    assert [item["id"] for item in results] == [11, 10]
    assert results[0]["metadata"] == {"kind": "reply"}


@pytest.mark.asyncio
async def test_backfill_conversation_thread_id_uses_surface_or_channel_scope() -> None:
    connection = _FakeConnection()
    store = ConversationHistoryStore(_FakeBrain(connection))

    await store.backfill_conversation_thread_id(
        platform="discord",
        channel_id=42,
        surface_id=" dm-42 ",
        thread_id="thread-surface",
    )
    await store.backfill_conversation_thread_id(
        platform="discord",
        channel_id=84,
        thread_id="thread-channel",
    )

    assert connection.closed is True
    surface_query, surface_args = connection.execute_calls[0]
    assert "surface_id = $2" in surface_query
    assert surface_args == ("discord", "dm-42", "thread-surface")

    channel_query, channel_args = connection.execute_calls[1]
    assert "channel_id = $2" in channel_query
    assert channel_args == ("discord", 84, "thread-channel")


@pytest.mark.asyncio
async def test_get_unified_dm_conversation_history_resolves_ids_and_reverses_order() -> None:
    connection = _FakeConnection(
        fetchval_results=["12345"],
        fetchrow_results=[(88,)],
        fetch_results=[
            [("discord", "777"), ("slack", "subject-user"), ("telegram", None)],
            [
                (2, 777, "discord", 42, "dm-42", "thread-1", "assistant", "Me", "reply", 2000.0, json.dumps({"is_dm": True})),
                (1, 12345, "discord", 42, "dm-42", "thread-1", "user", "Sam", "hi", 1000.0, json.dumps({"is_dm": True})),
            ],
        ],
    )
    store = ConversationHistoryStore(_FakeBrain(connection))

    history = await store.get_unified_dm_conversation_history(
        account_user_id=5,
        current_user_id=999,
        limit=3,
        include_metadata=True,
    )

    assert connection.closed is True
    assert connection.fetchval_calls[0][1] == (5,)
    assert connection.fetch_calls[0][1] == (5,)
    assert connection.fetchrow_calls[0][1] == ("slack", "subject-user")
    query, args = connection.fetch_calls[1]
    assert "json_extract(metadata, '$.is_dm')" in query
    # 2026-07-23 defensive fix: the identity set now SEEDS account_user_id
    # itself (local-mode rows live under it with no snowflake/connection —
    # the wrong-key blindness class), so 5 joins both IN-lists.
    assert args == (5, 88, 777, 999, 12345, 5, 88, 777, 999, 12345, 3)
    assert [item["id"] for item in history] == [1, 2]
    assert history[0]["metadata"] == {"is_dm": True}
    assert history[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_palli_brain_conversation_history_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.store_calls: list[dict[str, Any]] = []
            self.export_calls: list[list[int]] = []
            self.history_calls: list[dict[str, Any]] = []
            self.thread_history_calls: list[dict[str, Any]] = []
            self.search_thread_history_calls: list[dict[str, Any]] = []
            self.backfill_calls: list[dict[str, Any]] = []
            self.unified_dm_calls: list[dict[str, Any]] = []

        async def store_conversation(self, **kwargs: Any) -> int:
            self.store_calls.append(kwargs)
            return 41

        async def export_user_conversations(self, user_ids: list[int]) -> dict[str, Any]:
            self.export_calls.append(user_ids)
            return {"threads": []}

        async def get_conversation_history(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.history_calls.append(kwargs)
            return [{"id": 1}]

        async def get_thread_conversation_history(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.thread_history_calls.append(kwargs)
            return [{"id": 2}]

        async def search_thread_conversation_history(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.search_thread_history_calls.append(kwargs)
            return [{"id": 4}]

        async def backfill_conversation_thread_id(self, **kwargs: Any) -> None:
            self.backfill_calls.append(kwargs)

        async def get_unified_dm_conversation_history(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.unified_dm_calls.append(kwargs)
            return [{"id": 3}]

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._conversation_history_store = stub

    stored_id = await brain.store_conversation(
        user_id=7,
        platform="discord",
        channel_id=42,
        thread_id="thread-1",
        role="assistant",
        content="hello",
        username="Me",
        metadata={"kind": "reply"},
        surface_id="dm-42",
    )
    exported = await brain.export_user_conversations([7])
    history = await brain.get_conversation_history(platform="discord", channel_id=42, limit=2)
    thread_history = await brain.get_thread_conversation_history(thread_id="thread-1", limit=2, around_message_id=8)
    search_results = await brain.search_thread_conversation_history(
        thread_ids=["thread-1", "thread-2"],
        query="follow up",
        after=1000.0,
        before=2000.0,
        limit=3,
    )
    await brain.backfill_conversation_thread_id(
        platform="discord",
        channel_id=42,
        surface_id="dm-42",
        thread_id="thread-1",
    )
    unified_dm = await brain.get_unified_dm_conversation_history(account_user_id=5, current_user_id=7, limit=4)

    assert stored_id == 41
    assert exported == {"threads": []}
    assert history == [{"id": 1}]
    assert thread_history == [{"id": 2}]
    assert search_results == [{"id": 4}]
    assert unified_dm == [{"id": 3}]
    assert stub.store_calls == [
        {
            "user_id": 7,
            "platform": "discord",
            "channel_id": 42,
            "thread_id": "thread-1",
            "role": "assistant",
            "content": "hello",
            "username": "Me",
            "metadata": {"kind": "reply"},
            "surface_id": "dm-42",
        }
    ]
    assert stub.export_calls == [[7]]
    assert stub.history_calls == [{"user_id": None, "platform": "discord", "channel_id": 42, "surface_id": None, "thread_id": None, "limit": 2, "include_metadata": False}]
    assert stub.thread_history_calls == [{"thread_id": "thread-1", "limit": 2, "include_metadata": False, "around_message_id": 8}]
    assert stub.search_thread_history_calls == [
        {
            "thread_ids": ["thread-1", "thread-2"],
            "query": "follow up",
            "after": 1000.0,
            "before": 2000.0,
            "limit": 3,
            "include_metadata": False,
        }
    ]
    assert stub.backfill_calls == [{"platform": "discord", "channel_id": 42, "surface_id": "dm-42", "thread_id": "thread-1"}]
    assert stub.unified_dm_calls == [{"account_user_id": 5, "current_user_id": 7, "limit": 4, "include_metadata": False}]


@pytest.mark.asyncio
async def test_get_conversation_history_excludes_reminder_fire_machinery():
    """reminder_fire dispatches are machinery, not conversation, and must never
    seed conversational context. (2026-07-13: a stuck reminder fired 220x and
    its riffs buried the real thread in recall.)"""
    conn = _FakeConnection(fetch_results=[[]])
    store = ConversationHistoryStore(_FakeBrain(conn))
    await store.get_conversation_history(user_id=1, channel_id=5, limit=50)
    assert conn.fetch_calls, "expected a fetch call"
    query = conn.fetch_calls[0][0]
    assert "reminder_fire" in query and "!=" in query, (
        "get_conversation_history must exclude platform='reminder_fire'"
    )
