from __future__ import annotations

import weakref
from typing import Any, Dict, Protocol


class VoiceRuntimeUpdatesProtocol(Protocol):
    def begin_stop(self) -> None: ...

    async def record_session_started(self) -> None: ...

    async def record_session_stopped(self) -> None: ...


class ManagedVoiceSession(Protocol):
    channel_id: int
    platform: str
    runtime_session_key: str
    owner_user_id: int | None
    interaction_mode: str
    presence_profile: str
    focus_state: str
    local_transport_mode: str | None
    voice_engine_requested: str
    transport: Any
    _runtime_updates: VoiceRuntimeUpdatesProtocol

    def set_voice_engine(self, voice_engine: str) -> None: ...

    def set_interaction_mode(self, interaction_mode: str) -> None: ...

    def set_presence_profile(self, presence_profile: str) -> None: ...

    def set_focus_state(self, focus_state: str) -> None: ...

    def set_local_voice_mode(self, mode: str) -> None: ...

    def apply_voice_preferences(self, preferences: Dict[str, Any]) -> None: ...

    def get_status_snapshot(self) -> Dict[str, Any]: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class VoiceRuntimeManagerHandle(Protocol):
    registry: "VoiceRuntimeSessionRegistry"

    def _apply_updates_to_session(
        self,
        session: ManagedVoiceSession,
        updates: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    def get_session_snapshot_by_key(
        self,
        runtime_session_key: str,
    ) -> Dict[str, Any] | None: ...

    def apply_session_updates(
        self,
        runtime_session_key: str,
        updates: Dict[str, Any],
    ) -> Dict[str, Any] | None: ...

    async def abort_session(
        self,
        runtime_session_key: str,
        *,
        reason: str = "runtime_control_abort",
    ) -> Dict[str, Any] | None: ...

    async def stop_session_by_key(
        self,
        runtime_session_key: str,
        *,
        disconnect_transport: bool = False,
    ) -> bool: ...


_REGISTERED_RUNTIME_MANAGERS: weakref.WeakSet[Any] = weakref.WeakSet()


def register_runtime_manager(manager: VoiceRuntimeManagerHandle) -> None:
    _REGISTERED_RUNTIME_MANAGERS.add(manager)


class VoiceRuntimeSessionRegistry:
    def __init__(self) -> None:
        self.active_sessions: Dict[str, ManagedVoiceSession] = {}
        self._channel_index: Dict[tuple[str, int], str] = {}

    @staticmethod
    def channel_lookup_key(*, platform: str, channel_id: int) -> tuple[str, int]:
        return (str(platform or "").strip().lower() or "discord", int(channel_id))

    def index_session(self, session: ManagedVoiceSession) -> None:
        if session.runtime_session_key:
            self.active_sessions[session.runtime_session_key] = session
        self._channel_index[
            self.channel_lookup_key(
                platform=session.platform,
                channel_id=session.channel_id,
            )
        ] = session.runtime_session_key

    def remove_session(self, session: ManagedVoiceSession) -> None:
        if session.runtime_session_key:
            self.active_sessions.pop(session.runtime_session_key, None)
        self._channel_index.pop(
            self.channel_lookup_key(
                platform=session.platform,
                channel_id=session.channel_id,
            ),
            None,
        )

    def get_session(self, runtime_session_key: str) -> ManagedVoiceSession | None:
        return self.active_sessions.get(str(runtime_session_key or "").strip())

    def get_session_by_channel(
        self,
        channel_id: int,
        *,
        platform: str | None = None,
    ) -> ManagedVoiceSession | None:
        if platform:
            session_key = self._channel_index.get(
                self.channel_lookup_key(platform=platform, channel_id=channel_id)
            )
            return self.active_sessions.get(session_key or "")

        matching = [
            self.active_sessions[session_key]
            for key, session_key in self._channel_index.items()
            if key[1] == int(channel_id) and session_key in self.active_sessions
        ]
        if len(matching) == 1:
            return matching[0]
        return None

    def list_session_snapshots(self) -> list[Dict[str, Any]]:
        return [session.get_status_snapshot() for session in self.active_sessions.values()]

    def is_session_active(self, channel_id: int) -> bool:
        return self.get_session_by_channel(channel_id) is not None

    def get_session_count(self) -> int:
        return len(self.active_sessions)


def list_registered_voice_runtime_snapshots(
    owner_user_id: int | None = None,
) -> list[Dict[str, Any]]:
    snapshots: list[Dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for manager in list(_REGISTERED_RUNTIME_MANAGERS):
        for session in manager.registry.active_sessions.values():
            snapshot = session.get_status_snapshot()
            snapshot_owner_user_id = snapshot.get("owner_user_id")
            if owner_user_id is not None and int(snapshot_owner_user_id or 0) != int(
                owner_user_id
            ):
                continue
            key = (
                int(snapshot.get("channel_id") or 0),
                str(snapshot.get("platform") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            snapshots.append(snapshot)
    snapshots.sort(
        key=lambda item: (
            str(item.get("platform") or ""),
            int(item.get("channel_id") or 0),
        )
    )
    return snapshots


def apply_registered_voice_runtime_overrides(
    *,
    owner_user_id: int,
    platform: str,
    updates: Dict[str, Any],
) -> list[Dict[str, Any]]:
    normalized_platform = str(platform or "").strip().lower()
    applied: list[Dict[str, Any]] = []
    for manager in list(_REGISTERED_RUNTIME_MANAGERS):
        for session in manager.registry.active_sessions.values():
            if int(session.owner_user_id or 0) != int(owner_user_id):
                continue
            if str(session.platform or "").strip().lower() != normalized_platform:
                continue
            applied.append(manager._apply_updates_to_session(session, updates))
    return applied


def get_registered_voice_runtime_snapshot(
    runtime_session_key: str,
) -> Dict[str, Any] | None:
    clean_key = str(runtime_session_key or "").strip()
    if not clean_key:
        return None
    for manager in list(_REGISTERED_RUNTIME_MANAGERS):
        snapshot = manager.get_session_snapshot_by_key(clean_key)
        if snapshot is not None:
            return snapshot
    return None


def apply_registered_voice_session_overrides(
    *,
    runtime_session_key: str,
    updates: Dict[str, Any],
) -> Dict[str, Any] | None:
    clean_key = str(runtime_session_key or "").strip()
    if not clean_key:
        return None
    for manager in list(_REGISTERED_RUNTIME_MANAGERS):
        snapshot = manager.apply_session_updates(clean_key, updates)
        if snapshot is not None:
            return snapshot
    return None


async def abort_registered_voice_session(
    *,
    runtime_session_key: str,
    reason: str = "runtime_control_abort",
) -> Dict[str, Any] | None:
    clean_key = str(runtime_session_key or "").strip()
    if not clean_key:
        return None
    for manager in list(_REGISTERED_RUNTIME_MANAGERS):
        snapshot = await manager.abort_session(clean_key, reason=reason)
        if snapshot is not None:
            return snapshot
    return None


async def stop_registered_voice_session(
    *,
    runtime_session_key: str,
    disconnect_transport: bool = True,
) -> Dict[str, Any] | None:
    clean_key = str(runtime_session_key or "").strip()
    if not clean_key:
        return None
    for manager in list(_REGISTERED_RUNTIME_MANAGERS):
        snapshot = manager.get_session_snapshot_by_key(clean_key)
        if snapshot is None:
            continue
        success = await manager.stop_session_by_key(
            clean_key,
            disconnect_transport=disconnect_transport,
        )
        return snapshot if success else None
    return None
