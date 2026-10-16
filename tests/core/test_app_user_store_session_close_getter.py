"""Phase B / B4 step 3 — `get_user_last_session_closed_at` brain wrapper.

Background — audit finding IM-HIGH-2:
The session-close hook in ``core/turn/reflect.py`` previously stored a
``last_session_closed_at`` row in ``user_memory`` (a generic decay-ranked
fact store), polluting recall budgets with bookkeeping data. B4 step 1
shipped the new home — column ``users.last_session_closed_at`` plus a
migration script. Step 2 redirected the writer. Step 3 (this file's
tests) closes the read side: a brain getter symmetrical to step 2's
setter, so any future reader of ``last_session_closed_at`` routes
through the dedicated column and not the generic ``user_memory`` store.

Note on existing readers: a survey of the codebase at the time step 3
landed found NO production readers of ``user_memory`` rows with
``content LIKE 'last_session_closed_at:%'``. The function the plan
called out — ``core/turn/recall.py:_safe_count_monologue_ticks_since_last_user``
— reads from ``recent_history`` (conversation history rows), not from
the session-close marker. The plan's framing ("redirect the reader")
was inaccurate; there was no reader to redirect. Step 3 therefore
ships the getter wrapper without any production call site change, so
the read path is fully owned by the dedicated column when a future
consumer wires it up.

The brain wrapper:
  * ``PalliBrain.get_user_last_session_closed_at(*, owner_user_id) -> Optional[float]``
    delegates to ``_app_user_store.get_user_last_session_closed_at``.
  * The store method runs ``SELECT last_session_closed_at FROM users WHERE id = ?``.
  * Returns ``None`` for: user does not exist OR column is NULL (which is
    the case for any user whose runtime hasn't yet recorded a session
    close, OR whose legacy ``user_memory`` rows haven't been migrated
    via ``scripts/migrate_last_session_closed_at.py --apply``).

These tests mirror the patterns in ``test_app_user_store_session_close.py``.

Spec-review safety net (B2.2 lesson, commit cd30ecd): the live
``brain`` singleton MUST expose the wrapper. ``MagicMock(spec=...)`` on
Python 3.14 still allows arbitrary getattr — so we add a ``hasattr``
smoke check against the live singleton AND a ``create_autospec`` test
for the wrapper-lookup contract.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import create_autospec

import pytest

from core.app_user_store import AppUserStore
from core.database import PalliBrain, brain as live_brain


# ─── shared fakes (mirroring test_app_user_store_session_close.py) ──────


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchval_results: list[Any] | None = None,
        fetchval_exceptions: list[Exception | None] | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetchval_exceptions = list(fetchval_exceptions or [])
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.fetchval_calls.append((query, args))
        if self.fetchval_exceptions:
            exc = self.fetchval_exceptions.pop(0)
            if exc is not None:
                raise exc
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
        return None

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


# ─── store-level: SQL + args + connection lifecycle ─────────────────────


@pytest.mark.asyncio
async def test_get_user_last_session_closed_at_returns_column_value() -> None:
    """The store method returns the column value when present."""
    connection = _FakeConnection(fetchval_results=[1778197025.0])
    store = AppUserStore(_FakeBrain(connection))

    result = await store.get_user_last_session_closed_at(owner_user_id=42)

    assert connection.closed is True
    assert len(connection.fetchval_calls) == 1
    query, args = connection.fetchval_calls[0]
    assert "SELECT last_session_closed_at" in query
    assert "FROM users" in query
    assert "WHERE id" in query
    # owner_user_id coerced to int for the bind.
    assert args == (42,)
    # Result is the numeric column value, as a float.
    assert result == 1778197025.0
    assert isinstance(result, float)


@pytest.mark.asyncio
async def test_get_user_last_session_closed_at_returns_none_for_missing_user() -> None:
    """When the user row doesn't exist, fetchval returns None and the
    method passes that through."""
    connection = _FakeConnection(fetchval_results=[None])
    store = AppUserStore(_FakeBrain(connection))

    result = await store.get_user_last_session_closed_at(owner_user_id=999)

    assert connection.closed is True
    assert result is None


@pytest.mark.asyncio
async def test_get_user_last_session_closed_at_returns_none_when_column_null() -> None:
    """When the user row exists but the column is NULL (e.g., the
    operator has not yet run ``--apply`` on the migration, OR the user
    has never had a session close recorded), fetchval returns None.
    The method must surface that as None — no fallback to user_memory,
    no implicit default."""
    connection = _FakeConnection(fetchval_results=[None])
    store = AppUserStore(_FakeBrain(connection))

    result = await store.get_user_last_session_closed_at(owner_user_id=42)

    assert result is None


@pytest.mark.asyncio
async def test_get_user_last_session_closed_at_closes_connection_even_on_exception() -> None:
    """If the SELECT raises, the connection still closes (no leak)."""
    connection = _FakeConnection(fetchval_exceptions=[RuntimeError("transient")])
    store = AppUserStore(_FakeBrain(connection))

    with pytest.raises(RuntimeError, match="transient"):
        await store.get_user_last_session_closed_at(owner_user_id=42)

    assert connection.closed is True


@pytest.mark.asyncio
async def test_get_user_last_session_closed_at_coerces_owner_id_to_int() -> None:
    """Defensive coercion: int-ish owner_user_id (e.g., a numeric str)
    is bound as int, mirroring the setter convention."""
    connection = _FakeConnection(fetchval_results=[7000.0])
    store = AppUserStore(_FakeBrain(connection))

    await store.get_user_last_session_closed_at(
        owner_user_id="42",  # type: ignore[arg-type]
    )

    _, args = connection.fetchval_calls[0]
    assert args == (42,)
    assert isinstance(args[0], int)


@pytest.mark.asyncio
async def test_get_user_last_session_closed_at_coerces_numeric_result_to_float() -> None:
    """If the underlying driver returns an int (some sqlite3 paths do),
    the method normalizes to float so callers see a consistent type."""
    connection = _FakeConnection(fetchval_results=[1778197025])  # int, not float
    store = AppUserStore(_FakeBrain(connection))

    result = await store.get_user_last_session_closed_at(owner_user_id=42)

    assert result == 1778197025.0
    assert isinstance(result, float)


# ─── PalliBrain wrapper plumbing ────────────────────────────────────────


def test_brain_exposes_get_user_last_session_closed_at_wrapper() -> None:
    """B2.2 lesson: the live ``brain`` singleton MUST expose the wrapper.

    Without this assertion, a ``MagicMock(spec=PalliBrain)`` test would
    silently pass while production breaks (the lesson learned in
    B2.2 / commit cd30ecd).
    """
    assert hasattr(live_brain, "get_user_last_session_closed_at"), (
        "PalliBrain must expose get_user_last_session_closed_at — added "
        "to AppUserStore in B4 step 3 to close the read side of the "
        "session-close redirect (writer was step 2, column was step 1). "
        "Add the wrapper alongside other AppUserStore wrappers in "
        "core/database.py."
    )


@pytest.mark.asyncio
async def test_palli_brain_get_user_last_session_closed_at_delegates_to_store(
    tmp_path,
) -> None:
    """The PalliBrain method delegates to
    ``_app_user_store.get_user_last_session_closed_at`` with kwargs forwarded
    as-is, and the store's return value is propagated back unchanged."""

    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def get_user_last_session_closed_at(self, **kwargs: Any) -> float | None:
            self.calls.append(kwargs)
            return 1778197025.0

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._app_user_store = stub  # type: ignore[assignment]

    result = await brain.get_user_last_session_closed_at(owner_user_id=42)

    assert stub.calls == [{"owner_user_id": 42}]
    assert result == 1778197025.0


@pytest.mark.asyncio
async def test_palli_brain_get_user_last_session_closed_at_propagates_none(
    tmp_path,
) -> None:
    """When the underlying store returns None (missing user OR NULL
    column), the brain wrapper must propagate that None unchanged. Do
    NOT silently substitute a default — callers rely on None to detect
    "no session close recorded yet" cleanly."""

    class _StubStore:
        async def get_user_last_session_closed_at(self, **kwargs: Any) -> float | None:
            return None

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    brain._app_user_store = _StubStore()  # type: ignore[assignment]

    result = await brain.get_user_last_session_closed_at(owner_user_id=42)

    assert result is None


def test_autospec_brain_exposes_get_user_last_session_closed_at() -> None:
    """``create_autospec(PalliBrain, instance=True)`` raises AttributeError
    on missing methods; this test pins the contract for any future test
    that mocks the brain via autospec.

    Without this test, a refactor that accidentally drops the wrapper
    would only fail downstream — and downstream tests might be written
    with looser ``MagicMock`` and miss the regression entirely.
    """
    autospec_brain = create_autospec(PalliBrain, instance=True)
    # Accessing the method does not raise — autospec auto-mocks it.
    assert autospec_brain.get_user_last_session_closed_at is not None
