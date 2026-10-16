"""
Buffered PCM transcription helpers for Discord voice ingestion.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Awaitable, Callable, Optional

from core.config import config
from voice.types import StreamingTranscriber, TranscriptCallback, VoiceTranscriptEvent

logger = logging.getLogger(__name__)

PCMTranscribeCallback = Callable[[bytes], Awaitable[Optional[str]]]


class BufferedPCMTranscriber(StreamingTranscriber):
    """Buffer PCM audio into utterances and transcribe after silence."""

    def __init__(
        self,
        *,
        user_id: int,
        username: str,
        on_event: TranscriptCallback,
        transcribe_pcm: PCMTranscribeCallback,
        sample_rate: int = 48000,
        channels: int = 2,
        silence_threshold: float = 2.5,
        max_buffer_time: float = 10.0,
        voice_threshold: int = 200,
        min_speech_duration: float = 0.3,
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.on_event = on_event
        self.transcribe_pcm = transcribe_pcm
        self.sample_rate = max(1, int(sample_rate))
        self.channels = max(1, int(channels))
        self.silence_threshold = max(0.05, float(silence_threshold))
        self.max_buffer_time = max(0.05, float(max_buffer_time))
        self.voice_threshold = max(1, int(voice_threshold))
        self.min_speech_duration = max(0.0, float(min_speech_duration))

        self._buffer = bytearray()
        self._closed = False
        self._speaking = False
        self._silence_started_at: float | None = None
        self._flush_task: asyncio.Task | None = None
        self._state_lock = asyncio.Lock()
        self._turn_index = 0
        self._trace_frame_count = 0

    async def start(self) -> None:
        return None

    async def push_audio(self, audio_bytes: bytes) -> None:
        if self._closed or not audio_bytes:
            return

        rms = self._calculate_rms(audio_bytes)
        is_voice_active = rms > self.voice_threshold
        emit_speech_started = False
        should_flush = False
        self._trace_frame_count += 1

        async with self._state_lock:
            if is_voice_active:
                if not self._speaking:
                    self._speaking = True
                    emit_speech_started = True
                    self._trace(
                        "speech-activated",
                        rms=round(rms, 1),
                        frame_bytes=len(audio_bytes),
                        threshold=self.voice_threshold,
                        turn_index=self._turn_index,
                    )
                self._silence_started_at = None
                self._cancel_flush_task_locked()
                self._buffer.extend(audio_bytes)
                should_flush = self._buffer_duration_seconds(len(self._buffer)) >= self.max_buffer_time
            elif self._speaking:
                if self._silence_started_at is None:
                    self._silence_started_at = time.monotonic()
                    self._schedule_flush_task_locked()
                    self._trace(
                        "silence-window-started",
                        rms=round(rms, 1),
                        silence_threshold=self.silence_threshold,
                        buffer_seconds=round(self._buffer_duration_seconds(len(self._buffer)), 3),
                        turn_index=self._turn_index,
                    )
                # Keep natural trailing silence like the archive implementation did.
                self._buffer.extend(audio_bytes)

            if self._should_trace_frame():
                self._trace(
                    "frame",
                    frame=self._trace_frame_count,
                    rms=round(rms, 1),
                    voice_active=is_voice_active,
                    speaking=self._speaking,
                    frame_bytes=len(audio_bytes),
                    buffer_seconds=round(self._buffer_duration_seconds(len(self._buffer)), 3),
                    turn_index=self._turn_index,
                )

        if emit_speech_started:
            await self.on_event(
                VoiceTranscriptEvent(
                    event="speech_started",
                    user_id=self.user_id,
                    username=self.username,
                    turn_index=self._turn_index,
                )
            )

        if should_flush:
            await self._flush_buffer(reason="max_buffer")

    async def close(self) -> None:
        self._closed = True
        async with self._state_lock:
            self._cancel_flush_task_locked()
            self._buffer.clear()
            self._speaking = False
            self._silence_started_at = None

    def _schedule_flush_task_locked(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(
            self._flush_after_silence(),
            name=f"voice-buffered-flush-{self.user_id}",
        )

    def _cancel_flush_task_locked(self) -> None:
        task = self._flush_task
        self._flush_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _flush_after_silence(self) -> None:
        try:
            await asyncio.sleep(self.silence_threshold)
            await self._flush_buffer(reason="silence")
        except asyncio.CancelledError:
            pass
        finally:
            if self._flush_task is asyncio.current_task():
                self._flush_task = None

    async def _flush_buffer(self, *, reason: str) -> None:
        if self._closed:
            return

        async with self._state_lock:
            if not self._buffer:
                self._speaking = False
                self._silence_started_at = None
                if self._flush_task is asyncio.current_task():
                    self._flush_task = None
                return

            audio_bytes = bytes(self._buffer)
            duration_seconds = self._buffer_duration_seconds(len(audio_bytes))
            buffer_bytes = len(audio_bytes)
            self._buffer.clear()
            self._speaking = False
            self._silence_started_at = None
            if self._flush_task is not asyncio.current_task():
                self._cancel_flush_task_locked()
            else:
                self._flush_task = None

        self._trace(
            "flush",
            reason=reason,
            duration_seconds=round(duration_seconds, 3),
            buffer_bytes=buffer_bytes,
            turn_index=self._turn_index,
        )

        transcript = ""
        if duration_seconds >= self.min_speech_duration:
            try:
                transcript = (await self.transcribe_pcm(audio_bytes) or "").strip()
            except Exception as exc:
                logger.warning(
                    "Buffered PCM transcription failed for %s: %s",
                    self.username,
                    exc,
                )
                await self.on_event(
                    VoiceTranscriptEvent(
                        event="error",
                        user_id=self.user_id,
                        username=self.username,
                        turn_index=self._turn_index,
                        metadata={"error": str(exc), "reason": reason},
                    )
                )
                transcript = ""
        else:
            self._trace(
                "skip-transcribe-short-audio",
                reason=reason,
                duration_seconds=round(duration_seconds, 3),
                min_speech_duration=self.min_speech_duration,
                turn_index=self._turn_index,
            )

        if not transcript:
            self._trace(
                "empty-transcript",
                reason=reason,
                duration_seconds=round(duration_seconds, 3),
                buffer_bytes=buffer_bytes,
                turn_index=self._turn_index,
            )
        else:
            self._trace(
                "transcript",
                reason=reason,
                duration_seconds=round(duration_seconds, 3),
                transcript_preview=transcript[:120],
                turn_index=self._turn_index,
            )

        self._turn_index += 1
        await self.on_event(
            VoiceTranscriptEvent(
                event="end_of_turn",
                user_id=self.user_id,
                username=self.username,
                transcript=transcript,
                turn_index=self._turn_index,
                is_final=True,
                metadata={
                    "reason": reason,
                    "duration_seconds": round(duration_seconds, 3),
                    "min_speech_duration": self.min_speech_duration,
                    "buffer_bytes": buffer_bytes,
                },
            )
        )

    def _buffer_duration_seconds(self, buffer_size: int) -> float:
        return float(buffer_size) / float(self.sample_rate * self.channels * 2)

    def _calculate_rms(self, audio_bytes: bytes) -> float:
        sample_bytes = len(audio_bytes) - (len(audio_bytes) % 2)
        if sample_bytes <= 0:
            return 0.0

        try:
            samples = struct.unpack(f"<{sample_bytes // 2}h", audio_bytes[:sample_bytes])
        except struct.error:
            return 0.0

        if not samples:
            return 0.0

        return (sum(sample * sample for sample in samples) / len(samples)) ** 0.5

    def _should_trace_frame(self) -> bool:
        if not bool(getattr(config, "VOICE_TRACE_LOGGING_ENABLED", False)):
            return False
        every = max(1, int(getattr(config, "VOICE_TRACE_FRAME_LOG_EVERY", 40) or 40))
        return self._trace_frame_count <= 3 or (self._trace_frame_count % every) == 0

    def _trace(self, event: str, **fields) -> None:
        if not bool(getattr(config, "VOICE_TRACE_LOGGING_ENABLED", False)):
            return
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        logger.info(
            "voice_trace buffered user=%s turn=%s event=%s %s",
            self.username,
            self._turn_index,
            event,
            details.strip(),
        )
