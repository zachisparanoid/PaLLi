from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.identity.profile_store import ProfileReflectionStore


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchval_results: list[Any] | None = None,
        fetchrow_results: list[Any] | None = None,
        fetch_results: list[Any] | None = None,
        execute_results: list[str] | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_results = list(fetch_results or [])
        self.execute_results = list(execute_results or [])
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
        if self.execute_results:
            return self.execute_results.pop(0)
        return "UPDATE 1"

    async def close(self) -> None:
        self.closed = True


class _FakeBrain:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def get_connection(self) -> _FakeConnection:
        return self._connection

    @staticmethod
    def _rowcount(status: str) -> int:
        if not status:
            return 0
        parts = status.split()
        if not parts:
            return 0
        last = parts[-1]
        return int(last) if last.isdigit() else 0

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
async def test_get_user_profile_creates_default_profile_when_missing() -> None:
    connection = _FakeConnection(execute_results=["UPDATE 0", "INSERT 1"])
    store = ProfileReflectionStore(_FakeBrain(connection), now=lambda: 1234.5)

    profile = await store.get_user_profile(7, "discord")

    assert connection.closed is True
    assert profile == {
        "user_id": 7,
        "platform": "discord",
        "username": None,
        "display_name": None,
        "personality_data": {},
        "communication_style": {},
        "preferences": {},
        "habits": {},
        "emotional_patterns": {},
        "learning_progress": 0.0,
        "last_interaction": 1234.5,
        "total_interactions": 0,
        "personality_preset": "default",
        "created_at": 1234.5,
        "updated_at": 1234.5,
    }
    assert len(connection.fetchrow_calls) == 2
    update_query, update_args = connection.execute_calls[0]
    assert "UPDATE user_profiles" in update_query
    assert update_args[-2:] == (7, "discord")
    insert_query, insert_args = connection.execute_calls[1]
    assert "INSERT INTO user_profiles" in insert_query
    assert insert_args[0:2] == (7, "discord")
    assert insert_args[12:] == ("default", 1234.5, 1234.5)


@pytest.mark.asyncio
async def test_update_user_profile_merges_nested_fields_before_update() -> None:
    row = (
        7,
        "discord",
        "old-name",
        "Old Display",
        json.dumps({"existing": True}),
        json.dumps({"pace": "calm"}),
        json.dumps({"volume": "low"}),
        json.dumps({"routine": "night"}),
        json.dumps({"mood": "steady"}),
        0.4,
        1000.0,
        3,
        "default",
        900.0,
        950.0,
    )
    connection = _FakeConnection(fetchrow_results=[row], execute_results=["UPDATE 1"])
    store = ProfileReflectionStore(_FakeBrain(connection), now=lambda: 2000.0)

    await store.update_user_profile(
        7,
        "discord",
        {
            "username": "sam",
            "personality_data": {"tone": "warm"},
            "preferences": {"voice": "brief"},
            "total_interactions": 9,
        },
    )

    assert connection.closed is True
    assert len(connection.execute_calls) == 1
    query, args = connection.execute_calls[0]
    assert "UPDATE user_profiles" in query
    assert args[0] == "sam"
    assert json.loads(args[2]) == {"existing": True, "tone": "warm"}
    assert json.loads(args[4]) == {"volume": "low", "voice": "brief"}
    assert args[9] == 9
    assert args[11] == 2000.0
    assert args[-2:] == (7, "discord")


@pytest.mark.asyncio
async def test_update_user_interaction_creates_profile_on_first_touch() -> None:
    connection = _FakeConnection(
        fetchrow_results=[None],
        execute_results=["UPDATE 0", "UPDATE 0", "INSERT 1"],
    )
    store = ProfileReflectionStore(_FakeBrain(connection), now=lambda: 3000.0)

    await store.update_user_interaction(9, "local")

    assert connection.closed is True
    initial_update_query, initial_update_args = connection.execute_calls[0]
    assert "total_interactions = total_interactions + 1" in initial_update_query
    assert initial_update_args == (3000.0, 9, "local")
    insert_query, insert_args = connection.execute_calls[-1]
    assert "INSERT INTO user_profiles" in insert_query
    assert insert_args[0:2] == (9, "local")
    assert insert_args[11] == 1


@pytest.mark.asyncio
async def test_identity_document_version_create_and_list_shape_records() -> None:
    connection = _FakeConnection(
        fetch_results=[
            [
                (
                    "USER_RELATIONSHIP",
                    "identity/user/USER_RELATIONSHIP.md",
                    4,
                    "agent",
                    "palli",
                    "captured new preference",
                    "content here",
                    json.dumps({"owner": "user"}),
                    4567.0,
                )
            ]
        ]
    )
    store = ProfileReflectionStore(_FakeBrain(connection), now=lambda: 4000.0)

    await store.create_identity_document_version(
        user_id=7,
        platform="discord",
        doc_key="USER_RELATIONSHIP",
        file_path="identity/user/USER_RELATIONSHIP.md",
        version=4,
        actor_type="agent",
        actor_id="palli",
        reason="captured new preference",
        content="content here",
        frontmatter={"owner": "user"},
    )
    versions = await store.list_identity_document_versions(
        user_id=7,
        platform="discord",
        doc_key="USER_RELATIONSHIP",
        limit=5,
    )

    assert connection.closed is True
    create_query, create_args = connection.execute_calls[0]
    assert "INSERT INTO identity_document_versions" in create_query
    assert create_args[-1] == 4000.0
    assert json.loads(create_args[-2]) == {"owner": "user"}
    list_query, list_args = connection.fetch_calls[0]
    assert "doc_key = $3" in list_query
    assert list_args == (7, "discord", "USER_RELATIONSHIP", 5)
    assert versions == [
        {
            "doc_key": "USER_RELATIONSHIP",
            "file_path": "identity/user/USER_RELATIONSHIP.md",
            "version": 4,
            "actor_type": "agent",
            "actor_id": "palli",
            "reason": "captured new preference",
            "content": "content here",
            "frontmatter": {"owner": "user"},
            "created_at": 4567.0,
        }
    ]


@pytest.mark.asyncio
async def test_identity_document_version_detail_shapes_record() -> None:
    connection = _FakeConnection(
        fetchrow_results=[
            (
                "USER_RELATIONSHIP",
                "identity/user/USER_RELATIONSHIP.md",
                4,
                "agent",
                "palli",
                "captured new preference",
                "content here",
                json.dumps({"owner": "user"}),
                4567.0,
            )
        ]
    )
    store = ProfileReflectionStore(_FakeBrain(connection), now=lambda: 4000.0)

    version = await store.get_identity_document_version(
        user_id=7,
        platform="discord",
        doc_key="USER_RELATIONSHIP",
        version=4,
    )

    assert connection.closed is True
    detail_query, detail_args = connection.fetchrow_calls[0]
    assert "FROM identity_document_versions" in detail_query
    assert "version = $4" in detail_query
    assert detail_args == (7, "discord", "USER_RELATIONSHIP", 4)
    assert version == {
        "doc_key": "USER_RELATIONSHIP",
        "file_path": "identity/user/USER_RELATIONSHIP.md",
        "version": 4,
        "actor_type": "agent",
        "actor_id": "palli",
        "reason": "captured new preference",
        "content": "content here",
        "frontmatter": {"owner": "user"},
        "created_at": 4567.0,
    }


@pytest.mark.asyncio
async def test_palli_brain_profile_reflection_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.create_default_profile_calls: list[tuple[int, str]] = []
            self.get_user_profile_calls: list[tuple[int, str]] = []
            self.update_user_profile_calls: list[tuple[int, str, dict[str, Any]]] = []
            self.update_user_profile_internal_calls: list[tuple[Any, int, str, dict[str, Any]]] = []
            self.update_user_interaction_internal_calls: list[tuple[Any, int, str]] = []
            self.update_user_interaction_calls: list[tuple[int, str]] = []
            self.create_identity_document_version_calls: list[dict[str, Any]] = []
            self.list_identity_document_versions_calls: list[dict[str, Any]] = []
            self.get_identity_document_version_calls: list[dict[str, Any]] = []

        def create_default_profile(self, user_id: int, platform: str) -> dict[str, Any]:
            self.create_default_profile_calls.append((user_id, platform))
            return {"user_id": user_id, "platform": platform}

        async def get_user_profile(self, user_id: int, platform: str) -> dict[str, Any]:
            self.get_user_profile_calls.append((user_id, platform))
            return {"user_id": user_id, "platform": platform}

        async def update_user_profile(self, user_id: int, platform: str, updates: dict[str, Any]) -> None:
            self.update_user_profile_calls.append((user_id, platform, updates))

        async def _update_user_profile_internal(
            self,
            conn: Any,
            user_id: int,
            platform: str,
            updates: dict[str, Any],
        ) -> None:
            self.update_user_profile_internal_calls.append((conn, user_id, platform, updates))

        async def _update_user_interaction_internal(self, conn: Any, user_id: int, platform: str) -> None:
            self.update_user_interaction_internal_calls.append((conn, user_id, platform))

        async def update_user_interaction(self, user_id: int, platform: str) -> None:
            self.update_user_interaction_calls.append((user_id, platform))

        async def create_identity_document_version(self, **kwargs: Any) -> None:
            self.create_identity_document_version_calls.append(kwargs)

        async def list_identity_document_versions(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.list_identity_document_versions_calls.append(kwargs)
            return [{"doc_key": "MEMORY_SUMMARY"}]

        async def get_identity_document_version(self, **kwargs: Any) -> dict[str, Any]:
            self.get_identity_document_version_calls.append(kwargs)
            return {"doc_key": "MEMORY_SUMMARY", "version": 2}


    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._profile_reflection_store = stub
    dummy_conn = object()

    default_profile = brain._create_default_profile(7, "discord")
    profile = await brain.get_user_profile(7, "discord")
    await brain.update_user_profile(7, "discord", {"preferences": {"voice": "brief"}})
    await brain._update_user_profile_internal(dummy_conn, 7, "discord", {"username": "sam"})
    await brain._update_user_interaction_internal(dummy_conn, 7, "discord")
    await brain.update_user_interaction(7, "discord")
    await brain.create_identity_document_version(
        user_id=7,
        platform="discord",
        doc_key="MEMORY_SUMMARY",
        file_path="identity/MEMORY_SUMMARY.md",
        version=2,
        actor_type="agent",
        actor_id="palli",
        reason="refresh",
        content="content",
        frontmatter={"owner": "agent"},
    )
    versions = await brain.list_identity_document_versions(
        user_id=7,
        platform="discord",
        doc_key="MEMORY_SUMMARY",
        limit=4,
    )
    version_detail = await brain.get_identity_document_version(
        user_id=7,
        platform="discord",
        doc_key="MEMORY_SUMMARY",
        version=2,
    )

    assert default_profile == {"user_id": 7, "platform": "discord"}
    assert profile == {"user_id": 7, "platform": "discord"}
    assert versions == [{"doc_key": "MEMORY_SUMMARY"}]
    assert version_detail == {"doc_key": "MEMORY_SUMMARY", "version": 2}
    assert stub.create_default_profile_calls == [(7, "discord")]
    assert stub.get_user_profile_calls == [(7, "discord")]
    assert stub.update_user_profile_calls == [(7, "discord", {"preferences": {"voice": "brief"}})]
    assert stub.update_user_profile_internal_calls == [(dummy_conn, 7, "discord", {"username": "sam"})]
    assert stub.update_user_interaction_internal_calls == [(dummy_conn, 7, "discord")]
    assert stub.update_user_interaction_calls == [(7, "discord")]
    assert stub.create_identity_document_version_calls == [
        {
            "user_id": 7,
            "platform": "discord",
            "doc_key": "MEMORY_SUMMARY",
            "file_path": "identity/MEMORY_SUMMARY.md",
            "version": 2,
            "actor_type": "agent",
            "actor_id": "palli",
            "reason": "refresh",
            "content": "content",
            "frontmatter": {"owner": "agent"},
        }
    ]
    assert stub.list_identity_document_versions_calls == [
        {
            "user_id": 7,
            "platform": "discord",
            "doc_key": "MEMORY_SUMMARY",
            "limit": 4,
        }
    ]
    assert stub.get_identity_document_version_calls == [
        {
            "user_id": 7,
            "platform": "discord",
            "doc_key": "MEMORY_SUMMARY",
            "version": 2,
        }
    ]
