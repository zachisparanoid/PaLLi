"""
Session-local debug audio capture lifecycle helpers.
"""

from __future__ import annotations

import json
import logging
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from core.config import config

essential_logger = logging.getLogger("palli.essential")


@dataclass(slots=True)
class DebugAudioCapture:
    user_id: int
    username: str
    started_at_monotonic: float
    started_at_epoch: float
    last_audio_at_monotonic: float
    chunks: int
    buffer: bytearray


class VoiceSessionDebugCaptureRecorder:
    """Owns per-speaker debug audio buffering and file finalization."""

    def __init__(self, *, channel_id: int) -> None:
        self.channel_id = channel_id
        self._captures: dict[int, DebugAudioCapture] = {}

    async def record_audio(self, *, user_id: int, username: str, audio_bytes: bytes) -> None:
        if not bool(getattr(config, "VOICE_DEBUG_RECORD_INPUT_ENABLED", False)):
            return

        now_mono = time.monotonic()
        await self.flush_idle(force=False, reason="idle_boundary")

        capture = self._captures.get(user_id)
        idle_flush = float(getattr(config, "VOICE_DEBUG_RECORD_IDLE_FLUSH_SECONDS", 1.5) or 1.5)
        if capture is None or (now_mono - capture.last_audio_at_monotonic) > idle_flush:
            if capture is not None:
                await self._finalize_capture(capture, reason="speaker_gap")
            capture = DebugAudioCapture(
                user_id=user_id,
                username=username,
                started_at_monotonic=now_mono,
                started_at_epoch=time.time(),
                last_audio_at_monotonic=now_mono,
                chunks=0,
                buffer=bytearray(),
            )
            self._captures[user_id] = capture

        capture.buffer.extend(audio_bytes)
        capture.chunks += 1
        capture.last_audio_at_monotonic = now_mono

    async def flush_idle(self, *, force: bool, reason: str) -> None:
        if not bool(getattr(config, "VOICE_DEBUG_RECORD_INPUT_ENABLED", False)):
            return

        now_mono = time.monotonic()
        idle_flush = float(getattr(config, "VOICE_DEBUG_RECORD_IDLE_FLUSH_SECONDS", 1.5) or 1.5)
        to_flush: list[tuple[int, DebugAudioCapture]] = []
        for user_id, capture in list(self._captures.items()):
            if force or (now_mono - capture.last_audio_at_monotonic) >= idle_flush:
                to_flush.append((user_id, capture))

        for user_id, capture in to_flush:
            self._captures.pop(user_id, None)
            await self._finalize_capture(capture, reason=reason)

    def clear(self) -> None:
        self._captures.clear()

    async def _finalize_capture(self, capture: DebugAudioCapture, *, reason: str) -> None:
        duration_seconds = max(0.0, capture.last_audio_at_monotonic - capture.started_at_monotonic)
        min_seconds_raw = getattr(config, "VOICE_DEBUG_RECORD_MIN_SECONDS", 4.0)
        min_seconds = 4.0 if min_seconds_raw is None else float(min_seconds_raw)
        if duration_seconds < min_seconds or not capture.buffer:
            return

        output_dir = Path(
            str(
                getattr(
                    config,
                    "VOICE_DEBUG_RECORDINGS_DIR",
                    "logs/voice-debug-recordings",
                )
                or "logs/voice-debug-recordings"
            )
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_username = re.sub(r"[^A-Za-z0-9._-]+", "_", capture.username).strip("._-") or f"user_{capture.user_id}"
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(capture.started_at_epoch))
        base_path = output_dir / f"{safe_username}_{capture.user_id}_{timestamp}"

        sample_rate = int(getattr(config, "VOICE_DEEPGRAM_STREAM_SAMPLE_RATE", 48000) or 48000)
        channels = int(getattr(config, "VOICE_DEEPGRAM_STREAM_CHANNELS", 2) or 2)

        wav_path = base_path.with_suffix(".wav")
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(bytes(capture.buffer))
        saved_paths: dict[str, str] = {"audio": str(wav_path)}

        metadata_path = base_path.with_suffix(".json")
        metadata = {
            "user_id": capture.user_id,
            "username": capture.username,
            "channel_id": self.channel_id,
            "started_at_epoch": capture.started_at_epoch,
            "duration_seconds": round(duration_seconds, 3),
            "chunks": capture.chunks,
            "bytes": len(capture.buffer),
            "encoding": "linear16",
            "sample_rate": sample_rate,
            "channels": channels,
            "reason": reason,
            "saved_paths": saved_paths,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        essential_logger.info(
            "Saved voice debug capture for %s to %s (duration=%.2fs, bytes=%s)",
            capture.username,
            saved_paths.get("audio"),
            duration_seconds,
            len(capture.buffer),
        )
