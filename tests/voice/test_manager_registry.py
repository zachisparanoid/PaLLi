from __future__ import annotations

import weakref
from types import SimpleNamespace

import pytest

from voice import manager_registry
from voice.runtime import (
    VoiceRuntimeManager,
    apply_registered_voice_runtime_overrides,
    apply_registered_voice_session_overrides,
    get_registered_voice_runtime_snapshot,
    list_registered_voice_runtime_snapshots,
    stop_registered_voice_session,
)


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
        runtime_session_key: str,
        channel_id: int,
        platform: str,
        owner_user_id: int | None,
        connected: bool = False,
    ) -> None:
        self.runtime_session_key = runtime_session_key
        self.channel_id = channel_id
        self.platform = platform
        self.owner_user_id = owner_user_id
        self.transport = _FakeTransport(connected=connected)
        self.begin_stop_calls = 0
        self.record_stopped_calls = 0
        self.interaction_mode = "direct_address"
        self.presence_profile = "balanced"
        self.focus_state = "responsive"
        self.local_transport_mode = "push_to_talk"
        self.voice_engine_requested = "classic"
        self.voice_preferences: dict[str, object] = {}
        self.stop_calls = 0
        self.abort_calls: list[str] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stop_calls += 1

    async def abort_response(self, *, reason: str) -> None:
        self.abort_calls.append(reason)

    def _begin_runtime_updates_stop(self) -> None:
        self.begin_stop_calls += 1

    async def _record_session_started(self) -> None:
        return None

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
        self.voice_preferences = dict(preferences)

    def get_status_snapshot(self) -> dict[str, object]:
        return {
            "runtime_session_key": self.runtime_session_key,
            "channel_id": self.channel_id,
            "platform": self.platform,
            "owner_user_id": self.owner_user_id,
            "interaction_mode": self.interaction_mode,
            "presence_profile": self.presence_profile,
            "focus_state": self.focus_state,
            "voice_engine_requested": self.voice_engine_requested,
            "local_transport_mode": self.local_transport_mode,
        }


@pytest.fixture(autouse=True)
def _reset_registered_managers(monkeypatch):
    monkeypatch.setattr(
        manager_registry,
        "_REGISTERED_RUNTIME_MANAGERS",
        weakref.WeakSet(),
    )


def test_registered_runtime_overrides_filter_by_owner_and_platform() -> None:
    manager = VoiceRuntimeManager()
    local = _FakeSession(
        runtime_session_key="voice:local:42",
        channel_id=42,
        platform="local",
        owner_user_id=7,
    )
    discord = _FakeSession(
        runtime_session_key="voice:discord:42",
        channel_id=42,
        platform="discord",
        owner_user_id=7,
    )
    other_owner = _FakeSession(
        runtime_session_key="voice:local:99",
        channel_id=99,
        platform="local",
        owner_user_id=11,
    )
    manager.registry.index_session(local)
    manager.registry.index_session(discord)
    manager.registry.index_session(other_owner)

    applied = apply_registered_voice_runtime_overrides(
        owner_user_id=7,
        platform="local",
        updates={
            # Step 31: the bridge is deleted — classic is the only engine
            # value an override can carry.
            "voice_engine": "classic",
            "interaction_mode": "passive",
            "presence_profile": "quiet",
            "focus_state": "focused",
            "local_transport_mode": "always_on",
        },
    )

    assert len(applied) == 1
    assert applied[0]["runtime_session_key"] == "voice:local:42"
    assert local.voice_engine_requested == "classic"
    assert local.interaction_mode == "passive"
    assert local.presence_profile == "quiet"
    assert local.focus_state == "focused"
    assert local.local_transport_mode == "always_on"
    assert discord.interaction_mode == "direct_address"
    assert other_owner.interaction_mode == "direct_address"


def test_registered_runtime_snapshot_and_session_override() -> None:
    manager = VoiceRuntimeManager()
    local = _FakeSession(
        runtime_session_key="voice:local:42",
        channel_id=42,
        platform="local",
        owner_user_id=7,
    )
    discord = _FakeSession(
        runtime_session_key="voice:discord:42",
        channel_id=42,
        platform="discord",
        owner_user_id=7,
    )
    manager.registry.index_session(local)
    manager.registry.index_session(discord)

    updated = apply_registered_voice_session_overrides(
        runtime_session_key="voice:local:42",
        updates={"interaction_mode": "observer"},
    )
    snapshot = get_registered_voice_runtime_snapshot("voice:local:42")
    snapshots = list_registered_voice_runtime_snapshots(owner_user_id=7)

    assert updated is not None
    assert updated["interaction_mode"] == "observer"
    assert snapshot is not None
    assert snapshot["interaction_mode"] == "observer"
    assert [item["runtime_session_key"] for item in snapshots] == [
        "voice:discord:42",
        "voice:local:42",
    ]


def test_registered_session_overrides_apply_voice_preferences_for_identity_payload() -> None:
    manager = VoiceRuntimeManager()
    local = _FakeSession(
        runtime_session_key="voice:local:42",
        channel_id=42,
        platform="local",
        owner_user_id=7,
    )
    manager.registry.index_session(local)

    updated = apply_registered_voice_session_overrides(
        runtime_session_key="voice:local:42",
        updates={
            "group_voice_behavior": "reserved",
            "_reply_identity_delivery": {
                "social_permission": "warm",
                "communication_rhythm": "ongoing",
            },
        },
    )

    assert updated is not None
    assert local.voice_preferences["group_voice_behavior"] == "reserved"
    assert local.voice_preferences["_reply_identity_delivery"] == {
        "social_permission": "warm",
        "communication_rhythm": "ongoing",
    }


@pytest.mark.asyncio
async def test_stop_registered_voice_session_returns_pre_stop_snapshot() -> None:
    manager = VoiceRuntimeManager()
    session = _FakeSession(
        runtime_session_key="voice:local:42",
        channel_id=42,
        platform="local",
        owner_user_id=7,
        connected=True,
    )
    manager.registry.index_session(session)

    snapshot = await stop_registered_voice_session(
        runtime_session_key="voice:local:42",
        disconnect_transport=True,
    )

    assert snapshot is not None
    assert snapshot["runtime_session_key"] == "voice:local:42"
    assert manager.get_session("voice:local:42") is None
    assert session.begin_stop_calls == 1
    assert session.record_stopped_calls == 1
    assert session.stop_calls == 1
    assert session.transport.disconnect_calls == 1
