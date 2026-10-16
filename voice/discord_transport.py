"""
Discord adapter for the platform-neutral voice runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import tempfile
from typing import Optional

import discord
from discord.ext import voice_recv

from core.config import config
from core.voice_text_cleaner import clean_text_for_voice
from voice.types import VoiceConversationSessionProtocol, VoiceTransport

logger = logging.getLogger(__name__)
essential_logger = logging.getLogger("palli.essential")


class DiscordVoiceSink(voice_recv.AudioSink):
    """Adapter sink that forwards Discord audio frames into the runtime."""

    def __init__(self, session: VoiceConversationSessionProtocol, loop: asyncio.AbstractEventLoop):
        self.session = session
        self.loop = loop
        self._frame_counts: dict[int, int] = {}

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data):
        try:
            if not user or user.bot:
                return

            audio_bytes = getattr(data, "pcm", b"") or b""

            if not audio_bytes:
                if bool(getattr(config, "VOICE_TRACE_LOGGING_ENABLED", False)):
                    logger.info(
                        "voice_trace discord_sink user=%s event=empty-pcm-frame has_opus=%s",
                        getattr(user, "display_name", getattr(user, "name", "unknown")),
                        bool(getattr(data, "opus", b"") or getattr(getattr(data, "packet", None), "decrypted_data", b"")),
                    )
                return

            if bool(getattr(config, "VOICE_TRACE_LOGGING_ENABLED", False)):
                user_id = int(getattr(user, "id", 0) or 0)
                count = self._frame_counts.get(user_id, 0) + 1
                self._frame_counts[user_id] = count
                every = max(1, int(getattr(config, "VOICE_TRACE_FRAME_LOG_EVERY", 40) or 40))
                if count <= 3 or (count % every) == 0:
                    logger.info(
                        "voice_trace discord_sink user=%s frame=%s bytes=%s rms=%.1f",
                        getattr(user, "display_name", getattr(user, "name", "unknown")),
                        count,
                        len(audio_bytes),
                        self._calculate_rms(audio_bytes),
                    )

            future = asyncio.run_coroutine_threadsafe(
                self.session.on_audio_frame(user.id, getattr(user, "display_name", user.name), audio_bytes),
                self.loop,
            )
            future.add_done_callback(self._log_future_error)
        except Exception as exc:
            asyncio.run_coroutine_threadsafe(self.session.on_transport_error(exc), self.loop)

    def _log_future_error(self, future) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.exception("Voice sink frame handling failed", exc_info=exc)

    def cleanup(self):
        essential_logger.info("🎤 DiscordVoiceSink cleanup")

    @staticmethod
    def _calculate_rms(audio_bytes: bytes) -> float:
        usable = len(audio_bytes) - (len(audio_bytes) % 2)
        if usable <= 0:
            return 0.0
        total_sq = 0.0
        count = 0
        for (sample,) in struct.iter_unpack("<h", audio_bytes[:usable]):
            total_sq += float(sample * sample)
            count += 1
        if count <= 0:
            return 0.0
        return (total_sq / float(count)) ** 0.5


class DiscordVoiceTransport(VoiceTransport):
    """Discord-specific receive/playback implementation."""

    def __init__(self, *, voice_client: voice_recv.VoiceRecvClient, text_channel_id: int):
        self.voice_client = voice_client
        self.text_channel_id = text_channel_id
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._sink: Optional[DiscordVoiceSink] = None
        self._session: VoiceConversationSessionProtocol | None = None

    async def start(self, session: VoiceConversationSessionProtocol) -> None:
        self._loop = asyncio.get_running_loop()
        self._session = session
        self._sink = DiscordVoiceSink(session=session, loop=self._loop)
        if self.voice_client.is_listening():
            self.voice_client.stop_listening()
        self.voice_client.listen(self._sink)
        essential_logger.info("✅ Discord voice transport listening started")

    async def stop(self) -> None:
        if self.voice_client and hasattr(self.voice_client, "stop_listening"):
            try:
                self.voice_client.stop_listening()
            except Exception:
                logger.debug("Error stopping Discord voice listening", exc_info=True)
        await self.interrupt_playback()
        if self._sink and hasattr(self._sink, "cleanup"):
            self._sink.cleanup()
        self._sink = None
        self._session = None

    async def interrupt_playback(self) -> None:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()

    async def speak_text(self, text: str) -> None:
        cleaned_text = clean_text_for_voice(text)
        if self._session is None:
            raise RuntimeError("Discord voice session is not attached")
        audio_data = await self._session.synthesize_transport_audio(
            text=cleaned_text,
            encoding="linear16",
            sample_rate=48000,
            container="wav",
        )
        if not audio_data:
            raise RuntimeError("TTS synthesis failed for live playback")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(audio_data)
            wav_path = temp_file.name

        try:
            source = discord.FFmpegPCMAudio(wav_path)
            source = discord.PCMVolumeTransformer(source, volume=0.8)
            # Span (step 31): tell the session the moment audio actually
            # starts. getattr-guarded — a session without the hook is fine.
            note_playback = getattr(self._session, "_span_note_playback_start", None)
            if callable(note_playback):
                note_playback()
            self.voice_client.play(source)
            while self.voice_client.is_playing():
                await asyncio.sleep(0.1)
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    async def send_text_message(self, text: str) -> None:
        channel = self.voice_client.guild.get_channel(self.text_channel_id) or self.voice_client.client.get_channel(self.text_channel_id)
        if channel is None:
            essential_logger.warning("⚠️ Could not resolve linked text channel %s for voice mirror", self.text_channel_id)
            return
        await channel.send(text)
