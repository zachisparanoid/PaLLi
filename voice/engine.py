"""
Session-level voice engine selection helpers.

Step 31 (2026-08-20, spec §7): the OpenAI realtime-bridge experiment is
DELETED — it was unreachable in the live tree (yaml `engine: classic` +
VOICE_OPENAI_REALTIME_ENABLE_DISCORD=false); git keeps the idea. Classic
is THE engine. The resolution/label machinery stays because the session
controller and status surfaces ride on it, and because a mistyped or
stale engine value must still collapse safely to classic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

from voice.speech import voice_speech_service

logger = logging.getLogger(__name__)

VOICE_ENGINE_CLASSIC = "classic"


def normalize_voice_engine(value: str | None) -> str:
    # Every value — including the deleted "openai_realtime_bridge" — lands
    # on classic. Silent floor, same class as _get_tts_provider's default.
    return VOICE_ENGINE_CLASSIC


def voice_engine_label(engine_name: str) -> str:
    return "Classic"


@dataclass(slots=True)
class VoiceEngineResolution:
    requested: str
    effective: str
    fallback_active: bool = False
    fallback_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "effective": self.effective,
            "fallback_active": self.fallback_active,
            "fallback_reason": self.fallback_reason,
            "requested_label": voice_engine_label(self.requested),
            "effective_label": voice_engine_label(self.effective),
        }


def resolve_voice_engine(requested_engine: str | None, *, platform: str | None) -> VoiceEngineResolution:
    requested = normalize_voice_engine(requested_engine)
    return VoiceEngineResolution(requested=requested, effective=VOICE_ENGINE_CLASSIC)


class VoiceEngine(Protocol):
    async def create_live_transcriber(self, *, user_id: int, username: str, on_event):
        ...

    async def transcribe_linear16_utterance(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> Optional[str]:
        ...

    async def synthesize_speech(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        ...

    async def close(self) -> None:
        ...


class ClassicVoiceEngine:
    name = VOICE_ENGINE_CLASSIC

    async def create_live_transcriber(self, *, user_id: int, username: str, on_event):
        return await voice_speech_service.create_live_transcriber(
            user_id=user_id,
            username=username,
            on_event=on_event,
        )

    async def transcribe_linear16_utterance(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> Optional[str]:
        return await voice_speech_service.transcribe_linear16_utterance(
            audio_bytes,
            sample_rate=sample_rate,
            channels=channels,
        )

    async def synthesize_speech(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        return await voice_speech_service.synthesize_speech(
            text=text,
            encoding=encoding,
            sample_rate=sample_rate,
            container=container,
        )

    async def close(self) -> None:
        return None


def build_voice_engine(
    *,
    requested_engine: str | None,
    platform: str | None,
) -> tuple[VoiceEngineResolution, VoiceEngine]:
    resolution = resolve_voice_engine(requested_engine, platform=platform)
    return resolution, ClassicVoiceEngine()
