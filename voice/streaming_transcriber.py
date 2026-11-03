"""
Deepgram live streaming transcription helpers for the voice runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import aiohttp
from aiohttp import client_exceptions

from core.config import config
from voice.types import StreamingTranscriber, TranscriptCallback, VoiceTranscriptEvent

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")


class DeepgramFluxTranscriber(StreamingTranscriber):
    """Stream raw audio into Deepgram Flux and emit normalized transcript events."""

    def __init__(
        self,
        *,
        user_id: int,
        username: str,
        on_event: TranscriptCallback,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        channels: int = 2,
        chunk_ms: int = 80,
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.on_event = on_event
        self.encoding = str(encoding or "linear16").strip().lower()
        self.sample_rate = sample_rate
        self.channels = max(1, int(channels))
        self.chunk_ms = max(20, int(chunk_ms))

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._turn_index = 0
        self._last_transcript = ""
        self._endpoint = "wss://api.deepgram.com/v2/listen"
        self._buffer = bytearray()
        self._chunk_bytes = self._compute_chunk_bytes()
        self._sent_audio_messages = 0
        self._logged_payload_types: set[str] = set()

    def _compute_chunk_bytes(self) -> int:
        if self.encoding != "linear16":
            return 0
        bytes_per_sample = 2
        samples_per_chunk = int(self.sample_rate * (self.chunk_ms / 1000.0))
        return max(bytes_per_sample * self.channels, samples_per_chunk * self.channels * bytes_per_sample)

    def _build_connect_params(self) -> dict[str, str]:
        model = str(getattr(config, "VOICE_DEEPGRAM_STREAM_MODEL", "flux-general-en") or "flux-general-en").strip()
        is_flux = model.lower().startswith("flux")

        params = {
            "model": model,
            "encoding": self.encoding,
            "sample_rate": str(self.sample_rate),
        }
        if self.encoding == "linear16" and self.channels > 1:
            params["channels"] = str(self.channels)

        # Flux models use the conversational /v2/listen endpoint and do not need a separate language hint.
        if not is_flux:
            params["interim_results"] = "true"
            params["smart_format"] = "true"
            params["punctuate"] = "true"
            params["vad_events"] = "true"
            params["language"] = "en-US"
            self._endpoint = "wss://api.deepgram.com/v1/listen"
            if getattr(config, "VOICE_EOT_TIMEOUT_MS", 0):
                params["endpointing"] = str(getattr(config, "VOICE_EOT_TIMEOUT_MS", 1200))
            if getattr(config, "VOICE_EOT_THRESHOLD", 0.0) > 0:
                params["utterance_end_ms"] = str(int(float(getattr(config, "VOICE_EOT_THRESHOLD", 0.7)) * 1000))
        else:
            self._endpoint = "wss://api.deepgram.com/v2/listen"
            # Start with the minimal supported Flux handshake and add turn controls conservatively.
            if getattr(config, "VOICE_EOT_THRESHOLD", 0.0) > 0:
                params["eot_threshold"] = str(getattr(config, "VOICE_EOT_THRESHOLD", 0.7))
            if getattr(config, "VOICE_EAGER_EOT_THRESHOLD", 0.0) > 0:
                params["eager_eot_threshold"] = str(getattr(config, "VOICE_EAGER_EOT_THRESHOLD", 0.55))
            if getattr(config, "VOICE_EOT_TIMEOUT_MS", 0):
                params["eot_timeout_ms"] = str(getattr(config, "VOICE_EOT_TIMEOUT_MS", 1200))

        return params

    async def start(self) -> None:
        if self._closed or self._ws is not None:
            return

        if not config.DEEPGRAM_API_KEY:
            raise RuntimeError("DEEPGRAM_API_KEY is required for live voice transcription")

        params = self._build_connect_params()
        headers = {"Authorization": f"Token {config.DEEPGRAM_API_KEY}"}
        session = aiohttp.ClientSession(headers=headers)
        try:
            ws = await session.ws_connect(
                self._endpoint,
                params=params,
                heartbeat=15.0,
            )
        except client_exceptions.WSServerHandshakeError as exc:
            dg_error = ""
            if getattr(exc, "headers", None):
                dg_error = str(exc.headers.get("dg-error") or "").strip()
            if dg_error:
                logger.warning(
                    "Deepgram live handshake rejected for user %s: %s",
                    self.user_id,
                    dg_error,
                )
            await session.close()
            raise
        except Exception:
            await session.close()
            raise

        self._session = session
        self._ws = ws
        self._reader_task = asyncio.create_task(self._reader_loop(), name=f"voice-flux-reader-{self.user_id}")

    async def push_audio(self, audio_bytes: bytes) -> None:
        if self._closed or not audio_bytes:
            return
        if self._ws is None:
            await self.start()
        if self._ws is None:
            return
        if self._chunk_bytes <= 0:
            async with self._write_lock:
                await self._ws.send_bytes(audio_bytes)
            self._sent_audio_messages += 1
            if self._sent_audio_messages <= 3:
                essential_logger.info(
                    "🎤 Sent live audio chunk %s for %s to Deepgram (%s bytes, encoding=%s)",
                    self._sent_audio_messages,
                    self.username,
                    len(audio_bytes),
                    self.encoding,
                )
            return

        self._buffer.extend(audio_bytes)
        while len(self._buffer) >= self._chunk_bytes:
            chunk = bytes(self._buffer[: self._chunk_bytes])
            del self._buffer[: self._chunk_bytes]
            async with self._write_lock:
                await self._ws.send_bytes(chunk)
            self._sent_audio_messages += 1
            if self._sent_audio_messages <= 3:
                essential_logger.info(
                    "🎤 Sent live audio chunk %s for %s to Deepgram (%s bytes, encoding=%s)",
                    self._sent_audio_messages,
                    self.username,
                    len(chunk),
                    self.encoding,
                )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        ws = self._ws
        session = self._session
        reader_task = self._reader_task

        self._ws = None
        self._session = None
        self._reader_task = None

        try:
            if ws is not None:
                if self._buffer:
                    try:
                        async with self._write_lock:
                            await ws.send_bytes(bytes(self._buffer))
                    except Exception:
                        pass
                    self._buffer.clear()
                try:
                    await ws.send_json({"type": "CloseStream"})
                except Exception:
                    pass
                await ws.close()
        finally:
            if reader_task is not None:
                reader_task.cancel()
                try:
                    await reader_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            if session is not None:
                await session.close()

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except json.JSONDecodeError:
                        logger.warning("Invalid Deepgram WS payload for user %s", self.user_id)
                        continue
                    await self._handle_payload(payload)
                elif message.type in {aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Deepgram live reader loop failed for user %s", self.user_id)
            await self.on_event(
                VoiceTranscriptEvent(
                    event="error",
                    user_id=self.user_id,
                    username=self.username,
                    metadata={"error": str(exc)},
                )
            )

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        payload_type = str(payload.get("type") or payload.get("event") or "").strip()
        if payload_type and payload_type not in self._logged_payload_types:
            self._logged_payload_types.add(payload_type)
            essential_logger.info(
                "🎤 Deepgram payload for %s: type=%s keys=%s",
                self.username,
                payload_type,
                sorted(payload.keys()),
            )

        if payload_type in {"Metadata", "KeepAlive", "Open", "Close", "Connected"}:
            return

        if payload_type == "SpeechStarted":
            self._turn_index += 1
            self._last_transcript = ""
            await self.on_event(
                VoiceTranscriptEvent(
                    event="speech_started",
                    user_id=self.user_id,
                    username=self.username,
                    turn_index=self._turn_index,
                )
            )
            return

        if payload_type == "UtteranceEnd":
            transcript = self._last_transcript.strip()
            if transcript:
                await self.on_event(
                    VoiceTranscriptEvent(
                        event="end_of_turn",
                        user_id=self.user_id,
                        username=self.username,
                        transcript=transcript,
                        turn_index=self._turn_index,
                        is_final=True,
                    )
                )
            return

        if payload_type == "Results":
            channel = payload.get("channel") or {}
            alternatives = channel.get("alternatives") or []
            alt = alternatives[0] if alternatives else {}
            transcript = str(alt.get("transcript") or "").strip()
            if not transcript:
                return

            self._last_transcript = transcript
            is_final = bool(payload.get("speech_final") or payload.get("is_final"))
            event_name = "end_of_turn" if is_final else "update"
            await self.on_event(
                VoiceTranscriptEvent(
                    event=event_name,
                    user_id=self.user_id,
                    username=self.username,
                    transcript=transcript,
                    turn_index=self._turn_index,
                    is_final=is_final,
                    confidence=float(alt.get("confidence") or 0.0),
                    metadata={
                        "payload_type": payload_type,
                        "speech_final": bool(payload.get("speech_final")),
                        "is_final": bool(payload.get("is_final")),
                    },
                )
            )
            return

        if payload_type == "TurnInfo":
            event_name = str(payload.get("event") or "").strip()
            transcript = str(payload.get("transcript") or self._last_transcript or "").strip()
            if transcript:
                self._last_transcript = transcript

            essential_logger.info(
                "🎤 Deepgram TurnInfo for %s: event=%s transcript=%r turn_index=%s",
                self.username,
                event_name or "<empty>",
                transcript[:200],
                payload.get("turn_index"),
            )

            mapped_event = {
                "StartOfTurn": "speech_started",
                "Update": "update",
                "EagerEndOfTurn": "eager_end_of_turn",
                "TurnResumed": "turn_resumed",
                "EndOfTurn": "end_of_turn",
            }.get(event_name, event_name.lower() or "update")

            if mapped_event == "speech_started":
                self._turn_index += 1

            await self.on_event(
                VoiceTranscriptEvent(
                    event=mapped_event,
                    user_id=self.user_id,
                    username=self.username,
                    transcript=transcript,
                    turn_index=self._turn_index,
                    is_final=(mapped_event == "end_of_turn"),
                    metadata={"payload_type": payload_type, "event_name": event_name},
                )
            )
            return

        if payload_type == "Error":
            await self.on_event(
                VoiceTranscriptEvent(
                    event="error",
                    user_id=self.user_id,
                    username=self.username,
                    metadata={"error": payload.get("description") or payload},
                )
            )
            return

        if payload_type:
            essential_logger.warning(
                "⚠️ Unhandled Deepgram payload for %s: type=%s payload=%s",
                self.username,
                payload_type,
                payload,
            )
