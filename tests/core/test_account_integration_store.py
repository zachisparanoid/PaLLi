from __future__ import annotations

import json
from typing import Any

import pytest

from core.account_integration_store import AccountIntegrationStore
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


def _make_oauth_connection_row() -> tuple[Any, ...]:
    row = (
        5,
        7,
        "google",
        "google-user-1",
        "owner@palli.local",
        "access-token",
        "refresh-token",
        4100.0,
        json.dumps(["gmail.readonly", "calendar.events"]),
        "active",
        4050.0,
        "",
        4000.0,
        4060.0,
    )
    assert len(row) == 14
    return row


def _make_onboarding_progress_row() -> tuple[Any, ...]:
    row = (
        9,
        7,
        "guided_setup",
        json.dumps(["platform_connections", "profile_setup"]),
        True,
        False,
        True,
        None,
        4000.0,
        4050.0,
    )
    assert len(row) == 10
    return row


@pytest.mark.asyncio
async def test_link_discord_guild_to_account_updates_platform_space_mapping_metadata() -> None:
    connection = _FakeConnection(fetchval_results=[31])
    store = AccountIntegrationStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.link_discord_guild_to_account(
        guild_id=123,
        account_user_id=7,
        default_channel_id=456,
    )

    assert result == 31
    assert connection.closed is True
    insert_query, insert_args = connection.fetchval_calls[0]
    assert "INSERT INTO discord_guild_accounts" in insert_query
    assert insert_args == (123, 7, 456, 2500.0, 2500.0)
    mirror_query, mirror_args = connection.execute_calls[0]
    assert "INSERT INTO platform_space_connections" in mirror_query
    assert mirror_args[0:2] == ("123", 7)
    assert json.loads(mirror_args[2]) == {"default_channel_id": 456}
    assert mirror_args[3:] == (2500.0, 2500.0)


@pytest.mark.asyncio
async def test_get_account_for_discord_guild_falls_back_to_platform_space_connection() -> None:
    connection = _FakeConnection(
        fetchrow_results=[
            None,
            (11, "123", 7, json.dumps({"default_channel_id": 456}), 100.0, 110.0),
        ]
    )
    store = AccountIntegrationStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.get_account_for_discord_guild(123)

    assert connection.closed is True
    first_query, first_args = connection.fetchrow_calls[0]
    assert "FROM discord_guild_accounts" in first_query
    assert first_args == (123,)
    fallback_query, fallback_args = connection.fetchrow_calls[1]
    assert "FROM platform_space_connections" in fallback_query
    assert fallback_args == ("123",)
    assert result == {
        "id": 11,
        "guild_id": 123,
        "account_user_id": 7,
        "default_channel_id": 456,
        "created_at": 100.0,
        "updated_at": 110.0,
    }


@pytest.mark.asyncio
async def test_upsert_account_features_merges_existing_values_and_preserves_created_at() -> None:
    # E.2 step 5a: allow_multi_analysis column dropped - row is one column
    # narrower (was index 6 in the tuple).
    connection = _FakeConnection(
        fetchrow_results=[(7, True, False, True, True, False, False, 1200.0, 1300.0)]
    )
    store = AccountIntegrationStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.upsert_account_features(
        7,
        allow_email=True,
        allow_voice=False,
    )

    assert result == 7
    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "INSERT INTO account_features" in query
    assert args[0] == 7
    # E.2 step 5a: 6 feature flags now (was 7). Verify the slice does NOT
    # include the multi_analysis column.
    assert args[1:7] == (True, True, True, False, False, False)
    assert args[7] == 1200.0
    assert args[8] == 2500.0


@pytest.mark.asyncio
async def test_get_account_features_returns_allow_all_defaults_when_absent() -> None:
    connection = _FakeConnection(fetchrow_results=[None])
    store = AccountIntegrationStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.get_account_features(7)

    assert connection.closed is True
    # E.2 step 5a: allow_multi_analysis dropped from default features.
    assert result == {
        "allow_web_search": True,
        "allow_email": True,
        "allow_calendar": True,
        "allow_voice": True,
        "allow_proactive": True,
        "allow_personality_learning": True,
    }


@pytest.mark.asyncio
async def test_store_oauth_connection_serializes_scopes_and_returns_id() -> None:
    connection = _FakeConnection(fetchval_results=[44])
    store = AccountIntegrationStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.store_oauth_connection(
        user_id=7,
        provider="google",
        provider_user_id="google-user-1",
        provider_email="owner@palli.local",
        access_token="access-token",
        refresh_token="refresh-token",
        token_expires_at=4100.0,
        scopes=["gmail.readonly", "calendar.events"],
    )

    assert result == 44
    assert connection.closed is True
    query, args = connection.fetchval_calls[0]
    assert "INSERT INTO oauth_connections" in query
    assert args[0:7] == (
        7,
        "google",
        "google-user-1",
        "owner@palli.local",
        "access-token",
        "refresh-token",
        4100.0,
    )
    assert json.loads(args[7]) == ["gmail.readonly", "calendar.events"]
    assert args[8:] == (2500.0, 2500.0)


@pytest.mark.asyncio
async def test_get_oauth_connections_maps_rows_and_decodes_scopes() -> None:
    connection = _FakeConnection(fetch_results=[[_make_oauth_connection_row()]])
    store = AccountIntegrationStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.get_oauth_connections(7)

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "FROM oauth_connections" in query
    assert args == (7,)
    assert result == [
        {
            "id": 5,
            "user_id": 7,
            "provider": "google",
            "provider_user_id": "google-user-1",
            "provider_email": "owner@palli.local",
            "access_token_encrypted": "access-token",
            "refresh_token_encrypted": "refresh-token",
            "token_expires_at": 4100.0,
            "scopes": ["gmail.readonly", "calendar.events"],
            "connection_status": "active",
            "last_sync_at": 4050.0,
            "error_message": "",
            "created_at": 4000.0,
            "updated_at": 4060.0,
        }
    ]


@pytest.mark.asyncio
async def test_update_and_get_onboarding_progress_preserve_completion_flags() -> None:
    connection = _FakeConnection(fetchrow_results=[_make_onboarding_progress_row()])
    store = AccountIntegrationStore(_FakeBrain(connection), now=lambda: 2500.0)

    updated = await store.update_onboarding_progress(
        user_id=7,
        current_step="guided_setup_complete",
        completed_steps=["platform_connections", "profile_setup"],
    )
    fetched = await store.get_onboarding_progress(7)

    assert connection.closed is True
    update_query, update_args = connection.execute_calls[0]
    assert "INSERT INTO onboarding_progress" in update_query
    assert update_args[0:2] == (7, "guided_setup_complete")
    assert json.loads(update_args[2]) == ["platform_connections", "profile_setup"]
    assert update_args[3:6] == (True, False, True)
    assert update_args[6:] == (2500.0, 2500.0)
    assert updated == {
        "user_id": 7,
        "current_step": "guided_setup_complete",
        "completed_steps": ["platform_connections", "profile_setup"],
        "platform_connections_completed": True,
        "oauth_connections_completed": False,
        "profile_setup_completed": True,
    }
    assert fetched == {
        "id": 9,
        "user_id": 7,
        "current_step": "guided_setup",
        "completed_steps": ["platform_connections", "profile_setup"],
        "platform_connections_completed": True,
        "oauth_connections_completed": False,
        "profile_setup_completed": True,
        "completed_at": None,
        "created_at": 4000.0,
        "updated_at": 4050.0,
    }


@pytest.mark.asyncio
async def test_palli_brain_account_integration_methods_delegate_to_extracted_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.guild_calls: list[dict[str, Any]] = []
            self.progress_calls: list[dict[str, Any]] = []

        async def get_account_for_discord_guild(self, guild_id: int) -> dict[str, Any]:
            self.guild_calls.append({"guild_id": guild_id})
            return {"account_user_id": 7}

        async def update_onboarding_progress(self, **kwargs: Any) -> dict[str, Any]:
            self.progress_calls.append(kwargs)
            return {"current_step": kwargs["current_step"]}

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._account_integration_store = stub

    mapping = await brain.get_account_for_discord_guild(123)
    progress = await brain.update_onboarding_progress(
        user_id=7,
        current_step="guided_setup_complete",
        completed_steps=["profile_setup"],
    )

    assert mapping == {"account_user_id": 7}
    assert progress == {"current_step": "guided_setup_complete"}
    assert stub.guild_calls == [{"guild_id": 123}]
    assert stub.progress_calls == [
        {
            "user_id": 7,
            "current_step": "guided_setup_complete",
            "completed_steps": ["profile_setup"],
        }
    ]
