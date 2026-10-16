from __future__ import annotations

from typing import Any

import pytest

from core.app_user_store import AppUserStore
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
        fetchval_results: list[Any] | None = None,
        fetchrow_results: list[Any] | None = None,
        fetch_results: list[Any] | None = None,
        execute_results: list[Any] | None = None,
        execute_exceptions: list[Exception | None] | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_results = list(fetch_results or [])
        self.execute_results = list(execute_results or [])
        self.execute_exceptions = list(execute_exceptions or [])
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False
        self.transaction_handle = _FakeTransaction()

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

    async def execute(self, query: str, *args: Any) -> Any:
        self.execute_calls.append((query, args))
        if self.execute_exceptions:
            exc = self.execute_exceptions.pop(0)
            if exc is not None:
                raise exc
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


def _app_user_row() -> tuple[Any, ...]:
    return (
        3,
        "owner@example.com",
        "hashed-password",
        "google-sub-123",
        "admin",
        71,
        1,
        "cus_123",
        "cs_456",
        "pi_789",
        "9988776655",
        "owner_handle",
        111.0,
        1,
        222.0,
        0,
        None,
        333.0,
        444.0,
        "tos-v1",
        "privacy-v1",
        "127.0.0.1",
        "pytest-agent",
        "+15555550123",
        "mfa-secret",
        1,
        555.0,
        666.0,
    )


@pytest.mark.asyncio
async def test_create_app_user_returns_id_even_if_security_row_creation_fails() -> None:
    connection = _FakeConnection(fetchval_results=[15], execute_exceptions=[RuntimeError("security row failed")])
    store = AppUserStore(_FakeBrain(connection), now=lambda: 1234.5)

    user_id = await store.create_app_user(
        email="owner@example.com",
        password_hash="hashed-password",
        google_sub="google-sub-123",
        role="admin",
        palli_user_id=71,
    )

    assert connection.closed is True
    assert user_id == 15
    insert_query, insert_args = connection.fetchval_calls[0]
    assert "INSERT INTO users" in insert_query
    assert insert_args == (
        "owner@example.com",
        "hashed-password",
        "google-sub-123",
        "admin",
        71,
        1234.5,
        1234.5,
    )
    security_query, security_args = connection.execute_calls[0]
    assert "INSERT INTO user_security" in security_query
    assert security_args == (15, 1234.5)


@pytest.mark.asyncio
async def test_get_app_user_by_id_maps_owner_account_record_shape() -> None:
    connection = _FakeConnection(fetchrow_results=[_app_user_row()])
    store = AppUserStore(_FakeBrain(connection))

    record = await store.get_app_user_by_id(3)

    assert connection.closed is True
    query, args = connection.fetchrow_calls[0]
    assert "FROM users" in query
    assert args == (3,)
    assert record == {
        "id": 3,
        "email": "owner@example.com",
        "password_hash": "hashed-password",
        "google_sub": "google-sub-123",
        "role": "admin",
        "palli_user_id": 71,
        "has_access": True,
        "stripe_customer_id": "cus_123",
        "stripe_checkout_session_id": "cs_456",
        "stripe_payment_intent_id": "pi_789",
        "discord_user_id": "9988776655",
        "telegram_username": "owner_handle",
        "beta_joined_at": 111.0,
        "email_verified": True,
        "email_verified_at": 222.0,
        "age_confirmed": False,
        "age_confirmed_at": None,
        "tos_accepted_at": 333.0,
        "privacy_accepted_at": 444.0,
        "tos_version": "tos-v1",
        "privacy_version": "privacy-v1",
        "consent_ip": "127.0.0.1",
        "consent_user_agent": "pytest-agent",
        "phone_number": "+15555550123",
        "mfa_secret": "mfa-secret",
        "mfa_enabled": True,
        "created_at": 555.0,
        "updated_at": 666.0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "lookup_value", "expected_fragment"),
    [
        ("get_app_user_by_email", "Owner@Example.com", "lower(email) = lower($1)"),
        ("get_app_user_by_google_sub", "google-sub-123", "WHERE google_sub = $1"),
        ("get_app_user_by_discord_id", "9988776655", "WHERE discord_user_id = $1"),
    ],
)
async def test_lookup_methods_resolve_matching_user_id_and_delegate_to_full_row_load(
    method_name: str,
    lookup_value: str,
    expected_fragment: str,
) -> None:
    lookup_connection = _FakeConnection(fetchrow_results=[(3,)])
    record_connection = _FakeConnection(fetchrow_results=[_app_user_row()])
    store = AppUserStore(_FakeBrain(lookup_connection, record_connection))

    record = await getattr(store, method_name)(lookup_value)

    assert lookup_connection.closed is True
    assert record_connection.closed is True
    lookup_query, lookup_args = lookup_connection.fetchrow_calls[0]
    assert expected_fragment in lookup_query
    assert lookup_args == (lookup_value,)
    assert record == {
        "id": 3,
        "email": "owner@example.com",
        "password_hash": "hashed-password",
        "google_sub": "google-sub-123",
        "role": "admin",
        "palli_user_id": 71,
        "has_access": True,
        "stripe_customer_id": "cus_123",
        "stripe_checkout_session_id": "cs_456",
        "stripe_payment_intent_id": "pi_789",
        "discord_user_id": "9988776655",
        "telegram_username": "owner_handle",
        "beta_joined_at": 111.0,
        "email_verified": True,
        "email_verified_at": 222.0,
        "age_confirmed": False,
        "age_confirmed_at": None,
        "tos_accepted_at": 333.0,
        "privacy_accepted_at": 444.0,
        "tos_version": "tos-v1",
        "privacy_version": "privacy-v1",
        "consent_ip": "127.0.0.1",
        "consent_user_agent": "pytest-agent",
        "phone_number": "+15555550123",
        "mfa_secret": "mfa-secret",
        "mfa_enabled": True,
        "created_at": 555.0,
        "updated_at": 666.0,
    }


@pytest.mark.asyncio
async def test_update_app_user_filters_unknown_fields_and_stamps_updated_at() -> None:
    connection = _FakeConnection()
    store = AppUserStore(_FakeBrain(connection), now=lambda: 9999.0)

    updated = await store.update_app_user(
        7,
        {
            "email": "new-owner@example.com",
            "ignored_field": "skip-me",
            "mfa_enabled": True,
        },
    )

    assert connection.closed is True
    assert updated is True
    query, args = connection.execute_calls[0]
    assert "UPDATE users" in query
    assert "email = $1" in query
    assert "mfa_enabled = $2" in query
    assert "ignored_field" not in query
    assert "updated_at = $3" in query
    assert "WHERE id = $4" in query
    assert args == ("new-owner@example.com", True, 9999.0, 7)


@pytest.mark.asyncio
async def test_palli_brain_app_user_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.create_calls: list[dict[str, Any]] = []
            self.get_by_id_calls: list[dict[str, Any]] = []
            self.get_by_email_calls: list[dict[str, Any]] = []
            self.get_by_google_sub_calls: list[dict[str, Any]] = []
            self.get_by_discord_id_calls: list[dict[str, Any]] = []
            self.update_calls: list[dict[str, Any]] = []

        async def create_app_user(self, **kwargs: Any) -> int:
            self.create_calls.append(kwargs)
            return 41

        async def get_app_user_by_id(self, **kwargs: Any) -> dict[str, Any]:
            self.get_by_id_calls.append(kwargs)
            return {"id": kwargs["user_id"]}

        async def get_app_user_by_email(self, **kwargs: Any) -> dict[str, Any]:
            self.get_by_email_calls.append(kwargs)
            return {"id": 42}

        async def get_app_user_by_google_sub(self, **kwargs: Any) -> dict[str, Any]:
            self.get_by_google_sub_calls.append(kwargs)
            return {"id": 43}

        async def get_app_user_by_discord_id(self, **kwargs: Any) -> dict[str, Any]:
            self.get_by_discord_id_calls.append(kwargs)
            return {"id": 44}

        async def update_app_user(self, **kwargs: Any) -> bool:
            self.update_calls.append(kwargs)
            return True

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._app_user_store = stub

    user_id = await brain.create_app_user(
        email="owner@example.com",
        password_hash="hash",
        google_sub="google-sub-123",
        role="admin",
        palli_user_id=71,
    )
    user_by_id = await brain.get_app_user_by_id(41)
    user_by_email = await brain.get_app_user_by_email("owner@example.com")
    user_by_google_sub = await brain.get_app_user_by_google_sub("google-sub-123")
    user_by_discord_id = await brain.get_app_user_by_discord_id("9988776655")
    updated = await brain.update_app_user(41, {"email": "new-owner@example.com"})

    assert user_id == 41
    assert user_by_id == {"id": 41}
    assert user_by_email == {"id": 42}
    assert user_by_google_sub == {"id": 43}
    assert user_by_discord_id == {"id": 44}
    assert updated is True
    assert stub.create_calls == [
        {
            "email": "owner@example.com",
            "password_hash": "hash",
            "google_sub": "google-sub-123",
            "role": "admin",
            "palli_user_id": 71,
        }
    ]
    assert stub.get_by_id_calls == [{"user_id": 41}]
    assert stub.get_by_email_calls == [{"email": "owner@example.com"}]
    assert stub.get_by_google_sub_calls == [{"google_sub": "google-sub-123"}]
    assert stub.get_by_discord_id_calls == [{"discord_user_id": "9988776655"}]
    assert stub.update_calls == [{"user_id": 41, "updates": {"email": "new-owner@example.com"}}]
