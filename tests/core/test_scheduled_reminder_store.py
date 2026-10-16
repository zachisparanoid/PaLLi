from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.scheduled_reminder_store import ScheduledReminderStore


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchrow_results: list[Any] | None = None,
        fetch_results: list[Any] | None = None,
    ) -> None:
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_results = list(fetch_results or [])
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

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


def _make_reminder_row() -> tuple[Any, ...]:
    row = (
        91,
        7,
        "discord",
        1234,
        "discord-dm-1234",
        "Check the draft",
        4567.0,
        4000.0,
        False,
        "followthrough",
        "job_91",
        "owner:7:surface:discord:dm:1234",
        json.dumps({"delivery_preference": "dm"}),
    )
    assert len(row) == 13
    return row


@pytest.mark.asyncio
async def test_get_scheduled_reminder_record_maps_record() -> None:
    connection = _FakeConnection(fetchrow_results=[_make_reminder_row()])
    store = ScheduledReminderStore(_FakeBrain(connection))

    result = await store.get_scheduled_reminder_record(91)

    assert connection.closed is True
    assert result == {
        "id": 91,
        "user_id": 7,
        "platform": "discord",
        "channel_id": 1234,
        "surface_id": "discord-dm-1234",
        "reminder_text": "Check the draft",
        "scheduled_time": 4567.0,
        "created_time": 4000.0,
        "is_completed": False,
        "kind": "followthrough",
        "runtime_job_id": "job_91",
        "runtime_session_key": "owner:7:surface:discord:dm:1234",
        "metadata": {"delivery_preference": "dm"},
    }


@pytest.mark.asyncio
async def test_list_user_scheduled_reminders_filters_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_reminder_row()]])
    store = ScheduledReminderStore(_FakeBrain(connection))

    result = await store.list_user_scheduled_reminders(user_id=7, platform="discord")

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "WHERE user_id = $1 AND platform = $2 AND is_completed = FALSE" in query
    assert "ORDER BY scheduled_time ASC" in query
    assert args == (7, "discord")
    assert result == [
        {
            "id": 91,
            "user_id": 7,
            "platform": "discord",
            "channel_id": 1234,
            "surface_id": "discord-dm-1234",
            "reminder_text": "Check the draft",
            "scheduled_time": 4567.0,
            "created_time": 4000.0,
            "is_completed": False,
            "kind": "followthrough",
            "runtime_job_id": "job_91",
            "runtime_session_key": "owner:7:surface:discord:dm:1234",
            "metadata": {"delivery_preference": "dm"},
        }
    ]


@pytest.mark.asyncio
async def test_list_open_scheduled_reminders_limits_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_reminder_row()]])
    store = ScheduledReminderStore(_FakeBrain(connection))

    result = await store.list_open_scheduled_reminders(limit=5)

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "ORDER BY scheduled_time ASC" in query
    assert args == (5,)
    assert result == [
        {
            "id": 91,
            "user_id": 7,
            "platform": "discord",
            "channel_id": 1234,
            "surface_id": "discord-dm-1234",
            "reminder_text": "Check the draft",
            "scheduled_time": 4567.0,
            "created_time": 4000.0,
            "is_completed": False,
            "kind": "followthrough",
            "runtime_job_id": "job_91",
            "runtime_session_key": "owner:7:surface:discord:dm:1234",
            "metadata": {"delivery_preference": "dm"},
        }
    ]


@pytest.mark.asyncio
async def test_list_due_scheduled_reminders_filters_by_due_time_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_reminder_row()]])
    store = ScheduledReminderStore(_FakeBrain(connection))

    result = await store.list_due_scheduled_reminders(as_of=5000.0)

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "scheduled_time <= $1" in query
    assert "ORDER BY scheduled_time ASC" in query
    assert "LIMIT" not in query
    assert args == (5000.0,)
    assert result == [
        {
            "id": 91,
            "user_id": 7,
            "platform": "discord",
            "channel_id": 1234,
            "surface_id": "discord-dm-1234",
            "reminder_text": "Check the draft",
            "scheduled_time": 4567.0,
            "created_time": 4000.0,
            "is_completed": False,
            "kind": "followthrough",
            "runtime_job_id": "job_91",
            "runtime_session_key": "owner:7:surface:discord:dm:1234",
            "metadata": {"delivery_preference": "dm"},
        }
    ]


@pytest.mark.asyncio
async def test_mark_scheduled_reminder_completed_updates_completion_flag() -> None:
    connection = _FakeConnection()
    store = ScheduledReminderStore(_FakeBrain(connection))

    await store.mark_scheduled_reminder_completed(reminder_id=91)

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "SET is_completed = TRUE" in query
    assert args == (91,)


@pytest.mark.asyncio
async def test_update_scheduled_reminder_schedule_updates_due_time() -> None:
    connection = _FakeConnection()
    store = ScheduledReminderStore(_FakeBrain(connection))

    await store.update_scheduled_reminder_schedule(reminder_id=91, scheduled_time=7777.0)

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "SET scheduled_time = $1" in query
    assert args == (7777.0, 91)


@pytest.mark.asyncio
async def test_update_scheduled_reminder_runtime_link_normalizes_blank_values() -> None:
    connection = _FakeConnection()
    store = ScheduledReminderStore(_FakeBrain(connection))

    await store.update_scheduled_reminder_runtime_link(
        reminder_id=91,
        runtime_job_id="  ",
        runtime_session_key=" owner:7:surface:local:chat ",
    )

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "runtime_job_id = COALESCE($2, runtime_job_id)" in query
    assert args == (91, None, "owner:7:surface:local:chat")


@pytest.mark.asyncio
async def test_palli_brain_scheduled_reminder_methods_delegate_to_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any] | int]] = []

        async def get_scheduled_reminder_record(self, reminder_id: int) -> dict[str, Any]:
            self.calls.append(("get", reminder_id))
            return {"id": reminder_id, "runtime_job_id": "job_stub"}

        async def list_user_scheduled_reminders(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(("list_user", kwargs))
            return [{"id": 90}]

        async def list_open_scheduled_reminders(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(("list", kwargs))
            return [{"id": 91}]

        async def list_due_scheduled_reminders(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(("list_due", kwargs))
            return [{"id": 92}]

        async def mark_scheduled_reminder_completed(self, **kwargs: Any) -> None:
            self.calls.append(("complete", kwargs))

        async def update_scheduled_reminder_schedule(self, **kwargs: Any) -> None:
            self.calls.append(("schedule", kwargs))

        async def update_scheduled_reminder_runtime_link(self, **kwargs: Any) -> None:
            self.calls.append(("update", kwargs))

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._scheduled_reminder_store = stub

    reminder = await brain.get_scheduled_reminder_record(91)
    user_reminders = await brain.list_user_scheduled_reminders(user_id=7, platform="discord")
    reminders = await brain.list_open_scheduled_reminders(limit=3)
    due_reminders = await brain.list_due_scheduled_reminders(as_of=5000.0)
    await brain.mark_scheduled_reminder_completed(reminder_id=92)
    await brain.update_scheduled_reminder_schedule(reminder_id=91, scheduled_time=7777.0)
    await brain.update_scheduled_reminder_runtime_link(
        reminder_id=91,
        runtime_job_id="job_91",
        runtime_session_key="owner:7:surface:local:chat",
    )

    assert reminder == {"id": 91, "runtime_job_id": "job_stub"}
    assert user_reminders == [{"id": 90}]
    assert reminders == [{"id": 91}]
    assert due_reminders == [{"id": 92}]
    assert stub.calls == [
        ("get", 91),
        ("list_user", {"user_id": 7, "platform": "discord"}),
        ("list", {"limit": 3}),
        ("list_due", {"as_of": 5000.0, "limit": None}),
        ("complete", {"reminder_id": 92}),
        ("schedule", {"reminder_id": 91, "scheduled_time": 7777.0}),
        (
            "update",
            {
                "reminder_id": 91,
                "runtime_job_id": "job_91",
                "runtime_session_key": "owner:7:surface:local:chat",
            },
        ),
    ]
