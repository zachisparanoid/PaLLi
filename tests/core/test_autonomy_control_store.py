from __future__ import annotations

import json
from typing import Any

import pytest

from core.autonomy_control_store import AutonomyControlStore
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
        self.close_count = 0

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
        self.close_count += 1


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


def _make_heartbeat_row() -> tuple[Any, ...]:
    row = (
        "hbr_1",
        7,
        "job_1",
        "scheduled",
        "completed",
        "standing_order",
        "review_due",
        "Sent a followthrough prompt",
        "chat",
        json.dumps(["act_1", "act_2"]),
        json.dumps({"events": 2}),
        2000.0,
        2010.0,
        1999.0,
        2011.0,
    )
    assert len(row) == 15
    return row


def _make_standing_order_row() -> tuple[Any, ...]:
    row = (
        "so_1",
        7,
        "Weekday review",
        "Check the repo every weekday morning.",
        "operator_program",
        "cron",
        json.dumps({"weekday": "mon-fri", "hour": 9}),
        "active",
        "ready",
        "message",
        "gentle",
        "/repo/current",
        3000.0,
        2500.0,
        "Last review completed",
        json.dumps({"source": "owner"}),
        2000.0,
        3100.0,
    )
    assert len(row) == 18
    return row


class _FakeStatus:
    """Stand-in for ThreadStatus enum-like with .value attribute."""

    def __init__(self, value: str) -> None:
        self.value = value


class _FakeOpenThread:
    """Lightweight stand-in for OpenThread in store-level tests.

    Updated for C8: now exposes the full attribute set the store renders
    in _standing_order_from_open_thread (status with .value, updated_at,
    resolved_at, resolution_reason). Pre-existing C1.5 callers don't
    touch the new fields so they fall back to defaults.
    """

    def __init__(
        self,
        *,
        id: str,
        owner_user_id: int,
        topic_summary: str,
        source_id: str | None,
        source_payload: dict[str, Any] | None,
        created_at: float,
        last_surfaced_at: float | None,
        # C8 additions — defaults preserve C1.5 test compatibility.
        status: str = "logged",
        updated_at: float | None = None,
        resolved_at: float | None = None,
        resolution_reason: str | None = None,
        source_kind: str = "proactive_record",
    ) -> None:
        self.id = id
        self.owner_user_id = owner_user_id
        self.topic_summary = topic_summary
        self.source_id = source_id
        self.source_payload = source_payload
        self.created_at = created_at
        self.last_surfaced_at = last_surfaced_at
        self.status = _FakeStatus(status)
        self.updated_at = float(updated_at) if updated_at is not None else float(created_at)
        self.resolved_at = resolved_at
        self.resolution_reason = resolution_reason
        self.source_kind = _FakeStatus(source_kind)


class _FakeOpenThreadsBrain:
    """Brain stand-in routing absorbed-table writes through open_threads.

    Captures add_source_open_thread / list_open_threads_by_source_kind
    calls and returns deterministic synthetic responses so store-level
    tests can assert against the new code path without standing up a
    sqlite connection.

    C8 (B5 RECURRING absorb): added get_open_thread_by_source_id,
    update_source_open_thread, list_due_open_threads_by_source_kind,
    and get_recurring_open_thread_unscoped helpers.
    """

    def __init__(self, return_threads: list[_FakeOpenThread] | None = None) -> None:
        self.add_source_calls: list[dict[str, Any]] = []
        self.list_by_source_calls: list[dict[str, Any]] = []
        # C8 additions:
        self.get_by_source_id_calls: list[dict[str, Any]] = []
        self.unscoped_get_calls: list[dict[str, Any]] = []
        self.update_source_calls: list[dict[str, Any]] = []
        self.list_due_calls: list[dict[str, Any]] = []
        self._return_threads = list(return_threads or [])
        self._next_thread_id = "0123456789abcdef" * 2  # 32 hex chars
        # Single source-keyed thread the get_by_source_id and
        # unscoped get returns (None until set).
        self.return_get_thread: _FakeOpenThread | None = None

    async def add_source_open_thread(self, **kwargs: Any) -> str:
        self.add_source_calls.append(kwargs)
        return self._next_thread_id

    async def list_open_threads_by_source_kind(self, **kwargs: Any) -> list[_FakeOpenThread]:
        self.list_by_source_calls.append(kwargs)
        return list(self._return_threads)

    async def get_open_thread_by_source_id(self, **kwargs: Any) -> _FakeOpenThread | None:
        self.get_by_source_id_calls.append(kwargs)
        return self.return_get_thread

    async def get_open_thread_by_source_id_unscoped(self, **kwargs: Any) -> _FakeOpenThread | None:
        self.unscoped_get_calls.append(kwargs)
        return self.return_get_thread

    async def get_recurring_open_thread_unscoped(self, *, source_id: str) -> _FakeOpenThread | None:
        return await self.get_open_thread_by_source_id_unscoped(
            source_kind="recurring", source_id=source_id
        )

    async def update_source_open_thread(self, **kwargs: Any) -> bool:
        self.update_source_calls.append(kwargs)
        return self.return_get_thread is not None

    async def list_due_open_threads_by_source_kind(
        self, **kwargs: Any
    ) -> list[_FakeOpenThread]:
        self.list_due_calls.append(kwargs)
        return list(self._return_threads)


@pytest.mark.asyncio
async def test_create_proactive_action_routes_through_open_threads_with_b5_shape() -> None:
    """C1.5: writer must INSERT to open_threads with source_kind='proactive_record'."""
    brain = _FakeOpenThreadsBrain()
    store = AutonomyControlStore(brain, now=lambda: 1000.0)

    action_id = await store.create_proactive_action(
        user_id=7,
        action_type=" proactive_message ",
        content="hey",
        trigger_reason='{"candidate_tick_timestamp": "1810.000000"}',
        scheduled_time=1800.0,
        executed_time=1810.0,
        success=True,
        user_response="thanks",
        effectiveness_score=0.8,
        runtime_job_id=" job_1 ",
        runtime_session_key=" owner:7:surface:maintenance ",
    )
    # Returned id is derived from the (text) thread_id; non-zero proves
    # the write went through.
    assert action_id != 0
    assert len(brain.add_source_calls) == 1
    call = brain.add_source_calls[0]
    assert call["owner_user_id"] == 7
    assert call["topic_summary"] == "hey"
    assert call["source_kind"] == "proactive_record"
    assert call["status"] == "logged"
    assert call["kind"] == "other"
    assert call["urgency"] == "historical"
    assert call["created_at"] == 1800.0
    assert call["updated_at"] == 1810.0
    assert call["last_surfaced_at"] == 1810.0
    assert call["resolved_at"] == 1810.0
    assert call["resolution_reason"] == "executed"
    assert call["surface_count"] == 1
    assert "Proactive outreach record" in call["why_it_matters"]
    payload = call["source_payload"]
    assert payload["runtime_job_id"] == "job_1"
    assert payload["runtime_session_key"] == "owner:7:surface:maintenance"
    assert payload["action_type"] == "proactive_message"
    # JSON trigger_reason was decoded so the rumination guard can read
    # source_payload['original_trigger_reason']['candidate_tick_timestamp']
    # as a dict.
    assert payload["original_trigger_reason"]["candidate_tick_timestamp"] == "1810.000000"
    assert payload["success"] is True
    assert payload["user_response"] == "thanks"
    assert payload["effectiveness_score"] == 0.8


@pytest.mark.asyncio
async def test_create_proactive_action_marks_failed_for_success_false() -> None:
    brain = _FakeOpenThreadsBrain()
    store = AutonomyControlStore(brain, now=lambda: 1000.0)
    await store.create_proactive_action(
        user_id=1,
        action_type="proactive_message",
        content="msg",
        scheduled_time=1000.0,
        executed_time=1010.0,
        success=False,
    )
    call = brain.add_source_calls[0]
    assert call["resolution_reason"] == "failed"


@pytest.mark.asyncio
async def test_create_proactive_action_unexecuted_has_no_resolution() -> None:
    """Scheduled but not yet fired → no resolved_at, no resolution_reason."""
    brain = _FakeOpenThreadsBrain()
    store = AutonomyControlStore(brain, now=lambda: 1000.0)
    await store.create_proactive_action(
        user_id=1,
        action_type="proactive_message",
        content="msg",
        scheduled_time=2000.0,
        executed_time=None,
        success=None,
    )
    call = brain.add_source_calls[0]
    assert call["created_at"] == 2000.0
    assert call["updated_at"] == 2000.0
    assert call["last_surfaced_at"] is None
    assert call["resolved_at"] is None
    assert call["resolution_reason"] is None
    assert call["surface_count"] == 0


@pytest.mark.asyncio
async def test_create_proactive_action_preserves_non_json_trigger_reason() -> None:
    brain = _FakeOpenThreadsBrain()
    store = AutonomyControlStore(brain, now=lambda: 1000.0)
    await store.create_proactive_action(
        user_id=1,
        action_type="proactive_message",
        content="msg",
        trigger_reason="user_idle_for_3h",
    )
    call = brain.add_source_calls[0]
    assert call["source_payload"]["original_trigger_reason"] == "user_idle_for_3h"


@pytest.mark.asyncio
async def test_list_recent_proactive_actions_routes_through_open_threads() -> None:
    """Reader returns the legacy dict shape; data sourced from open_threads."""
    fake_threads = [
        _FakeOpenThread(
            id="thread_text_id_xyz",
            owner_user_id=7,
            topic_summary="hey",
            source_id="42",
            source_payload={
                "runtime_job_id": "job_1",
                "runtime_session_key": "owner:7:surface:maintenance",
                "action_type": "proactive_message",
                "original_trigger_reason": "review_due",
                "success": True,
                "user_response": "thanks",
                "effectiveness_score": 0.8,
            },
            created_at=1800.0,
            last_surfaced_at=1810.0,
        ),
    ]
    brain = _FakeOpenThreadsBrain(return_threads=fake_threads)
    store = AutonomyControlStore(brain, now=lambda: 1000.0)

    actions = await store.list_recent_proactive_actions_for_user(7, limit=4)

    # Brain wrapper called with B5 source_kind + limit.
    assert brain.list_by_source_calls == [
        {
            "owner_user_id": 7,
            "source_kind": "proactive_record",
            "limit": 4,
        }
    ]
    # Legacy dict shape preserved (rumination guard, daily-cap, and
    # bounded-reflection readers are unchanged).
    assert actions == [
        {
            "id": 42,
            "user_id": 7,
            "runtime_job_id": "job_1",
            "runtime_session_key": "owner:7:surface:maintenance",
            "action_type": "proactive_message",
            "trigger_reason": "review_due",
            "content": "hey",
            "scheduled_time": 1800.0,
            "executed_time": 1810.0,
            "success": True,
            "user_response": "thanks",
            "effectiveness_score": 0.8,
        }
    ]


@pytest.mark.asyncio
async def test_list_recent_proactive_actions_falls_back_to_thread_id_hash() -> None:
    """Non-numeric source_id (post-C1.5 runtime row): id derived from the
    text PK so ``int(row['id'])`` dedup keys stay stable and unique."""
    fake_threads = [
        _FakeOpenThread(
            id="0123456789abcdeffedcba9876543210",  # 32 hex chars
            owner_user_id=7,
            topic_summary="x",
            source_id=None,  # runtime row — brain doesn't surface text PK
            source_payload={"action_type": "proactive_message"},
            created_at=100.0,
            last_surfaced_at=110.0,
        ),
    ]
    brain = _FakeOpenThreadsBrain(return_threads=fake_threads)
    store = AutonomyControlStore(brain)
    actions = await store.list_recent_proactive_actions_for_user(7, limit=4)
    # Hash of first 15 hex chars of the text PK — non-zero so dedup
    # collapses identical rows but distinct PKs survive.
    assert actions[0]["id"] != 0
    assert actions[0]["id"] == int("0123456789abcde", 16)


@pytest.mark.asyncio
async def test_list_recent_proactive_actions_uses_source_id_when_numeric() -> None:
    """Migrated legacy rows: source_id is the text-rendered int PK;
    use it directly so the legacy id round-trips."""
    fake_threads = [
        _FakeOpenThread(
            id="0123456789abcdeffedcba9876543210",
            owner_user_id=7,
            topic_summary="x",
            source_id="42",
            source_payload={"action_type": "proactive_message"},
            created_at=100.0,
            last_surfaced_at=110.0,
        ),
    ]
    brain = _FakeOpenThreadsBrain(return_threads=fake_threads)
    store = AutonomyControlStore(brain)
    actions = await store.list_recent_proactive_actions_for_user(7, limit=4)
    assert actions[0]["id"] == 42


@pytest.mark.asyncio
async def test_palli_brain_autonomy_control_store_delegates_calls(tmp_path) -> None:
    """Great Excision (2026-07-09): the store keeps only the LIVE
    proactive-record pair; heartbeat/standing-order delegation went with
    the operator engine."""

    class _StubStore:
        def __init__(self) -> None:
            self.create_proactive_action_calls: list[dict[str, Any]] = []
            self.list_recent_proactive_actions_calls: list[tuple[int, int]] = []

        async def create_proactive_action(self, **kwargs: Any) -> int:
            self.create_proactive_action_calls.append(kwargs)
            return 41

        async def list_recent_proactive_actions_for_user(self, user_id: int, *, limit: int = 5) -> list[dict[str, Any]]:
            self.list_recent_proactive_actions_calls.append((user_id, limit))
            return [{"id": 41}]

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._autonomy_control_store = stub

    created = await brain.create_proactive_action(
        user_id=7, action_type="message", trigger_reason="test", content="hi"
    )
    listed = await brain.list_recent_proactive_actions_for_user(7, limit=3)

    assert created == 41
    assert listed == [{"id": 41}]
    assert stub.create_proactive_action_calls and stub.create_proactive_action_calls[0]["user_id"] == 7
    assert stub.list_recent_proactive_actions_calls == [(7, 3)]
