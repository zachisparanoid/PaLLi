from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.plugin_runtime_store import PluginRuntimeStore


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
        self.close_count = 0

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


def _make_plugin_row() -> tuple[Any, ...]:
    row = (
        "builtin.llm.providers",
        "Built-in LLM Providers",
        "builtin",
        "1",
        "Core providers",
        "builtin:llm_providers",
        "builtin",
        "",
        json.dumps(["llm_provider"]),
        "required",
        "runtime_reload",
        json.dumps(["any"]),
        json.dumps({"openai": True}),
        json.dumps({"plugin_id": "builtin.llm.providers"}),
        True,
        True,
        "compatible",
        "ready",
        False,
        "",
        1000.0,
        1005.0,
        json.dumps({"source": "catalog"}),
        900.0,
        1010.0,
    )
    assert len(row) == 25
    return row


def _make_run_row() -> tuple[Any, ...]:
    row = (
        "pluginrun_1",
        "manual",
        "discover",
        "completed",
        json.dumps(["builtin.llm.providers"]),
        False,
        "Catalog refreshed",
        "",
        json.dumps({"count": 1}),
        1100.0,
        1110.0,
    )
    assert len(row) == 11
    return row


@pytest.mark.asyncio
async def test_plugin_runtime_store_persists_catalog_state_and_run_history() -> None:
    connection = _FakeConnection(
        fetchrow_results=[('{"existing": true}',), _make_plugin_row()],
        fetch_results=[[_make_plugin_row()], [_make_run_row()]],
    )
    store = PluginRuntimeStore(_FakeBrain(connection), now=lambda: 1200.0)

    await store.upsert_plugin_runtime_plugin(
        plugin_id=" builtin.llm.providers ",
        display_name=" Built-in LLM Providers ",
        version=" builtin ",
        plugin_api_version=" 1 ",
        description=" Core providers ",
        entrypoint=" builtin:llm_providers ",
        source_kind=" builtin ",
        families=[" llm_provider "],
        criticality=" required ",
        reload_mode=" runtime_reload ",
        host_support=[" any "],
        requires_json={"openai": True},
        manifest={"plugin_id": "builtin.llm.providers"},
        desired_enabled=True,
        applied_enabled=False,
        compatibility_status=" compatible ",
        health_status=" ready ",
        restart_required=False,
        last_error="",
        last_discovered_at=1000.0,
        last_applied_at=1005.0,
        metadata={"source": "catalog"},
        created_at=900.0,
    )
    await store.update_plugin_runtime_plugin(
        plugin_id="builtin.llm.providers",
        applied_enabled=True,
        metadata={"last_action": "discover"},
        last_applied_at=1205.0,
    )
    plugin = await store.get_plugin_runtime_plugin(plugin_id="builtin.llm.providers")
    plugins = await store.list_plugin_runtime_plugins()
    await store.create_plugin_runtime_run(
        plugin_runtime_run_id=" pluginrun_1 ",
        trigger_kind=" manual ",
        action_kind=" discover ",
        status=" completed ",
        changed_plugin_ids=[" builtin.llm.providers "],
        summary=" Catalog refreshed ",
        metadata={"count": 1},
        created_at=1100.0,
    )
    runs = await store.list_plugin_runtime_runs(limit=4)

    assert connection.close_count == 6

    create_query, create_args = connection.execute_calls[0]
    assert "INSERT INTO plugin_runtime_plugins" in create_query
    assert create_args[0] == "builtin.llm.providers"
    assert create_args[1] == "Built-in LLM Providers"
    assert json.loads(create_args[8]) == ["llm_provider"]
    assert json.loads(create_args[12]) == {"openai": True}
    assert json.loads(create_args[13]) == {"plugin_id": "builtin.llm.providers"}
    assert create_args[20] == 1000.0
    assert create_args[21] == 1005.0
    assert json.loads(create_args[22]) == {"source": "catalog"}
    assert create_args[23] == 900.0

    update_query, update_args = connection.execute_calls[1]
    assert "UPDATE plugin_runtime_plugins" in update_query
    assert update_args[0] == "builtin.llm.providers"
    assert update_args[2] is True
    assert update_args[8] == 1205.0
    assert json.loads(update_args[9]) == {"existing": True, "last_action": "discover"}
    assert update_args[10] == 1200.0

    get_query, get_args = connection.fetchrow_calls[1]
    assert "FROM plugin_runtime_plugins" in get_query
    assert get_args == ("builtin.llm.providers",)
    assert plugin == {
        "plugin_id": "builtin.llm.providers",
        "display_name": "Built-in LLM Providers",
        "version": "builtin",
        "plugin_api_version": "1",
        "description": "Core providers",
        "entrypoint": "builtin:llm_providers",
        "source_kind": "builtin",
        "source_path": "",
        "families": ["llm_provider"],
        "criticality": "required",
        "reload_mode": "runtime_reload",
        "host_support": ["any"],
        "requires_json": {"openai": True},
        "manifest": {"plugin_id": "builtin.llm.providers"},
        "desired_enabled": True,
        "applied_enabled": True,
        "compatibility_status": "compatible",
        "health_status": "ready",
        "restart_required": False,
        "last_error": "",
        "last_discovered_at": 1000.0,
        "last_applied_at": 1005.0,
        "metadata": {"source": "catalog"},
        "created_at": 900.0,
        "updated_at": 1010.0,
    }

    list_query, list_args = connection.fetch_calls[0]
    assert "ORDER BY source_kind ASC, display_name COLLATE NOCASE ASC, plugin_id ASC" in list_query
    assert list_args == ()
    assert plugins == [plugin]

    run_query, run_args = connection.execute_calls[2]
    assert "INSERT INTO plugin_runtime_runs" in run_query
    assert run_args[0] == "pluginrun_1"
    assert run_args[1] == "manual"
    assert run_args[2] == "discover"
    assert json.loads(run_args[4]) == ["builtin.llm.providers"]
    assert json.loads(run_args[8]) == {"count": 1}
    assert run_args[9] == 1100.0

    runs_query, runs_args = connection.fetch_calls[1]
    assert "ORDER BY created_at DESC" in runs_query
    assert runs_args == (4,)
    assert runs == [
        {
            "plugin_runtime_run_id": "pluginrun_1",
            "trigger_kind": "manual",
            "action_kind": "discover",
            "status": "completed",
            "changed_plugin_ids": ["builtin.llm.providers"],
            "restart_required": False,
            "summary": "Catalog refreshed",
            "error_text": "",
            "metadata": {"count": 1},
            "created_at": 1100.0,
            "updated_at": 1110.0,
        }
    ]


@pytest.mark.asyncio
async def test_palli_brain_plugin_runtime_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.upsert_plugin_runtime_plugin_calls: list[dict[str, Any]] = []
            self.get_plugin_runtime_plugin_calls: list[str] = []
            self.list_plugin_runtime_plugins_calls = 0
            self.update_plugin_runtime_plugin_calls: list[dict[str, Any]] = []
            self.create_plugin_runtime_run_calls: list[dict[str, Any]] = []
            self.list_plugin_runtime_runs_calls: list[int] = []

        async def upsert_plugin_runtime_plugin(self, **kwargs: Any) -> None:
            self.upsert_plugin_runtime_plugin_calls.append(kwargs)

        async def get_plugin_runtime_plugin(self, *, plugin_id: str) -> dict[str, Any]:
            self.get_plugin_runtime_plugin_calls.append(plugin_id)
            return {"plugin_id": plugin_id}

        async def list_plugin_runtime_plugins(self) -> list[dict[str, Any]]:
            self.list_plugin_runtime_plugins_calls += 1
            return [{"plugin_id": "builtin.llm.providers"}]

        async def update_plugin_runtime_plugin(self, **kwargs: Any) -> None:
            self.update_plugin_runtime_plugin_calls.append(kwargs)

        async def create_plugin_runtime_run(self, **kwargs: Any) -> None:
            self.create_plugin_runtime_run_calls.append(kwargs)

        async def list_plugin_runtime_runs(self, *, limit: int = 12) -> list[dict[str, Any]]:
            self.list_plugin_runtime_runs_calls.append(limit)
            return [{"plugin_runtime_run_id": "pluginrun_1"}]

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._plugin_runtime_store = stub

    await brain.upsert_plugin_runtime_plugin(
        plugin_id="builtin.llm.providers",
        display_name="Built-in LLM Providers",
        source_kind="builtin",
        criticality="required",
        reload_mode="runtime_reload",
    )
    plugin = await brain.get_plugin_runtime_plugin(plugin_id="builtin.llm.providers")
    plugins = await brain.list_plugin_runtime_plugins()
    await brain.update_plugin_runtime_plugin(
        plugin_id="builtin.llm.providers",
        applied_enabled=True,
        metadata={"last_action": "discover"},
    )
    await brain.create_plugin_runtime_run(
        plugin_runtime_run_id="pluginrun_1",
        trigger_kind="manual",
        action_kind="discover",
        status="completed",
    )
    runs = await brain.list_plugin_runtime_runs(limit=5)

    assert plugin == {"plugin_id": "builtin.llm.providers"}
    assert plugins == [{"plugin_id": "builtin.llm.providers"}]
    assert runs == [{"plugin_runtime_run_id": "pluginrun_1"}]
    assert stub.upsert_plugin_runtime_plugin_calls == [
        {
            "plugin_id": "builtin.llm.providers",
            "display_name": "Built-in LLM Providers",
            "version": None,
            "plugin_api_version": None,
            "description": None,
            "entrypoint": None,
            "source_kind": "builtin",
            "source_path": None,
            "families": None,
            "criticality": "required",
            "reload_mode": "runtime_reload",
            "host_support": None,
            "requires_json": None,
            "manifest": None,
            "desired_enabled": False,
            "applied_enabled": False,
            "compatibility_status": "unknown",
            "health_status": "unknown",
            "restart_required": False,
            "last_error": None,
            "last_discovered_at": None,
            "last_applied_at": None,
            "metadata": None,
            "created_at": None,
        }
    ]
    assert stub.get_plugin_runtime_plugin_calls == ["builtin.llm.providers"]
    assert stub.list_plugin_runtime_plugins_calls == 1
    assert stub.update_plugin_runtime_plugin_calls == [
        {
            "plugin_id": "builtin.llm.providers",
            "desired_enabled": None,
            "applied_enabled": True,
            "compatibility_status": None,
            "health_status": None,
            "restart_required": None,
            "last_error": None,
            "last_discovered_at": None,
            "last_applied_at": None,
            "metadata": {"last_action": "discover"},
        }
    ]
    assert stub.create_plugin_runtime_run_calls == [
        {
            "plugin_runtime_run_id": "pluginrun_1",
            "trigger_kind": "manual",
            "action_kind": "discover",
            "status": "completed",
            "changed_plugin_ids": None,
            "restart_required": False,
            "summary": None,
            "error_text": None,
            "metadata": None,
            "created_at": None,
        }
    ]
    assert stub.list_plugin_runtime_runs_calls == [5]
