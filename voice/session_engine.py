"""
Session-level voice engine state and lifecycle coordination.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from voice.engine import (
    VoiceEngine,
    VoiceEngineResolution,
    build_voice_engine,
    normalize_voice_engine,
    resolve_voice_engine,
)

logger = logging.getLogger(__name__)


StateChangedCallback = Callable[[], None]
CloseSpeakerStreamsCallback = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class VoiceSessionEngineState:
    requested: str
    effective: str
    fallback_active: bool = False
    fallback_reason: str = ""
    fallback_started_at: float | None = None
    last_transition_at: float | None = None
    last_transition_reason: str = ""
    transition_count: int = 0

    @classmethod
    def from_resolution(cls, resolution: VoiceEngineResolution) -> "VoiceSessionEngineState":
        return cls(
            requested=resolution.requested,
            effective=resolution.effective,
            fallback_active=bool(resolution.fallback_active),
            fallback_reason=str(resolution.fallback_reason or ""),
        )


class VoiceSessionEngineController:
    """Owns session-local voice engine selection, fallback state, and lifecycle."""

    def __init__(
        self,
        *,
        channel_id: int,
        platform: str,
        requested_engine: str | None,
        on_state_changed: StateChangedCallback,
        close_speaker_streams: CloseSpeakerStreamsCallback,
    ) -> None:
        self.channel_id = channel_id
        self.platform = str(platform or "discord")
        self._on_state_changed = on_state_changed
        self._close_speaker_streams = close_speaker_streams
        self._state = VoiceSessionEngineState.from_resolution(
            resolve_voice_engine(requested_engine, platform=self.platform)
        )
        if self._state.fallback_active:
            now = time.time()
            self._state.fallback_started_at = now
            self._state.last_transition_at = now
            self._state.last_transition_reason = str(self._state.fallback_reason or "initial_resolution")
            self._state.transition_count = 1
        self._engine_instance: VoiceEngine | None = None
        self._reset_task: asyncio.Task[None] | None = None

    @property
    def requested(self) -> str:
        return self._state.requested

    @property
    def effective(self) -> str:
        return self._state.effective

    @property
    def fallback_active(self) -> bool:
        return self._state.fallback_active

    @property
    def fallback_reason(self) -> str:
        return self._state.fallback_reason

    @property
    def fallback_started_at(self) -> float | None:
        return self._state.fallback_started_at

    @property
    def last_transition_at(self) -> float | None:
        return self._state.last_transition_at

    @property
    def last_transition_reason(self) -> str:
        return self._state.last_transition_reason

    @property
    def transition_count(self) -> int:
        return self._state.transition_count

    @property
    def reset_task(self) -> asyncio.Task[None] | None:
        return self._reset_task

    def _apply_state(
        self,
        *,
        requested: str | None = None,
        effective: str | None = None,
        fallback_active: bool | None = None,
        fallback_reason: str | None = None,
        transition_reason: str = "",
    ) -> None:
        next_requested = normalize_voice_engine(requested) if requested is not None else self._state.requested
        next_effective = normalize_voice_engine(effective) if effective is not None else self._state.effective
        next_fallback_active = bool(fallback_active) if fallback_active is not None else self._state.fallback_active
        next_fallback_reason = str(fallback_reason or "") if fallback_reason is not None else self._state.fallback_reason

        if (
            next_requested == self._state.requested
            and next_effective == self._state.effective
            and next_fallback_active == self._state.fallback_active
            and next_fallback_reason == self._state.fallback_reason
        ):
            return

        was_fallback_active = self._state.fallback_active
        changed_at = time.time()
        self._state.requested = next_requested
        self._state.effective = next_effective
        self._state.fallback_active = next_fallback_active
        self._state.fallback_reason = next_fallback_reason
        if next_fallback_active:
            if not was_fallback_active or self._state.fallback_started_at is None:
                self._state.fallback_started_at = changed_at
        else:
            self._state.fallback_started_at = None
        self._state.last_transition_at = changed_at
        self._state.last_transition_reason = str(
            transition_reason or next_fallback_reason or "voice_engine_state_updated"
        )
        self._state.transition_count += 1
        self._on_state_changed()

    def _apply_resolution(
        self,
        resolution: VoiceEngineResolution,
        *,
        transition_reason: str = "",
    ) -> None:
        self._apply_state(
            requested=resolution.requested,
            effective=resolution.effective,
            fallback_active=bool(resolution.fallback_active),
            fallback_reason=str(resolution.fallback_reason or ""),
            transition_reason=transition_reason or str(resolution.fallback_reason or ""),
        )

    def override_state(
        self,
        *,
        requested: str | None = None,
        effective: str | None = None,
        fallback_active: bool | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        self._apply_state(
            requested=requested,
            effective=effective,
            fallback_active=fallback_active,
            fallback_reason=fallback_reason,
            transition_reason="manual_override",
        )

    def set_requested_engine(self, voice_engine: str) -> None:
        normalized = normalize_voice_engine(voice_engine)
        if (
            normalized == self._state.requested
            and self._engine_instance is not None
            and not self._state.fallback_active
        ):
            return

        self._apply_resolution(
            resolve_voice_engine(normalized, platform=self.platform),
            transition_reason="voice_engine_updated",
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        self.cancel_pending_reconfigure()
        self._reset_task = loop.create_task(
            self.reconfigure(reason="voice_engine_updated"),
            name=f"voice-engine-reset-{self.channel_id}",
        )

    def cancel_pending_reconfigure(self) -> None:
        if self._reset_task is not None and not self._reset_task.done():
            self._reset_task.cancel()

    async def ensure_engine(self, *, transition_reason: str = "") -> VoiceEngine:
        if self._engine_instance is not None:
            return self._engine_instance

        resolution, engine = build_voice_engine(
            requested_engine=self._state.requested,
            platform=self.platform,
        )
        self._engine_instance = engine
        self._apply_resolution(
            resolution,
            transition_reason=str(transition_reason or resolution.fallback_reason or "voice_engine_ready"),
        )

        if resolution.fallback_active:
            logger.warning(
                "Voice engine fallback on channel %s platform=%s requested=%s effective=%s reason=%s",
                self.channel_id,
                self.platform,
                resolution.requested,
                resolution.effective,
                resolution.fallback_reason,
            )
        else:
            logger.info(
                "Voice engine ready on channel %s platform=%s engine=%s",
                self.channel_id,
                self.platform,
                resolution.effective,
            )
        return engine

    async def close_engine(self) -> None:
        engine = self._engine_instance
        self._engine_instance = None
        if engine is None:
            return
        try:
            await engine.close()
        except Exception:
            logger.debug("Failed to close voice engine for channel %s", self.channel_id, exc_info=True)

    async def reconfigure(self, *, reason: str) -> None:
        try:
            await self._close_speaker_streams()
            await self.close_engine()
            await self.ensure_engine(transition_reason=str(reason or "voice_engine_reconfigured"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Voice engine reconfigure failed on channel %s: %s",
                self.channel_id,
                reason,
            )
        finally:
            if self._reset_task is asyncio.current_task():
                self._reset_task = None

    # Step 31 (spec §7): downgrade_to_classic left with the realtime bridge —
    # classic is the only engine, so there is nothing to downgrade FROM. The
    # controller keeps engine lifecycle (ensure/reconfigure/close) and the
    # state snapshot the status surfaces read.
