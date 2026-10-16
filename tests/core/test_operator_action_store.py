from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.operator_action_store import OperatorActionStore


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

    @staticmethod
    def _placeholders(count: int, start: int = 1) -> str:
        return ",".join(f"${index}" for index in range(start, start + count))


def _make_action_row() -> tuple[Any, ...]:
    row = (
        "act_1",
        7,
        "job_1",
        "run_1",
        "session:owner:local",
        "root:local:123",
        "wf_1",
        "step_1",
        "cp_1",
        "resume",
        "/repo/current",
        "Current Repo",
        "origin",
        "palli/workspace",
        "chat",
        "repo_fix",
        "completed",
        "Ship the patch",
        "Need a local fix",
        "Patched the runtime issue",
        "Owner approved the fix",
        "act_0",
        "act_2",
        json.dumps({"surface": "mission_control"}),
        2000.0,
        2100.0,
        2200.0,
    )
    assert len(row) == 27
    return row


@pytest.mark.asyncio
async def test_create_operator_action_uses_normalized_args_and_now() -> None:
    connection = _FakeConnection()
    store = OperatorActionStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.create_operator_action(
        action_id=" act_1 ",
        owner_user_id=7,
        runtime_job_id=" job_1 ",
        agent_run_id=None,
        source_runtime_session_key=" session:owner:local ",
        root_session_key=" root:local:123 ",
        workflow_id=" wf_1 ",
        workflow_step_id=None,
        resumed_from_checkpoint_id=" cp_1 ",
        resume_launch_mode=" resume ",
        workspace_root=" /repo/current ",
        workspace_label=" Current Repo ",
        target_remote_name=" origin ",
        managed_branch_name=None,
        trigger_source=" chat ",
        intent_kind=" repo_fix ",
        status=" planned ",
        objective="Ship the patch",
        decision_reason=" Need a local fix ",
        metadata={"surface": "mission_control"},
    )

    assert connection.closed is True
    _, args = connection.execute_calls[0]
    assert args[0] == "act_1"
    assert args[2] == "job_1"
    assert args[3] is None
    assert args[10] == "/repo/current"
    assert args[11] == "Current Repo"
    assert args[16] == "planned"
    assert args[17] == "Ship the patch"
    assert json.loads(args[23]) == {"surface": "mission_control"}
    assert args[24] == 2500.0
    assert args[25] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current_completed_at", "status", "expected_completed_at"),
    [
        (None, "completed", 2500.0),
        (1111.0, "running", None),
    ],
)
async def test_update_operator_action_merges_metadata_and_manages_completed_at(
    current_completed_at: float | None,
    status: str,
    expected_completed_at: float | None,
) -> None:
    connection = _FakeConnection(fetchrow_results=[('{"existing": true}', current_completed_at, "act_prev", "act_next")])
    store = OperatorActionStore(_FakeBrain(connection), now=lambda: 2500.0)

    await store.update_operator_action(
        action_id="act_1",
        status=status,
        metadata={"added": "value"},
    )

    assert connection.closed is True
    _, args = connection.execute_calls[0]
    assert args[20] == "act_prev"
    assert args[21] == "act_next"
    assert json.loads(args[22]) == {"existing": True, "added": "value"}
    assert args[23] == 2500.0
    assert args[24] == expected_completed_at


@pytest.mark.asyncio
async def test_get_operator_action_maps_record() -> None:
    connection = _FakeConnection(fetchrow_results=[_make_action_row()])
    store = OperatorActionStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.get_operator_action(action_id="act_1")

    assert connection.closed is True
    assert result == {
        "action_id": "act_1",
        "owner_user_id": 7,
        "runtime_job_id": "job_1",
        "agent_run_id": "run_1",
        "source_runtime_session_key": "session:owner:local",
        "root_session_key": "root:local:123",
        "workflow_id": "wf_1",
        "workflow_step_id": "step_1",
        "resumed_from_checkpoint_id": "cp_1",
        "resume_launch_mode": "resume",
        "workspace_root": "/repo/current",
        "workspace_label": "Current Repo",
        "target_remote_name": "origin",
        "managed_branch_name": "palli/workspace",
        "trigger_source": "chat",
        "intent_kind": "repo_fix",
        "status": "completed",
        "objective": "Ship the patch",
        "decision_reason": "Need a local fix",
        "result_summary": "Patched the runtime issue",
        "last_transition_reason": "Owner approved the fix",
        "retry_of_action_id": "act_0",
        "replacement_action_id": "act_2",
        "metadata": {"surface": "mission_control"},
        "created_at": 2000.0,
        "updated_at": 2100.0,
        "completed_at": 2200.0,
    }


@pytest.mark.asyncio
async def test_list_operator_actions_filters_and_maps_records() -> None:
    connection = _FakeConnection(fetch_results=[[_make_action_row()]])
    store = OperatorActionStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.list_operator_actions(
        owner_user_id=7,
        statuses=["completed", "failed"],
        workflow_id="wf_1",
        workspace_root="/repo/current",
        limit=5,
    )

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "status IN ($2,$3)" in query
    assert "workflow_id = $4" in query
    assert "workspace_root = $5" in query
    assert args == (7, "completed", "failed", "wf_1", "/repo/current", 5)
    assert result == [
        {
            "action_id": "act_1",
            "owner_user_id": 7,
            "runtime_job_id": "job_1",
            "agent_run_id": "run_1",
            "source_runtime_session_key": "session:owner:local",
            "root_session_key": "root:local:123",
            "workflow_id": "wf_1",
            "workflow_step_id": "step_1",
            "resumed_from_checkpoint_id": "cp_1",
            "resume_launch_mode": "resume",
            "workspace_root": "/repo/current",
            "workspace_label": "Current Repo",
            "target_remote_name": "origin",
            "managed_branch_name": "palli/workspace",
            "trigger_source": "chat",
            "intent_kind": "repo_fix",
            "status": "completed",
            "objective": "Ship the patch",
            "decision_reason": "Need a local fix",
            "result_summary": "Patched the runtime issue",
            "last_transition_reason": "Owner approved the fix",
            "retry_of_action_id": "act_0",
            "replacement_action_id": "act_2",
            "metadata": {"surface": "mission_control"},
            "created_at": 2000.0,
            "updated_at": 2100.0,
            "completed_at": 2200.0,
        }
    ]


@pytest.mark.asyncio
async def test_palli_brain_action_list_delegates_to_extracted_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def update_operator_action(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._operator_action_store = stub

    # Great Excision (2026-07-09): update_operator_action is the ONLY facade
    # method kept for this store — untouchable core/desktop_perception.py
    # calls it on its operator-triggered-inspection branch.
    await brain.update_operator_action(action_id="act_stub", status="completed")

    assert stub.calls == [{"action_id": "act_stub", "status": "completed"}]
