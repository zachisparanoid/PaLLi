from __future__ import annotations

import json
from typing import Any

import pytest

from core.database import PalliBrain
from core.voice_session_store import VoiceSessionStore


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
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
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


def _make_voice_session_row() -> tuple[Any, ...]:
    row = (
        9,
        42,
        None,
        None,
        None,
        None,
        123.4,
        None,
        "ambient",
        None,
        "focused",
        None,
        None,
        2,
        None,
        "",
        None,
        "",
        "",
        125.6,
        json.dumps({"engine": "classic", "surface": "local"}),
    )
    assert len(row) == 21
    return row


@pytest.mark.asyncio
async def test_start_voice_session_normalizes_fields_and_persists_metadata() -> None:
    connection = _FakeConnection(fetchval_results=[41])
    store = VoiceSessionStore(_FakeBrain(connection), now=lambda: 2500.0)

    result = await store.start_voice_session(
        channel_id=123,
        user_id=7,
        platform=" Local ",
        text_channel_id=456,
        runtime_session_key=" runtime-1 ",
        interaction_mode=" ambient ",
        presence_profile=" companion ",
        focus_state=" focused ",
        social_context=" shared_room ",
        human_participant_count=2,
        bot_participant_count=1,
        owner_present=True,
        last_decision_reason=" owner_present ",
        initiative_source=" proactive ",
        reply_posture=" brief_companion ",
        local_transport_mode=" push_to_talk ",
        last_floor_state=" listening ",
        metadata={"voice_engine": "classic"},
    )

    assert result == 41
    assert connection.closed is True
    query, args = connection.fetchval_calls[0]
    assert "INSERT INTO voice_sessions" in query
    assert args[0:19] == (
        123,
        7,
        "local",
        456,
        "runtime-1",
        2500.0,
        "listening",
        "ambient",
        "companion",
        "focused",
        "shared_room",
        2,
        1,
        True,
        "owner_present",
        "proactive",
        "brief_companion",
        "push_to_talk",
        2500.0,
    )
    assert json.loads(args[19]) == {"voice_engine": "classic"}


@pytest.mark.asyncio
async def test_update_voice_session_state_persists_latest_state_and_touch_time() -> None:
    connection = _FakeConnection()
    store = VoiceSessionStore(_FakeBrain(connection), now=lambda: 2600.0)

    await store.update_voice_session_state(
        session_id=41,
        last_floor_state=" speaking ",
        interaction_mode=" group ",
        presence_profile=" attentive ",
        focus_state=" waiting ",
        social_context=" direct_address ",
        human_participant_count=3,
        bot_participant_count=1,
        owner_present=False,
        last_decision_reason=" follow_up ",
        initiative_source=" owner ",
        reply_posture=" concise ",
        local_transport_mode=" duplex ",
        metadata={"fallback_active": True},
        end_time=2700.5,
    )

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "UPDATE voice_sessions" in query
    assert args[0:13] == (
        41,
        "speaking",
        "group",
        "attentive",
        "waiting",
        "direct_address",
        3,
        1,
        False,
        "follow_up",
        "owner",
        "concise",
        "duplex",
    )
    assert json.loads(args[13]) == {"fallback_active": True}
    assert args[14:] == (2600.0, 2700.5)


@pytest.mark.asyncio
async def test_end_voice_session_for_channel_filters_by_platform_when_provided() -> None:
    connection = _FakeConnection()
    store = VoiceSessionStore(_FakeBrain(connection), now=lambda: 2700.0)

    await store.end_voice_session_for_channel(123, platform=" Discord ")

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "platform = $3" in query
    assert args == (2700.0, 123, "discord")


@pytest.mark.asyncio
async def test_list_open_voice_sessions_maps_defaults_and_decodes_metadata() -> None:
    connection = _FakeConnection(fetch_results=[[_make_voice_session_row()]])
    store = VoiceSessionStore(_FakeBrain(connection), now=lambda: 2800.0)

    result = await store.list_open_voice_sessions(limit=0)

    assert connection.closed is True
    query, args = connection.fetch_calls[0]
    assert "FROM voice_sessions" in query
    assert args == (32,)
    assert result == [
        {
            "id": 9,
            "channel_id": 42,
            "user_id": None,
            "platform": "discord",
            "text_channel_id": None,
            "runtime_session_key": "",
            "start_time": 123.4,
            "last_floor_state": "",
            "interaction_mode": "ambient",
            "presence_profile": "",
            "focus_state": "focused",
            "social_context": "",
            "human_participant_count": 0,
            "bot_participant_count": 2,
            "owner_present": False,
            "last_decision_reason": "",
            "initiative_source": "none",
            "reply_posture": "brief_companion",
            "local_transport_mode": None,
            "last_event_at": 125.6,
            "metadata": {"engine": "classic", "surface": "local"},
        }
    ]


@pytest.mark.asyncio
async def test_close_voice_session_uses_now_when_end_time_is_absent() -> None:
    connection = _FakeConnection()
    store = VoiceSessionStore(_FakeBrain(connection), now=lambda: 2900.0)

    await store.close_voice_session(session_id=41)

    assert connection.closed is True
    query, args = connection.execute_calls[0]
    assert "SET end_time = COALESCE($2, end_time, $3)" in query
    assert args == (41, None, 2900.0)


@pytest.mark.asyncio
async def test_palli_brain_voice_session_methods_delegate_to_extracted_store(tmp_path) -> None:
    class _StubStore:
        def __init__(self) -> None:
            self.start_calls: list[dict[str, Any]] = []
            self.update_calls: list[dict[str, Any]] = []
            self.end_calls: list[dict[str, Any]] = []
            self.list_calls: list[dict[str, Any]] = []
            self.close_calls: list[dict[str, Any]] = []

        async def start_voice_session(self, **kwargs: Any) -> int:
            self.start_calls.append(kwargs)
            return 41

        async def update_voice_session_state(self, **kwargs: Any) -> None:
            self.update_calls.append(kwargs)

        async def end_voice_session_for_channel(self, **kwargs: Any) -> None:
            self.end_calls.append(kwargs)

        async def list_open_voice_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
            self.list_calls.append(kwargs)
            return [{"id": 41}]

        async def close_voice_session(self, **kwargs: Any) -> None:
            self.close_calls.append(kwargs)

    brain = PalliBrain(db_path=str(tmp_path / "palli-test.sqlite3"))
    stub = _StubStore()
    brain._voice_session_store = stub

    session_id = await brain.start_voice_session(channel_id=123, platform="local", runtime_session_key="runtime-1")
    await brain.update_voice_session_state(session_id=41, interaction_mode="ambient")
    await brain.end_voice_session_for_channel(123, platform="discord")
    sessions = await brain.list_open_voice_sessions(limit=8)
    await brain.close_voice_session(session_id=41, end_time=3000.5)

    assert session_id == 41
    assert sessions == [{"id": 41}]
    assert stub.start_calls[0]["channel_id"] == 123
    assert stub.start_calls[0]["platform"] == "local"
    assert stub.start_calls[0]["runtime_session_key"] == "runtime-1"
    assert stub.update_calls == [{"session_id": 41, "interaction_mode": "ambient", "last_floor_state": None, "presence_profile": None, "focus_state": None, "social_context": None, "human_participant_count": None, "bot_participant_count": None, "owner_present": None, "last_decision_reason": None, "initiative_source": None, "reply_posture": None, "local_transport_mode": None, "metadata": None, "end_time": None}]
    assert stub.end_calls == [{"channel_id": 123, "platform": "discord"}]
    assert stub.list_calls == [{"limit": 8}]
    assert stub.close_calls == [{"session_id": 41, "end_time": 3000.5}]
