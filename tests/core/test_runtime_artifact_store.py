from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.runtime_artifact_store import RuntimeArtifactStore


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


def _make_artifact_row(storage_path: str) -> tuple[Any, ...]:
    row = (
        "art_capture_1",
        7,
        "desktop_capture",
        "capture_1",
        "run_1",
        "act_1",
        "123456",
        storage_path,
        "image",
        "ready",
        "image/png",
        2600.0,
        json.dumps({"label": "Desktop capture"}),
        2500.0,
        2550.0,
        2560.0,
        2,
    )
    assert len(row) == 17
    return row


@pytest.mark.asyncio
async def test_runtime_artifact_store_persists_and_marks_delivery(tmp_path: Path) -> None:
    storage_path = str(tmp_path / "capture.png")
    Path(storage_path).write_bytes(b"png")
    connection = _FakeConnection(
        fetchrow_results=[(json.dumps({"existing": True}), 1), _make_artifact_row(storage_path)],
        fetch_results=[[_make_artifact_row(storage_path)]],
    )
    store = RuntimeArtifactStore(_FakeBrain(connection), now=lambda: 2555.0)

    await store.create_runtime_artifact(
        artifact_id=" art_capture_1 ",
        owner_user_id=7,
        source_kind=" desktop_capture ",
        source_id=" capture_1 ",
        source_run_id=" run_1 ",
        source_action_id=" act_1 ",
        surface_id=" 123456 ",
        artifact_kind=" image ",
        storage_path=f" {storage_path} ",
        content_type=" image/png ",
        expires_at=2600.0,
        metadata={"label": "Desktop capture"},
        created_at=2500.0,
    )
    await store.mark_runtime_artifact_delivered(
        artifact_id="art_capture_1",
        surface_id="654321",
        metadata={"last_delivery_platform": "discord"},
    )
    artifact = await store.get_runtime_artifact(artifact_id="art_capture_1")
    artifacts = await store.list_runtime_artifacts(owner_user_id=7, source_kind="desktop_capture", status="ready", limit=4)

    assert connection.close_count == 4

    create_query, create_args = connection.execute_calls[0]
    assert "INSERT INTO runtime_artifacts" in create_query
    assert create_args[0] == "art_capture_1"
    assert create_args[2] == "desktop_capture"
    assert create_args[3] == "capture_1"
    assert create_args[4] == "run_1"
    assert create_args[5] == "act_1"
    assert create_args[6] == "123456"
    assert create_args[7] == storage_path
    assert create_args[8] == "image"
    assert create_args[9] == "ready"
    assert create_args[10] == "image/png"
    assert create_args[11] == 2600.0
    assert json.loads(create_args[12]) == {"label": "Desktop capture"}
    assert create_args[13] == 2500.0

    mark_query, mark_args = connection.execute_calls[1]
    assert "UPDATE runtime_artifacts" in mark_query
    assert mark_args[0] == "art_capture_1"
    assert mark_args[1] == "654321"
    assert json.loads(mark_args[2]) == {"existing": True, "last_delivery_platform": "discord"}
    assert mark_args[3] == 2555.0

    assert artifact == {
        "artifact_id": "art_capture_1",
        "owner_user_id": 7,
        "source_kind": "desktop_capture",
        "source_id": "capture_1",
        "source_run_id": "run_1",
        "source_action_id": "act_1",
        "surface_id": "123456",
        "storage_path": storage_path,
        "artifact_kind": "image",
        "status": "ready",
        "effective_status": "ready",
        "content_type": "image/png",
        "expires_at": 2600.0,
        "metadata": {"label": "Desktop capture"},
        "created_at": 2500.0,
        "updated_at": 2550.0,
        "last_delivered_at": 2560.0,
        "delivery_count": 2,
        "label": "Desktop capture",
        "file_exists": True,
        "expired": False,
    }
    assert artifacts == [artifact]


@pytest.mark.asyncio
async def test_runtime_artifact_store_marks_missing_files_as_missing(tmp_path: Path) -> None:
    missing_path = str(tmp_path / "missing.png")
    connection = _FakeConnection(fetchrow_results=[_make_artifact_row(missing_path)])
    store = RuntimeArtifactStore(_FakeBrain(connection), now=lambda: 2555.0)

    artifact = await store.get_runtime_artifact(artifact_id="art_capture_1")

    assert artifact is not None
    assert artifact["file_exists"] is False
    assert artifact["effective_status"] == "missing"
