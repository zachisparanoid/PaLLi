from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.passive_listening_store import PassiveListeningStore


class _FakeConnection:
    def __init__(
        self,
        *,
        fetchval_results: list[Any] | None = None,
        fetch_results: list[Any] | None = None,
    ) -> None:
        self.fetchval_results = list(fetchval_results or [])
        self.fetch_results = list(fetch_results or [])
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.fetchval_calls.append((query, args))
        if self.fetchval_results:
            return self.fetchval_results.pop(0)
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


@pytest.mark.asyncio
async def test_store_passive_listening_event_serializes_decision_and_context() -> None:
    connection = _FakeConnection(fetchval_results=[18])
    store = PassiveListeningStore(_FakeBrain(connection), now=lambda: 5432.1)

    event_id = await store.store_passive_listening_event(
        channel_id=42,
        utterance_count=6,
        check_reason="silence",
        decision={
            "should_interject": True,
            "confidence": 0.82,
            "reasoning": "Conversation paused after the owner mentioned dinner.",
            "urgency": "medium",
            "suggested_approach": "gentle_follow_up",
        },
        did_respond=True,
        passive_context=[
            {"speaker": "owner", "text": "What should I eat tonight?"},
            {"speaker": "friend", "text": "I don't know."},
        ],
        metadata={"source": "voice_presence", "surface": "discord"},
    )

    assert connection.closed is True
    assert event_id == 18
    _, args = connection.fetchval_calls[0]
    assert args[0] == 42
    assert args[1] == 5432.1
    assert args[2] == 6
    assert args[3] == "silence"
    assert args[4] is True
    assert args[5] == 0.82
    assert args[6] == "Conversation paused after the owner mentioned dinner."
    assert args[7] == "medium"
    assert args[8] == "gentle_follow_up"
    assert args[9] is True
    assert json.loads(args[10]) == [
        {"speaker": "owner", "text": "What should I eat tonight?"},
        {"speaker": "friend", "text": "I don't know."},
    ]
    assert json.loads(args[11]) == {"source": "voice_presence", "surface": "discord"}


@pytest.mark.asyncio
async def test_get_passive_listening_stats_aggregates_grouped_rows_and_channel_filter() -> None:
    connection = _FakeConnection(
        fetch_results=[
            [
                (3, 2, 1, 0.7, 4.0, "silence", "medium", "gentle_follow_up"),
                (2, 1, 1, 0.4, 6.0, "interval", "high", "direct_question"),
            ]
        ]
    )
    store = PassiveListeningStore(_FakeBrain(connection), now=lambda: 2000.0)

    stats = await store.get_passive_listening_stats(channel_id=42, days=3)

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "timestamp >= $1" in query
    assert "channel_id = $2" in query
    assert "GROUP BY check_reason, urgency, suggested_approach" in query
    assert args == (2000.0 - (3 * 24 * 60 * 60), 42)
    assert stats == {
        "period_days": 3,
        "total_checks": 5,
        "recommended_interjections": 3,
        "actual_responses": 2,
        "avg_confidence": pytest.approx(0.58),
        "avg_utterance_count": pytest.approx(4.8),
        "by_check_reason": {
            "silence": {"count": 3, "interjections": 2},
            "interval": {"count": 2, "interjections": 1},
        },
        "by_urgency": {
            "medium": 2,
            "high": 1,
        },
        "by_approach": {
            "gentle_follow_up": 2,
            "direct_question": 1,
        },
    }


@pytest.mark.asyncio
async def test_palli_brain_passive_listening_store_delegates_calls(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.store_calls: list[dict[str, Any]] = []
            self.stats_calls: list[dict[str, Any]] = []

        async def store_passive_listening_event(self, **kwargs: Any) -> int:
            self.store_calls.append(kwargs)
            return 29

        async def get_passive_listening_stats(self, **kwargs: Any) -> dict[str, Any]:
            self.stats_calls.append(kwargs)
            return {"period_days": kwargs["days"], "total_checks": 4}

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._passive_listening_store = stub

    event_id = await brain.store_passive_listening_event(
        channel_id=99,
        utterance_count=4,
        check_reason="utterance_count",
        decision={"should_interject": False, "confidence": 0.2},
        did_respond=False,
        passive_context=[{"speaker": "owner", "text": "hello"}],
        metadata={"surface": "local"},
    )
    stats = await brain.get_passive_listening_stats(channel_id=99, days=5)

    assert event_id == 29
    assert stats == {"period_days": 5, "total_checks": 4}
    assert stub.store_calls == [
        {
            "channel_id": 99,
            "utterance_count": 4,
            "check_reason": "utterance_count",
            "decision": {"should_interject": False, "confidence": 0.2},
            "did_respond": False,
            "passive_context": [{"speaker": "owner", "text": "hello"}],
            "metadata": {"surface": "local"},
        }
    ]
    assert stub.stats_calls == [{"channel_id": 99, "days": 5}]
