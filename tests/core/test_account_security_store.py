from __future__ import annotations

from typing import Any

import pytest

from core.account_security_store import AccountSecurityStore
from core.database import PalliBrain


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchval_results: list[Any] | None = None,
        fetchrow_results: list[Any] | None = None,
        execute_exceptions: list[Exception | None] | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.execute_exceptions = list(execute_exceptions or [])
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
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

    async def execute(self, query: str, *args: Any) -> None:
        self.execute_calls.append((query, args))
        if self.execute_exceptions:
            exc = self.execute_exceptions.pop(0)
            if exc is not None:
                raise exc

    async def close(self) -> None:
        self.closed = True


class _FakeBrain:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def get_connection(self) -> _FakeConnection:
        return self._connection


@pytest.mark.asyncio
async def test_create_password_reset_token_persists_token_and_timestamp() -> None:
    connection = _FakeConnection(fetchval_results=[12])
    store = AccountSecurityStore(_FakeBrain(connection), now=lambda: 1234.5)

    token_id = await store.create_password_reset_token(user_id=7, token_hash="reset-hash", expires_at=9999.0)

    assert connection.closed is True
    assert token_id == 12
    query, args = connection.fetchval_calls[0]
    assert "INSERT INTO password_reset_tokens" in query
    assert args == (7, "reset-hash", 9999.0, 1234.5)


@pytest.mark.asyncio
async def test_get_password_reset_token_maps_latest_row_shape() -> None:
    connection = _FakeConnection(fetchrow_results=[(4, 8, "reset-hash", 4567.0, 1, 4000.0)])
    store = AccountSecurityStore(_FakeBrain(connection))

    record = await store.get_password_reset_token("reset-hash")

    assert connection.closed is True
    query, args = connection.fetchrow_calls[0]
    assert "FROM password_reset_tokens" in query
    assert "ORDER BY id DESC" in query
    assert args == ("reset-hash",)
    assert record == {
        "id": 4,
        "user_id": 8,
        "token": "reset-hash",
        "expires_at": 4567.0,
        "used": True,
        "created_at": 4000.0,
    }


@pytest.mark.asyncio
async def test_mark_password_reset_token_used_returns_false_when_missing() -> None:
    connection = _FakeConnection(fetchrow_results=[None])
    store = AccountSecurityStore(_FakeBrain(connection))

    updated = await store.mark_password_reset_token_used("missing")

    assert connection.closed is True
    query, args = connection.fetchrow_calls[0]
    assert "UPDATE password_reset_tokens" in query
    assert "WHERE token = $1 AND used = FALSE" in query
    assert args == ("missing",)
    assert updated is False


@pytest.mark.asyncio
async def test_create_email_verification_token_invalidates_prior_unused_tokens() -> None:
    connection = _FakeConnection(fetchval_results=[21], execute_exceptions=[None])
    store = AccountSecurityStore(_FakeBrain(connection), now=lambda: 2000.0)

    token_id = await store.create_email_verification_token(
        user_id=9,
        token_hash="verify-hash",
        expires_at=3000.0,
    )

    assert connection.closed is True
    assert token_id == 21
    invalidate_query, invalidate_args = connection.execute_calls[0]
    assert "UPDATE email_verification_tokens SET used = TRUE" in invalidate_query
    assert invalidate_args == (9,)
    insert_query, insert_args = connection.fetchval_calls[0]
    assert "INSERT INTO email_verification_tokens" in insert_query
    assert insert_args == (9, "verify-hash", 3000.0, 2000.0)


@pytest.mark.asyncio
async def test_mark_user_email_verified_updates_flags_and_timestamp() -> None:
    connection = _FakeConnection(fetchrow_results=[(5,)])
    store = AccountSecurityStore(_FakeBrain(connection), now=lambda: 5555.0)

    updated = await store.mark_user_email_verified(5)

    assert connection.closed is True
    query, args = connection.fetchrow_calls[0]
    assert "UPDATE users" in query
    assert "email_verified = TRUE" in query
    assert "email_verified_at = COALESCE(email_verified_at, $1)" in query
    assert args == (5555.0, 5)
    assert updated is True


@pytest.mark.asyncio
async def test_get_and_mark_email_verification_token_round_trip_shape() -> None:
    connection = _FakeConnection(fetchrow_results=[(6, 14, "verify-hash", 7777.0, 0, 7000.0), (6,)])
    store = AccountSecurityStore(_FakeBrain(connection))

    record = await store.get_email_verification_token("verify-hash")
    marked = await store.mark_email_verification_token_used("verify-hash")

    assert connection.closed is True
    assert record == {
        "id": 6,
        "user_id": 14,
        "token_hash": "verify-hash",
        "expires_at": 7777.0,
        "used": False,
        "created_at": 7000.0,
    }
    update_query, update_args = connection.fetchrow_calls[1]
    assert "UPDATE email_verification_tokens" in update_query
    assert update_args == ("verify-hash",)
    assert marked is True


@pytest.mark.asyncio
async def test_create_account_deletion_token_ignores_invalidation_errors() -> None:
    connection = _FakeConnection(
        fetchval_results=[33],
        execute_exceptions=[RuntimeError("cannot invalidate old tokens")],
    )
    store = AccountSecurityStore(_FakeBrain(connection), now=lambda: 6000.0)

    token_id = await store.create_account_deletion_token(
        user_id=11,
        token_hash="delete-hash",
        expires_at=9000.0,
    )

    assert connection.closed is True
    assert token_id == 33
    invalidate_query, invalidate_args = connection.execute_calls[0]
    assert "UPDATE account_deletion_tokens SET used = TRUE" in invalidate_query
    assert invalidate_args == (11,)
    insert_query, insert_args = connection.fetchval_calls[0]
    assert "INSERT INTO account_deletion_tokens" in insert_query
    assert insert_args == (11, "delete-hash", 9000.0, 6000.0)


@pytest.mark.asyncio
async def test_get_and_mark_account_deletion_token_round_trip_shape() -> None:
    connection = _FakeConnection(fetchrow_results=[(10, 19, "delete-hash", 8800.0, 0, 8100.0), (10,)])
    store = AccountSecurityStore(_FakeBrain(connection))

    record = await store.get_account_deletion_token("delete-hash")
    marked = await store.mark_account_deletion_token_used("delete-hash")

    assert connection.closed is True
    assert record == {
        "id": 10,
        "user_id": 19,
        "token_hash": "delete-hash",
        "expires_at": 8800.0,
        "used": False,
        "created_at": 8100.0,
    }
    update_query, update_args = connection.fetchrow_calls[1]
    assert "UPDATE account_deletion_tokens" in update_query
    assert update_args == ("delete-hash",)
    assert marked is True


@pytest.mark.asyncio
async def test_palli_brain_account_security_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.password_reset_create_calls: list[dict[str, Any]] = []
            self.password_reset_get_calls: list[dict[str, Any]] = []
            self.password_reset_mark_calls: list[dict[str, Any]] = []
            self.email_verification_create_calls: list[dict[str, Any]] = []
            self.email_verification_get_calls: list[dict[str, Any]] = []
            self.email_verification_mark_calls: list[dict[str, Any]] = []
            self.user_email_verified_calls: list[dict[str, Any]] = []
            self.account_deletion_create_calls: list[dict[str, Any]] = []
            self.account_deletion_get_calls: list[dict[str, Any]] = []
            self.account_deletion_mark_calls: list[dict[str, Any]] = []

        async def create_password_reset_token(self, **kwargs: Any) -> int:
            self.password_reset_create_calls.append(kwargs)
            return 41

        async def get_password_reset_token(self, **kwargs: Any) -> dict[str, Any]:
            self.password_reset_get_calls.append(kwargs)
            return {"id": 1}

        async def mark_password_reset_token_used(self, **kwargs: Any) -> bool:
            self.password_reset_mark_calls.append(kwargs)
            return True

        async def create_email_verification_token(self, **kwargs: Any) -> int:
            self.email_verification_create_calls.append(kwargs)
            return 42

        async def get_email_verification_token(self, **kwargs: Any) -> dict[str, Any]:
            self.email_verification_get_calls.append(kwargs)
            return {"id": 2}

        async def mark_email_verification_token_used(self, **kwargs: Any) -> bool:
            self.email_verification_mark_calls.append(kwargs)
            return False

        async def mark_user_email_verified(self, **kwargs: Any) -> bool:
            self.user_email_verified_calls.append(kwargs)
            return True

        async def create_account_deletion_token(self, **kwargs: Any) -> int:
            self.account_deletion_create_calls.append(kwargs)
            return 43

        async def get_account_deletion_token(self, **kwargs: Any) -> dict[str, Any]:
            self.account_deletion_get_calls.append(kwargs)
            return {"id": 3}

        async def mark_account_deletion_token_used(self, **kwargs: Any) -> bool:
            self.account_deletion_mark_calls.append(kwargs)
            return True

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._account_security_store = stub

    password_reset_id = await brain.create_password_reset_token(user_id=7, token_hash="reset", expires_at=100.0)
    password_reset_record = await brain.get_password_reset_token("reset")
    password_reset_marked = await brain.mark_password_reset_token_used("reset")
    email_verification_id = await brain.create_email_verification_token(
        user_id=8,
        token_hash="verify",
        expires_at=200.0,
    )
    email_verification_record = await brain.get_email_verification_token("verify")
    email_verification_marked = await brain.mark_email_verification_token_used("verify")
    user_email_verified = await brain.mark_user_email_verified(8)
    account_deletion_id = await brain.create_account_deletion_token(
        user_id=9,
        token_hash="delete",
        expires_at=300.0,
    )
    account_deletion_record = await brain.get_account_deletion_token("delete")
    account_deletion_marked = await brain.mark_account_deletion_token_used("delete")

    assert password_reset_id == 41
    assert password_reset_record == {"id": 1}
    assert password_reset_marked is True
    assert email_verification_id == 42
    assert email_verification_record == {"id": 2}
    assert email_verification_marked is False
    assert user_email_verified is True
    assert account_deletion_id == 43
    assert account_deletion_record == {"id": 3}
    assert account_deletion_marked is True
    assert stub.password_reset_create_calls == [
        {"user_id": 7, "token_hash": "reset", "expires_at": 100.0}
    ]
    assert stub.password_reset_get_calls == [{"token_hash": "reset"}]
    assert stub.password_reset_mark_calls == [{"token_hash": "reset"}]
    assert stub.email_verification_create_calls == [
        {"user_id": 8, "token_hash": "verify", "expires_at": 200.0}
    ]
    assert stub.email_verification_get_calls == [{"token_hash": "verify"}]
    assert stub.email_verification_mark_calls == [{"token_hash": "verify"}]
    assert stub.user_email_verified_calls == [{"user_id": 8}]
    assert stub.account_deletion_create_calls == [
        {"user_id": 9, "token_hash": "delete", "expires_at": 300.0}
    ]
    assert stub.account_deletion_get_calls == [{"token_hash": "delete"}]
    assert stub.account_deletion_mark_calls == [{"token_hash": "delete"}]
