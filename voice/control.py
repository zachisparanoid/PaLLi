from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.runtime_control import owner_surface_key
from voice.runtime import (
    abort_registered_voice_session,
    apply_registered_voice_session_overrides,
    get_registered_voice_runtime_snapshot,
    list_registered_voice_runtime_snapshots,
    stop_registered_voice_session,
)


class VoiceSessionControlService:
    def list_sessions(self, *, owner_user_id: int | None = None) -> List[Dict[str, Any]]:
        return list_registered_voice_runtime_snapshots(
            owner_user_id=int(owner_user_id) if owner_user_id is not None else None
        )

    def get_session(self, *, runtime_session_key: str) -> Optional[Dict[str, Any]]:
        return get_registered_voice_runtime_snapshot(str(runtime_session_key or "").strip())

    def local_session_key(self, *, owner_user_id: int) -> str:
        return owner_surface_key(int(owner_user_id), "local", "voice")

    def apply_updates(self, *, runtime_session_key: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return apply_registered_voice_session_overrides(
            runtime_session_key=str(runtime_session_key or "").strip(),
            updates=dict(updates or {}),
        )

    async def abort(self, *, runtime_session_key: str, reason: str = "runtime_control_abort") -> Optional[Dict[str, Any]]:
        return await abort_registered_voice_session(
            runtime_session_key=str(runtime_session_key or "").strip(),
            reason=reason,
        )

    async def stop(self, *, runtime_session_key: str) -> Optional[Dict[str, Any]]:
        return await stop_registered_voice_session(
            runtime_session_key=str(runtime_session_key or "").strip(),
            disconnect_transport=True,
        )

    async def say_text(self, *, runtime_session_key: str, text: str) -> bool:
        """Her-hands (step 33 task 3): one line aloud, right now, in an
        active session — the same transport.speak_text path a reply's
        audio already takes (voice/runtime.py _transport_output_deliver).
        Session-scoped: False when the key doesn't resolve to a live one."""
        from voice.manager_registry import _REGISTERED_RUNTIME_MANAGERS

        key = str(runtime_session_key or "").strip()
        if not key:
            return False
        for manager in list(_REGISTERED_RUNTIME_MANAGERS):
            session = manager.get_session(key)
            if session is None:
                continue
            transport = getattr(session, "transport", None)
            if transport is None:
                return False
            try:
                await transport.speak_text(str(text or ""))
            except Exception:
                return False
            return True
        return False


voice_session_control_service = VoiceSessionControlService()
