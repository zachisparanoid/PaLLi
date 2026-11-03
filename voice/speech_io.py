"""
Session-local speech I/O and speaker-stream lifecycle helpers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from core.config import config
from voice.engine import VoiceEngine
from voice.types import VoiceTranscriptEvent

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")


# Step 31 (spec §7): the openai-realtime downgrade arms left with the
# bridge — classic is the engine and its own floor; engine errors
# propagate to the callers' existing containment.
EnsureVoiceEngineCallback = Callable[[], Awaitable[VoiceEngine]]
TranscriptEventCallback = Callable[[VoiceTranscriptEvent], Awaitable[None]]
StreamStateChangedCallback = Callable[[], None]
TraceCallback = Callable[..., None]


@dataclass
class SpeakerTurnStream:
    user_id: int
    username: str
    transcriber: Any
    last_activity: float


class VoiceSessionSpeechIO:
    """Owns engine-backed speech I/O plus live speaker-stream setup/cleanup."""

    def __init__(
        self,
        *,
        channel_id: int,
        ensure_voice_engine: EnsureVoiceEngineCallback,
        on_transcript_event: TranscriptEventCallback,
        on_stream_state_changed: StreamStateChangedCallback,
        trace: TraceCallback,
    ) -> None:
        self.channel_id = channel_id
        self._ensure_voice_engine = ensure_voice_engine
        self._on_transcript_event = on_transcript_event
        self._on_stream_state_changed = on_stream_state_changed
        self._trace = trace

        self.speaker_streams: Dict[int, SpeakerTurnStream] = {}
        self.pending_stream_tasks: Dict[int, asyncio.Task[SpeakerTurnStream]] = {}
        self.speaker_cooldowns: Dict[int, float] = {}
        self.last_cleanup_at = time.monotonic()

    def active_stream_count(self) -> int:
        return len(self.speaker_streams)

    def pending_stream_count(self) -> int:
        return sum(1 for task in self.pending_stream_tasks.values() if not task.done())

    def cooldown_count(self) -> int:
        now = time.monotonic()
        return sum(1 for retry_after in self.speaker_cooldowns.values() if retry_after > now)

    async def close_all_speaker_streams(self) -> None:
        for stream in list(self.speaker_streams.values()):
            try:
                await stream.transcriber.close()
            except Exception:
                logger.debug("Failed to close speaker stream for %s", stream.user_id, exc_info=True)
        for user_id, task in list(self.pending_stream_tasks.items()):
            if task.done():
                self.pending_stream_tasks.pop(user_id, None)
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Failed to close pending speaker stream task for %s", user_id, exc_info=True)
            finally:
                self.pending_stream_tasks.pop(user_id, None)
        self.speaker_streams.clear()
        self.speaker_cooldowns.clear()
        self._on_stream_state_changed()

    async def synthesize_transport_audio(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        engine = await self._ensure_voice_engine()
        return await engine.synthesize_speech(
            text=text,
            encoding=encoding,
            sample_rate=sample_rate,
            container=container,
        )

    async def transcribe_local_utterance(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> str:
        engine = await self._ensure_voice_engine()
        transcript = await engine.transcribe_linear16_utterance(
            audio_bytes,
            sample_rate=sample_rate,
            channels=channels,
        )
        return str(transcript or "").strip()

    async def ensure_speaker_stream(self, user_id: int, username: str) -> SpeakerTurnStream:
        stream = self.speaker_streams.get(user_id)
        if stream is not None:
            return stream

        retry_after = self.speaker_cooldowns.get(user_id, 0.0)
        if retry_after > time.monotonic():
            raise RuntimeError(f"live transcriber cooling down for {username}")

        pending = self.pending_stream_tasks.get(user_id)
        if pending is not None:
            return await pending

        async def _create_stream() -> SpeakerTurnStream:
            transcriber = None
            try:
                engine = await self._ensure_voice_engine()
                transcriber = await engine.create_live_transcriber(
                    user_id=user_id,
                    username=username,
                    on_event=self._on_transcript_event,
                )
                await transcriber.start()
                stream = SpeakerTurnStream(
                    user_id=user_id,
                    username=username,
                    transcriber=transcriber,
                    last_activity=time.monotonic(),
                )
                self.speaker_streams[user_id] = stream
                self.speaker_cooldowns.pop(user_id, None)
                self._on_stream_state_changed()
                essential_logger.info(
                    "🎤 Voice transcriber ready for %s in voice channel %s",
                    username,
                    self.channel_id,
                )
                self._trace(
                    "stream-ready",
                    user_id=user_id,
                    username=username,
                    transcriber_type=type(transcriber).__name__,
                    sample_rate=getattr(transcriber, "sample_rate", "?"),
                    channels=getattr(transcriber, "channels", "?"),
                )
                return stream
            except asyncio.CancelledError:
                if transcriber is not None:
                    try:
                        await transcriber.close()
                    except Exception:
                        logger.debug("Failed to close transcriber during stream-init cancel for %s", username, exc_info=True)
                raise
            except Exception:
                self.speaker_cooldowns[user_id] = time.monotonic() + 2.0
                if transcriber is not None:
                    try:
                        await transcriber.close()
                    except Exception:
                        logger.debug("Failed to close transcriber after startup error", exc_info=True)
                self._on_stream_state_changed()
                raise
            finally:
                self.pending_stream_tasks.pop(user_id, None)
                self._on_stream_state_changed()

        task = asyncio.create_task(_create_stream(), name=f"voice-stream-init-{self.channel_id}-{user_id}")
        self.pending_stream_tasks[user_id] = task
        self._on_stream_state_changed()
        return await task

    async def push_audio_frame(self, *, user_id: int, username: str, audio_bytes: bytes) -> SpeakerTurnStream:
        stream = await self.ensure_speaker_stream(user_id, username)
        stream.last_activity = time.monotonic()
        try:
            await stream.transcriber.push_audio(audio_bytes)
        except Exception:
            self.speaker_streams.pop(user_id, None)
            self.speaker_cooldowns[user_id] = time.monotonic() + 2.0
            try:
                await stream.transcriber.close()
            except Exception:
                logger.debug("Failed to close errored speaker stream", exc_info=True)
            self._on_stream_state_changed()
            raise
        return stream

    async def cleanup_inactive_streams(self) -> None:
        now = time.monotonic()
        if now - self.last_cleanup_at < 15.0:
            return
        self.last_cleanup_at = now
        timeout_seconds = float(getattr(config, "VOICE_INACTIVE_USER_CLEANUP_SECONDS", 120.0))
        stale = [
            user_id
            for user_id, stream in self.speaker_streams.items()
            if now - stream.last_activity > timeout_seconds
        ]
        changed = False
        for user_id in stale:
            stream = self.speaker_streams.pop(user_id, None)
            if stream is None:
                continue
            changed = True
            self.speaker_cooldowns.pop(user_id, None)
            try:
                await stream.transcriber.close()
            except Exception:
                logger.debug("Error closing stale speaker stream", exc_info=True)
        if changed:
            self._on_stream_state_changed()
