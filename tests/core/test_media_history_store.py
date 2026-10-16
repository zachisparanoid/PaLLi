from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.media_history_store import MediaHistoryStore


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


@pytest.mark.asyncio
async def test_store_image_persists_metadata_and_trimmed_surface_id() -> None:
    connection = _FakeConnection(fetchval_results=[71])
    store = MediaHistoryStore(_FakeBrain(connection), now=lambda: 1234.5)

    image_id = await store.store_image(
        user_id=7,
        platform="discord",
        channel_id=42,
        surface_id=" dm-42 ",
        message_id=55,
        image_data=b"image-bytes",
        content_type="image/png",
        original_filename="desk.png",
        description="Desk photo",
        conversation_context="Earlier the user mentioned the monitor.",
        metadata={"vision_analysis": {"objects": ["monitor"]}},
    )

    assert connection.closed is True
    assert image_id == 71
    _, args = connection.fetchval_calls[0]
    assert args[0] == 7
    assert args[1] == "discord"
    assert args[2] == 42
    assert args[3] == "dm-42"
    assert args[4] == 55
    assert args[5] == b"image-bytes"
    assert args[6] == "image/png"
    assert args[7] == "desk.png"
    assert args[8] == "Desk photo"
    assert args[9] == "Earlier the user mentioned the monitor."
    assert args[10] == 1234.5
    assert json.loads(args[11]) == {"vision_analysis": {"objects": ["monitor"]}}


@pytest.mark.asyncio
async def test_get_recent_images_builds_filters_and_returns_chronological_rows() -> None:
    connection = _FakeConnection(
        fetch_results=[
            [
                (
                    12,
                    7,
                    "discord",
                    42,
                    None,
                    55,
                    b"newer",
                    "image/png",
                    "desk-2.png",
                    "Newer image",
                    "second",
                    2000.0,
                    json.dumps({"rank": 2}),
                ),
                (
                    11,
                    7,
                    "discord",
                    42,
                    "dm-42",
                    54,
                    b"older",
                    "image/jpeg",
                    "desk-1.jpg",
                    "Older image",
                    "first",
                    1000.0,
                    json.dumps({"rank": 1}),
                ),
            ]
        ]
    )
    store = MediaHistoryStore(_FakeBrain(connection))

    images = await store.get_recent_images(
        user_id=7,
        channel_id=42,
        surface_id="dm-42",
        limit=2,
        include_image_data=True,
    )

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "user_id = $1" in query
    assert "channel_id = $2" in query
    assert "surface_id = $3" in query
    assert args == (7, 42, "dm-42", 2)
    assert [image["id"] for image in images] == [11, 12]
    assert images[0]["image_data"] == b"older"
    assert images[0]["surface_id"] == "dm-42"
    assert images[0]["metadata"] == {"rank": 1}
    assert images[1]["surface_id"] == "42"
    assert images[1]["metadata"] == {"rank": 2}


@pytest.mark.asyncio
async def test_get_image_by_id_maps_record_and_honors_image_data_toggle() -> None:
    connection = _FakeConnection(
        fetchrow_results=[
            (
                33,
                9,
                "web",
                77,
                "surface:77",
                None,
                b"blob",
                "image/webp",
                "snapshot.webp",
                "Mission control screenshot",
                "dashboard",
                3000.0,
                json.dumps({"source": "dashboard"}),
            )
        ]
    )
    store = MediaHistoryStore(_FakeBrain(connection))

    image = await store.get_image_by_id(33, include_image_data=False)

    assert connection.closed is True
    assert connection.fetchrow_calls[0][1] == (33,)
    assert image == {
        "id": 33,
        "user_id": 9,
        "platform": "web",
        "channel_id": 77,
        "surface_id": "surface:77",
        "message_id": None,
        "content_type": "image/webp",
        "original_filename": "snapshot.webp",
        "description": "Mission control screenshot",
        "conversation_context": "dashboard",
        "timestamp": 3000.0,
        "metadata": {"source": "dashboard"},
    }


@pytest.mark.asyncio
async def test_palli_brain_media_history_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.store_calls: list[dict[str, Any]] = []
            self.recent_calls: list[dict[str, Any]] = []
            self.by_id_calls: list[tuple[int, bool]] = []

        async def store_image(self, **kwargs: Any) -> int:
            self.store_calls.append(kwargs)
            return 91

        async def get_recent_images(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.recent_calls.append(kwargs)
            return [{"id": 1}]

        async def get_image_by_id(self, image_id: int, *, include_image_data: bool = True) -> dict[str, Any] | None:
            self.by_id_calls.append((image_id, include_image_data))
            return {"id": image_id, "image_data": b"bytes" if include_image_data else None}

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._media_history_store = stub

    stored_id = await brain.store_image(
        user_id=7,
        platform="discord",
        channel_id=42,
        image_data=b"img",
        content_type="image/png",
        description="Desk photo",
        original_filename="desk.png",
        conversation_context="Prior thread",
        message_id=55,
        metadata={"kind": "vision"},
        surface_id="dm-42",
    )
    recent_images = await brain.get_recent_images(channel_id=42, surface_id="dm-42", limit=3, include_image_data=False)
    image = await brain.get_image_by_id(91, include_image_data=True)

    assert stored_id == 91
    assert recent_images == [{"id": 1}]
    assert image == {"id": 91, "image_data": b"bytes"}
    assert stub.store_calls == [
        {
            "user_id": 7,
            "platform": "discord",
            "channel_id": 42,
            "image_data": b"img",
            "content_type": "image/png",
            "description": "Desk photo",
            "original_filename": "desk.png",
            "conversation_context": "Prior thread",
            "message_id": 55,
            "metadata": {"kind": "vision"},
            "surface_id": "dm-42",
        }
    ]
    assert stub.recent_calls == [
        {
            "user_id": None,
            "channel_id": 42,
            "surface_id": "dm-42",
            "limit": 3,
            "include_image_data": False,
        }
    ]
    assert stub.by_id_calls == [(91, True)]
