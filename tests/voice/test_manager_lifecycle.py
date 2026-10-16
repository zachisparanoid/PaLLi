from __future__ import annotations

from types import SimpleNamespace

import pytest

from voice.manager_lifecycle import (
    VoiceRuntimeSessionStartRequest,
    start_runtime_session,
    stop_runtime_session_by_key,
)
from voice.manager_registry import VoiceRuntimeSessionRegistry


class _FakeTransport:
    def __init__(self, *, connected: bool = False) -> None:
        self.disconnect_calls = 0

        async def _disconnect() -> None:
            self.disconnect_calls += 1

        self.voice_client = SimpleNamespace(
            is_connected=lambda: connected,
            disconnect=_disconnect,
        )


class _FakeSession:
    def __init__(
        self,
        *,
        channel_id: int,
        text_channel_id: int,
        transport: _FakeTransport,
        owner_user_id: int | None = None,
        initiator_user_id: int | None = None,
        account_user_id: int | None = None,
        account_features: dict[str, bool] | None = None,
        platform: str = "discord",
        is_dm: bool = False,
        event_callback=None,
        start_error: Exception | None = None,
    ) -> None:
        self.channel_id = channel_id
        self.text_channel_id = text_channel_id
        self.transport = transport
        self.owner_user_id = owner_user_id
        self.initiator_user_id = initiator_user_id
        self.account_user_id = account_user_id
        self.account_features = account_features or {}
        self.platform = platform
        self.is_dm = is_dm
        self.event_callback = event_callback
        self.runtime_session_key = ""
        self.interaction_mode = "direct_address"
        self.presence_profile = "balanced"
        self.focus_state = "responsive"
        self.local_transport_mode = "push_to_talk"
        self.voice_engine_requested = "classic"
        self.begin_stop_calls = 0
        self.record_started_calls = 0
        self.record_stopped_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self._start_error = start_error

    async def start(self) -> None:
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error

    async def stop(self) -> None:
        self.stop_calls += 1

    def _begin_runtime_updates_stop(self) -> None:
        self.begin_stop_calls += 1

    async def _record_session_started(self) -> None:
        self.record_started_calls += 1

    async def _record_session_stopped(self) -> None:
        self.record_stopped_calls += 1

    def set_voice_engine(self, voice_engine: str) -> None:
        self.voice_engine_requested = voice_engine

    def set_interaction_mode(self, interaction_mode: str) -> None:
        self.interaction_mode = interaction_mode

    def set_presence_profile(self, presence_profile: str) -> None:
        self.presence_profile = presence_profile

    def set_focus_state(self, focus_state: str) -> None:
        self.focus_state = focus_state

    def set_local_voice_mode(self, mode: str) -> None:
        self.local_transport_mode = mode

    def apply_voice_preferences(self, preferences: dict[str, object]) -> None:
        return None

    def get_status_snapshot(self) -> dict[str, object]:
        return {
            "runtime_session_key": self.runtime_session_key,
            "channel_id": self.channel_id,
            "platform": self.platform,
            "owner_user_id": self.owner_user_id,
        }


@pytest.mark.asyncio
async def test_start_runtime_session_reuses_existing_owner_session_key() -> None:
    registry = VoiceRuntimeSessionRegistry()
    existing = _FakeSession(
        channel_id=42,
        text_channel_id=42,
        transport=_FakeTransport(),
        owner_user_id=7,
        platform="local",
        is_dm=True,
    )
    existing.runtime_session_key = "owner:7:surface:local:voice:42"
    registry.index_session(existing)

    created: list[_FakeSession] = []
    stopped: list[tuple[str, bool]] = []

    async def fake_stop(runtime_session_key: str, *, disconnect_transport: bool = False) -> bool:
        stopped.append((runtime_session_key, disconnect_transport))
        registry.active_sessions.pop(runtime_session_key, None)
        return True

    async def fake_ensure_surface_session(**_kwargs) -> dict[str, object]:
        return {"session_key": "owner:7:surface:local:voice:42"}

    def fake_session_factory(**kwargs) -> _FakeSession:
        session = _FakeSession(**kwargs)
        created.append(session)
        return session

    started = await start_runtime_session(
        registry=registry,
        request=VoiceRuntimeSessionStartRequest(
            channel_id=42,
            text_channel_id=84,
            transport=_FakeTransport(),
            initiator_user_id=7,
            platform="local",
            is_dm=True,
        ),
        session_factory=fake_session_factory,
        stop_session_by_key=fake_stop,
        ensure_surface_session=fake_ensure_surface_session,
    )

    assert started is True
    assert stopped == [("owner:7:surface:local:voice:42", False)]
    assert created[0].runtime_session_key == "owner:7:surface:local:voice:42"
    assert created[0].record_started_calls == 1
    assert registry.get_session("owner:7:surface:local:voice:42") is created[0]


@pytest.mark.asyncio
async def test_start_runtime_session_uses_fallback_key_without_owner() -> None:
    registry = VoiceRuntimeSessionRegistry()
    existing = _FakeSession(
        channel_id=42,
        text_channel_id=42,
        transport=_FakeTransport(),
        platform="local",
        is_dm=True,
    )
    existing.runtime_session_key = "stale-session"
    registry.index_session(existing)

    created: list[_FakeSession] = []
    stopped: list[str] = []

    async def fake_stop(runtime_session_key: str, *, disconnect_transport: bool = False) -> bool:
        stopped.append(runtime_session_key)
        registry.active_sessions.pop(runtime_session_key, None)
        return True

    def fake_session_factory(**kwargs) -> _FakeSession:
        session = _FakeSession(**kwargs)
        created.append(session)
        return session

    started = await start_runtime_session(
        registry=registry,
        request=VoiceRuntimeSessionStartRequest(
            channel_id=42,
            text_channel_id=84,
            transport=_FakeTransport(),
            platform="local",
            is_dm=True,
        ),
        session_factory=fake_session_factory,
        stop_session_by_key=fake_stop,
    )

    assert started is True
    assert stopped == ["stale-session"]
    assert created[0].runtime_session_key == "voice:local:42"
    assert registry.get_session("voice:local:42") is created[0]


@pytest.mark.asyncio
async def test_start_runtime_session_cleans_up_partial_start_failure() -> None:
    registry = VoiceRuntimeSessionRegistry()
    created: list[_FakeSession] = []

    def fake_session_factory(**kwargs) -> _FakeSession:
        session = _FakeSession(**kwargs, start_error=RuntimeError("boom"))
        created.append(session)
        return session

    with pytest.raises(RuntimeError, match="boom"):
        await start_runtime_session(
            registry=registry,
            request=VoiceRuntimeSessionStartRequest(
                channel_id=42,
                text_channel_id=84,
                transport=_FakeTransport(),
                platform="local",
                is_dm=True,
            ),
            session_factory=fake_session_factory,
            stop_session_by_key=lambda *_args, **_kwargs: _async_result(False),
        )

    failed = created[0]
    assert registry.get_session(failed.runtime_session_key) is None
    assert failed.begin_stop_calls == 1
    assert failed.stop_calls == 1


@pytest.mark.asyncio
async def test_stop_runtime_session_by_key_records_stop_and_disconnects_transport() -> None:
    registry = VoiceRuntimeSessionRegistry()
    transport = _FakeTransport(connected=True)
    session = _FakeSession(
        channel_id=42,
        text_channel_id=84,
        transport=transport,
        owner_user_id=7,
        platform="discord",
        is_dm=False,
    )
    session.runtime_session_key = "voice:discord:42"
    registry.index_session(session)

    stopped = await stop_runtime_session_by_key(
        registry=registry,
        runtime_session_key="voice:discord:42",
        disconnect_transport=True,
    )

    assert stopped is True
    assert registry.get_session("voice:discord:42") is None
    assert session.begin_stop_calls == 1
    assert session.record_stopped_calls == 1
    assert session.stop_calls == 1
    assert transport.disconnect_calls == 1


async def _async_result(value: object) -> object:
    return value
