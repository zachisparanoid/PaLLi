"""Phase B / B4 step 2 — `set_user_last_session_closed_at` brain wrapper.

Background — audit finding IM-HIGH-2:
The session-close hook in ``core/turn/reflect.py`` previously stored a
``last_session_closed_at`` row in ``user_memory`` (a generic decay-ranked
fact store), polluting recall budgets with bookkeeping data. B4 step 1
shipped the new home — column ``users.last_session_closed_at`` plus a
migration script. Step 2 (this file's tests) redirects the writer.

The brain wrapper:
  * ``PalliBrain.set_user_last_session_closed_at(owner_user_id, timestamp)``
    delegates to ``_app_user_store.set_user_last_session_closed_at``.
  * The store method runs ``UPDATE users SET last_session_closed_at = ?,
    updated_at = ? WHERE id = ?``.

These tests are at integration-flavor depth: a fake connection captures
the SQL + args, plus a `_FakeBrain` shim. Mirrors the patterns already in
``tests/core/test_app_user_store.py``.

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


# ─── shared fakes (mirroring tests/core/test_app_user_store.py) ─────────


class _FakeConnection:
    def __init__(
        self,
        *,
        execute_results: list[Any] | None = None,
        execute_exceptions: list[Exception | None] | None = None,
    ) -> None:
        self.execute_results = list(execute_results or [])
        self.execute_exceptions = list(execute_exceptions or [])
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def execute(self, query: str, *args: Any) -> Any:
        self.execute_calls.append((query, args))
        if self.execute_exceptions:
            exc = self.execute_exceptions.pop(0)
            if exc is not None:
                raise exc
        if self.execute_results:
            return self.execute_results.pop(0)
        return "UPDATE 1"

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
async def test_set_user_last_session_closed_at_writes_column_and_stamps_updated_at() -> None:
    """The store method writes ``users.last_session_closed_at`` for the
    given owner and stamps ``updated_at`` with the now-injection.
    """
    connection = _FakeConnection()
    store = AppUserStore(_FakeBrain(connection), now=lambda: 9999.0)

    await store.set_user_last_session_closed_at(
        owner_user_id=42, timestamp=1778197025.0,
    )

    assert connection.closed is True
    assert len(connection.execute_calls) == 1
    query, args = connection.execute_calls[0]
    assert "UPDATE users" in query
    assert "last_session_closed_at" in query
    assert "WHERE id" in query
    # Arg order: timestamp, updated_at, user_id
    assert args == (1778197025.0, 9999.0, 42)


@pytest.mark.asyncio
async def test_set_user_last_session_closed_at_closes_connection_even_on_exception() -> None:
    """If the UPDATE raises, the connection still closes (no leak)."""
    connection = _FakeConnection(execute_exceptions=[RuntimeError("transient")])
    store = AppUserStore(_FakeBrain(connection), now=lambda: 9999.0)

    with pytest.raises(RuntimeError, match="transient"):
        await store.set_user_last_session_closed_at(
            owner_user_id=42, timestamp=1778197025.0,
        )

    assert connection.closed is True


@pytest.mark.asyncio
async def test_set_user_last_session_closed_at_coerces_inputs_to_int_and_float() -> None:
    """Defensive coercion: int-ish owner_user_id → int, numeric timestamp → float."""
    connection = _FakeConnection()
    store = AppUserStore(_FakeBrain(connection), now=lambda: 1000.0)

    # Pass strings that cleanly coerce; tolerant of typing slop at call sites.
    await store.set_user_last_session_closed_at(
        owner_user_id="42", timestamp="1778197025",  # type: ignore[arg-type]
    )

    _, args = connection.execute_calls[0]
    assert args[0] == 1778197025.0
    assert isinstance(args[0], float)
    assert args[2] == 42
    assert isinstance(args[2], int)


# ─── PalliBrain wrapper plumbing ────────────────────────────────────────


def test_brain_exposes_set_user_last_session_closed_at_wrapper() -> None:
    """B2.2 lesson: the live ``brain`` singleton MUST expose the wrapper.

    The B4-step-1 commit added the column + migration script; this step
    (B4 step 2) redirects the writer to land in that column. The
    redirect goes through ``brain.set_user_last_session_closed_at``,
    so the wrapper has to be on PalliBrain — not just on the underlying
    store. Without this assertion, a ``MagicMock(spec=PalliBrain)`` test
    would silently pass while production breaks (the lesson learned in
    B2.2 / commit cd30ecd).
    """
    assert hasattr(live_brain, "set_user_last_session_closed_at"), (
        "PalliBrain must expose set_user_last_session_closed_at — added "
        "to AppUserStore in B4 step 2 to redirect the session-close "
        "writer out of user_memory and into users.last_session_closed_at. "
        "Add the wrapper alongside other AppUserStore wrappers in "
        "core/database.py."
    )


@pytest.mark.asyncio
async def test_palli_brain_set_user_last_session_closed_at_delegates_to_store(tmp_path) -> None:
    """The PalliBrain method delegates to ``_app_user_store.set_user_last_session_closed_at``
    with the kwargs forwarded as-is."""

    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def set_user_last_session_closed_at(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._app_user_store = stub  # type: ignore[assignment]

    await brain.set_user_last_session_closed_at(
        owner_user_id=42, timestamp=1778197025.0,
    )

    assert stub.calls == [
        {"owner_user_id": 42, "timestamp": 1778197025.0},
    ]


def test_autospec_brain_exposes_set_user_last_session_closed_at() -> None:
    """``create_autospec(PalliBrain, instance=True)`` raises AttributeError
    on missing methods; this test pins the contract for any future test
    that mocks the brain via autospec.

    Without this test, a refactor that accidentally drops the wrapper
    would only fail downstream — and downstream tests (the reflect
    redirect ones) might be written with looser ``MagicMock`` and miss
    the regression entirely.
    """
    autospec_brain = create_autospec(PalliBrain, instance=True)
    # Accessing the method does not raise — autospec auto-mocks it.
    assert autospec_brain.set_user_last_session_closed_at is not None
