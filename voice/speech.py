"""
Shared speech services used by live sessions and voice attachments.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
import io
import logging
import os
import tempfile
import threading
from pathlib import Path
import wave
from typing import Any, Optional, Protocol

from core.config import config
from core.ffmpeg import run_ffmpeg_checked
from voice.buffered_transcriber import BufferedPCMTranscriber
from voice.lemonfox_client import lemonfox_client
from voice.deepgram_client import deepgram_client
from voice.streaming_transcriber import DeepgramFluxTranscriber

logger = logging.getLogger(__name__)


class SpeechToTextProvider(Protocol):
    name: str

    async def create_live_transcriber(self, *, user_id: int, username: str, on_event):
        ...

    async def transcribe_file(self, filepath: str) -> Optional[str]:
        ...


class TextToSpeechProvider(Protocol):
    name: str

    async def synthesize_speech(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        ...

    async def list_voices(self) -> list[dict[str, str]]:
        ...


def _wrap_pcm_s16le_wav(
    audio_bytes: bytes,
    *,
    sample_rate: int,
    channels: int = 1,
) -> Optional[bytes]:
    if not audio_bytes:
        return None

    sample_width = 2
    normalized_channels = max(1, int(channels))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(normalized_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(max(1, int(sample_rate)))
        wav_file.writeframes(audio_bytes)
    return buffer.getvalue()


@dataclass(slots=True)
class SpeechVoiceOption:
    id: str
    name: str
    language: str = "en"
    provider: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": str(self.id),
            "name": str(self.name),
            "language": str(self.language or "en"),
            "provider": str(self.provider),
        }


@dataclass(slots=True)
class DeepgramSTTProvider:
    name: str = "deepgram"

    async def create_live_transcriber(self, *, user_id: int, username: str, on_event):
        runtime_mode = str(getattr(config, "VOICE_RUNTIME_MODE", "buffered") or "buffered").strip().lower()
        if runtime_mode == "buffered":
            sample_rate = int(getattr(config, "VOICE_DEEPGRAM_STREAM_SAMPLE_RATE", 48000))
            channels = int(getattr(config, "VOICE_DEEPGRAM_STREAM_CHANNELS", 2))

            async def _transcribe_buffered_pcm(audio_bytes: bytes) -> Optional[str]:
                return await voice_speech_service.transcribe_linear16_utterance(
                    audio_bytes,
                    sample_rate=sample_rate,
                    channels=channels,
                )

            return BufferedPCMTranscriber(
                user_id=user_id,
                username=username,
                on_event=on_event,
                transcribe_pcm=_transcribe_buffered_pcm,
                sample_rate=sample_rate,
                channels=channels,
                silence_threshold=float(getattr(config, "VOICE_SILENCE_THRESHOLD_SECONDS", 2.5)),
                max_buffer_time=float(getattr(config, "VOICE_MAX_BUFFER_TIME_SECONDS", 10.0)),
                voice_threshold=int(getattr(config, "VOICE_ACTIVITY_THRESHOLD", 200)),
                min_speech_duration=float(getattr(config, "VOICE_MIN_SPEECH_DURATION_SECONDS", 0.3)),
            )

        return DeepgramFluxTranscriber(
            user_id=user_id,
            username=username,
            on_event=on_event,
            encoding="linear16",
            sample_rate=int(getattr(config, "VOICE_DEEPGRAM_STREAM_SAMPLE_RATE", 48000)),
            channels=int(getattr(config, "VOICE_DEEPGRAM_STREAM_CHANNELS", 2)),
            chunk_ms=int(getattr(config, "VOICE_DEEPGRAM_STREAM_CHUNK_MS", 80)),
        )

    async def transcribe_file(self, filepath: str) -> Optional[str]:
        return await deepgram_client.transcribe_file(filepath)


@dataclass(slots=True)
class FasterWhisperSTTProvider:
    name: str = "faster_whisper"
    _model: Any = field(default=None, init=False, repr=False)
    _load_error: str | None = field(default=None, init=False, repr=False)

    async def _get_model(self):
        if self._model is not None:
            return self._model
        if self._load_error is not None:
            raise RuntimeError(self._load_error)
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            self._load_error = (
                "faster-whisper is not installed. Install it with `pip install faster-whisper` "
                "or switch STT_PROVIDER back to deepgram."
            )
            raise RuntimeError(self._load_error) from exc

        device = str(getattr(config, "VOICE_LOCAL_STT_DEVICE", "auto") or "auto").strip().lower()
        compute_type = str(getattr(config, "VOICE_LOCAL_STT_COMPUTE_TYPE", "") or "").strip().lower()
        if not compute_type:
            compute_type = "float16" if device in {"auto", "cuda"} else "int8"

        self._model = await asyncio.to_thread(
            WhisperModel,
            str(getattr(config, "VOICE_LOCAL_STT_MODEL", "small.en") or "small.en"),
            device=device,
            compute_type=compute_type,
        )
        return self._model

    async def create_live_transcriber(self, *, user_id: int, username: str, on_event):
        sample_rate = int(getattr(config, "VOICE_DEEPGRAM_STREAM_SAMPLE_RATE", 48000))
        channels = int(getattr(config, "VOICE_DEEPGRAM_STREAM_CHANNELS", 2))

        async def _transcribe_buffered_pcm(audio_bytes: bytes) -> Optional[str]:
            return await voice_speech_service.transcribe_linear16_utterance(
                audio_bytes,
                sample_rate=sample_rate,
                channels=channels,
            )

        return BufferedPCMTranscriber(
            user_id=user_id,
            username=username,
            on_event=on_event,
            transcribe_pcm=_transcribe_buffered_pcm,
            sample_rate=sample_rate,
            channels=channels,
            silence_threshold=float(getattr(config, "VOICE_SILENCE_THRESHOLD_SECONDS", 2.5)),
            max_buffer_time=float(getattr(config, "VOICE_MAX_BUFFER_TIME_SECONDS", 10.0)),
            voice_threshold=int(getattr(config, "VOICE_ACTIVITY_THRESHOLD", 200)),
            min_speech_duration=float(getattr(config, "VOICE_MIN_SPEECH_DURATION_SECONDS", 0.3)),
        )

    async def transcribe_file(self, filepath: str) -> Optional[str]:
        model = await self._get_model()

        def _run() -> Optional[str]:
            segments, _info = model.transcribe(
                filepath,
                beam_size=1,
                vad_filter=True,
                language="en",
            )
            text = " ".join(str(segment.text or "").strip() for segment in segments).strip()
            return text or None

        return await asyncio.to_thread(_run)


@dataclass(slots=True)
class DeepgramTTSProvider:
    name: str = "deepgram"

    async def synthesize_speech(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        return await deepgram_client.synthesize_speech(
            text=text,
            encoding=encoding,
            sample_rate=sample_rate,
            container=container,
        )

    async def list_voices(self) -> list[dict[str, str]]:
        model = str(getattr(config, "DEEPGRAM_TTS_MODEL", "aura-asteria-en") or "aura-asteria-en")
        return [SpeechVoiceOption(id=model, name=model, provider=self.name).to_dict()]


class SpeechProvider:
    """
    Backward-compatible combined provider boundary.

    New code should prefer explicit STT/TTS providers through VoiceSpeechService.
    """

    name = "deepgram"

    async def create_live_transcriber(self, *, user_id: int, username: str, on_event):
        return await DeepgramSTTProvider().create_live_transcriber(
            user_id=user_id,
            username=username,
            on_event=on_event,
        )

    async def transcribe_file(self, filepath: str) -> Optional[str]:
        return await deepgram_client.transcribe_file(filepath)

    async def synthesize_speech(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        return await deepgram_client.synthesize_speech(
            text=text,
            encoding=encoding,
            sample_rate=sample_rate,
            container=container,
        )

    async def transcribe_linear16_utterance(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int = 48000,
        channels: int = 2,
    ) -> Optional[str]:
        wav_path = None
        try:
            wav_path = await VoiceSpeechService.normalize_linear16_to_wav(
                audio_bytes=audio_bytes,
                input_sample_rate=sample_rate,
                input_channels=channels,
            )
            if not wav_path:
                return None
            return await self.transcribe_file(wav_path)
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass


# Step 31 (2026-08-20, owner's call): CartesiaTTSProvider deleted with the
# whole Cartesia integration — lemonfox is her provider of record; deepgram
# stays STT + the silent code-level TTS fallback. Git keeps the client.


@dataclass(slots=True)
class LemonfoxTTSProvider:
    name: str = "lemonfox"

    async def synthesize_speech(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        # Lemonfox (Kokoro stock voices) serves compressed audio at its
        # native rate with no server-side format knobs; the Discord playback
        # path wants WAV linear16 at the requested rate, so transcode
        # locally on the same ffmpeg spine the STT paths already use.
        source_audio = await lemonfox_client.synthesize_speech(text=text)
        if not source_audio:
            return None
        source_path: str | None = None
        wav_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
                handle.write(source_audio)
                source_path = handle.name
            wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(wav_fd)
            await run_ffmpeg_checked(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    source_path,
                    "-ar",
                    str(int(sample_rate)),
                    "-ac",
                    "1",
                    "-acodec",
                    "pcm_s16le",
                    wav_path,
                ]
            )
            with open(wav_path, "rb") as fh:
                return fh.read()
        except Exception as exc:
            logger.warning("lemonfox tts transcode failed: %s", exc)
            return None
        finally:
            for path in (source_path, wav_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    async def list_voices(self) -> list[dict[str, str]]:
        voice = str(getattr(config, "LEMONFOX_TTS_VOICE", "") or "sarah").strip()
        return [SpeechVoiceOption(id=voice, name=voice, provider=self.name).to_dict()]


class Pyttsx3TTSProvider:
    name = "pyttsx3"

    def __init__(self) -> None:
        self._lock = threading.Lock()

    async def synthesize_speech(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        return await asyncio.to_thread(
            self._synthesize_sync,
            text,
            encoding,
            sample_rate,
            container,
        )

    async def list_voices(self) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._list_voices_sync)

    def _synthesize_sync(
        self,
        text: str,
        encoding: str,
        sample_rate: int,
        container: str | None,
    ) -> Optional[bytes]:
        import pyttsx3

        with self._lock, tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            target = Path(handle.name)
        try:
            engine = pyttsx3.init()
            requested_voice = str(getattr(config, "VOICE_LOCAL_TTS_VOICE", "") or "").strip()
            if requested_voice:
                resolved = self._resolve_voice_id(engine.getProperty("voices"), requested_voice)
                if resolved is not None:
                    engine.setProperty("voice", resolved)
            engine.save_to_file(text, str(target))
            engine.runAndWait()

            if str(container or "wav").strip().lower() == "raw":
                with wave.open(str(target), "rb") as wav_file:
                    return wav_file.readframes(wav_file.getnframes())

            return target.read_bytes()
        finally:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass

    def _list_voices_sync(self) -> list[dict[str, str]]:
        import pyttsx3

        engine = pyttsx3.init()
        items = engine.getProperty("voices")
        voices: list[dict[str, str]] = []
        for item in items:
            voices.append(
                SpeechVoiceOption(
                    id=str(getattr(item, "id", "")),
                    name=str(getattr(item, "name", getattr(item, "id", "voice"))),
                    language="en",
                    provider=self.name,
                ).to_dict()
            )
        return voices

    @staticmethod
    def _resolve_voice_id(voices: list[object], requested_voice: str) -> str | None:
        requested = str(requested_voice or "").strip().casefold()
        if not requested:
            return None

        for item in voices:
            voice_id = str(getattr(item, "id", ""))
            voice_name = str(getattr(item, "name", ""))
            if requested == voice_id.casefold() or requested == voice_name.casefold():
                return voice_id

        for item in voices:
            voice_id = str(getattr(item, "id", ""))
            voice_name = str(getattr(item, "name", ""))
            if requested in voice_name.casefold() or requested in voice_id.casefold():
                return voice_id

        return None


def _voice_lane_off(field: str) -> bool:
    """Did boot turn this half of voice off for the run?

    `config.effective_stt_provider` / `effective_tts_provider` read "off" when
    the configured provider is deepgram and DEEPGRAM_API_KEY is missing (week 1
    task 1: that is a warning at boot now, not a refusal to start). Empty means
    validate() has not spoken, and nothing changes.
    """
    return str(getattr(config, field, "") or "").strip().lower() == "off"


def _build_default_stt_provider_registry() -> dict[str, SpeechToTextProvider]:
    return {
        "deepgram": DeepgramSTTProvider(),
        "faster_whisper": FasterWhisperSTTProvider(),
    }


def _build_default_tts_provider_registry() -> dict[str, TextToSpeechProvider]:
    return {
        "deepgram": DeepgramTTSProvider(),
        "lemonfox": LemonfoxTTSProvider(),
        "pyttsx3": Pyttsx3TTSProvider(),
    }


def _collect_replaced_provider_instances(
    *,
    previous_providers: list[object],
    next_providers: list[object],
) -> list[object]:
    next_provider_ids = {id(provider) for provider in next_providers}
    replaced: list[object] = []
    seen_provider_ids: set[int] = set()
    for provider in previous_providers:
        provider_id = id(provider)
        if provider_id in next_provider_ids or provider_id in seen_provider_ids:
            continue
        seen_provider_ids.add(provider_id)
        replaced.append(provider)
    return replaced


async def _close_provider_instances(providers: list[object]) -> None:
    seen_provider_ids: set[int] = set()
    for provider in providers:
        provider_id = id(provider)
        if provider_id in seen_provider_ids:
            continue
        seen_provider_ids.add(provider_id)
        close_method = getattr(provider, "close", None)
        if not callable(close_method):
            continue
        try:
            result = close_method()
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass


class VoiceSpeechService:
    """Shared speech helpers for live voice and attachment flows."""

    def __init__(self) -> None:
        self._stt_providers = _build_default_stt_provider_registry()
        self._tts_providers = _build_default_tts_provider_registry()

    def set_provider_registry(
        self,
        *,
        stt_providers: dict[str, SpeechToTextProvider],
        tts_providers: dict[str, TextToSpeechProvider],
    ) -> None:
        next_stt = dict(stt_providers or {})
        next_tts = dict(tts_providers or {})
        if not next_stt:
            next_stt = _build_default_stt_provider_registry()
        if not next_tts:
            next_tts = _build_default_tts_provider_registry()
        self._stt_providers = next_stt
        self._tts_providers = next_tts

    async def replace_provider_registry(
        self,
        *,
        stt_providers: dict[str, SpeechToTextProvider],
        tts_providers: dict[str, TextToSpeechProvider],
    ) -> None:
        previous_providers = [*self._stt_providers.values(), *self._tts_providers.values()]
        self.set_provider_registry(stt_providers=stt_providers, tts_providers=tts_providers)
        next_providers = [*self._stt_providers.values(), *self._tts_providers.values()]
        replaced_providers = _collect_replaced_provider_instances(
            previous_providers=previous_providers,
            next_providers=next_providers,
        )
        await _close_provider_instances(replaced_providers)

    def _get_stt_provider(self) -> SpeechToTextProvider:
        # E.5 Wave 4 step 4 (2026-05-13): STT_PROVIDER moved off
        # core.config.Config onto data/palli.yaml -> voice.stt_provider.
        try:
            import importlib
            _settings = importlib.import_module("core.settings_boot").settings_service
            provider_name = str(_settings.snapshot().voice.stt_provider.value or "deepgram").strip().lower()
        except Exception:
            provider_name = "deepgram"
        if provider_name in self._stt_providers:
            return self._stt_providers[provider_name]
        return next(iter(self._stt_providers.values()))

    def _get_tts_provider(self) -> TextToSpeechProvider:
        # E.5 Wave 4 step 4 (2026-05-13): TTS_PROVIDER moved off
        # core.config.Config onto data/palli.yaml -> voice.tts_provider.
        try:
            import importlib
            _settings = importlib.import_module("core.settings_boot").settings_service
            provider_name = str(_settings.snapshot().voice.tts_provider.value or "deepgram").strip().lower()
        except Exception:
            provider_name = "deepgram"
        if provider_name in self._tts_providers:
            return self._tts_providers[provider_name]
        return next(iter(self._tts_providers.values()))

    def get_runtime_status(self) -> dict[str, str]:
        stt_provider = self._get_stt_provider()
        tts_provider = self._get_tts_provider()
        return {
            "stt_provider": stt_provider.name,
            "tts_provider": tts_provider.name,
        }

    def list_stt_provider_names(self) -> list[str]:
        return sorted(self._stt_providers.keys())

    def list_tts_provider_names(self) -> list[str]:
        return sorted(self._tts_providers.keys())

    async def list_tts_voices(self, *, provider_name: str | None = None) -> list[dict[str, str]]:
        provider = self._tts_providers.get(str(provider_name or "").strip().lower()) if provider_name else self._get_tts_provider()
        if provider is None or not hasattr(provider, "list_voices"):
            return []
        voices = await provider.list_voices()
        return list(voices or [])

    async def create_live_transcriber(self, *, user_id: int, username: str, on_event):
        if _voice_lane_off("effective_stt_provider"):
            raise RuntimeError(
                "speech-to-text is off for this boot: the configured provider has "
                "no key (see the startup punch list)"
            )
        return await self._get_stt_provider().create_live_transcriber(
            user_id=user_id,
            username=username,
            on_event=on_event,
        )

    @staticmethod
    async def normalize_linear16_to_wav(
        *,
        audio_bytes: bytes,
        input_sample_rate: int = 48000,
        input_channels: int = 2,
        output_sample_rate: int = 16000,
        output_channels: int = 1,
    ) -> Optional[str]:
        if not audio_bytes:
            return None

        source_wav_path = None
        normalized_wav_path = None
        try:
            wav_bytes = _wrap_pcm_s16le_wav(
                audio_bytes,
                sample_rate=max(1, int(input_sample_rate)),
                channels=max(1, int(input_channels)),
            )
            if not wav_bytes:
                return None

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_file.write(wav_bytes)
                source_wav_path = temp_file.name

            normalized_sample_rate = max(1, int(output_sample_rate))
            normalized_channels = max(1, int(output_channels))
            if (
                max(1, int(input_sample_rate)) == normalized_sample_rate
                and max(1, int(input_channels)) == normalized_channels
            ):
                return source_wav_path

            normalized_wav_path = source_wav_path.rsplit(".", 1)[0] + ".dg.wav"
            try:
                await run_ffmpeg_checked(
                    [
                        "ffmpeg",
                        "-i",
                        source_wav_path,
                        "-ac",
                        str(normalized_channels),
                        "-ar",
                        str(normalized_sample_rate),
                        normalized_wav_path,
                        "-y",
                        "-loglevel",
                        "error",
                    ],
                    timeout_seconds=90.0,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to normalize linear16 audio to %s Hz / %s channel(s); falling back to source WAV: %s",
                    normalized_sample_rate,
                    normalized_channels,
                    exc,
                )
                return source_wav_path

            try:
                os.remove(source_wav_path)
            except OSError:
                pass
            source_wav_path = None
            return normalized_wav_path
        except Exception:
            for path in (source_wav_path, normalized_wav_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            raise

    async def transcribe_discord_pcm_utterance(self, audio_bytes: bytes) -> Optional[str]:
        return await self.transcribe_linear16_utterance(audio_bytes, sample_rate=48000, channels=2)

    async def transcribe_linear16_utterance(
        self,
        audio_bytes: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> Optional[str]:
        wav_path = None
        try:
            wav_path = await self.normalize_linear16_to_wav(
                audio_bytes=audio_bytes,
                input_sample_rate=sample_rate,
                input_channels=channels,
            )
            if not wav_path:
                return None
            return await self.transcribe_file(wav_path)
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    async def transcribe_attachment_bytes(
        self,
        *,
        audio_bytes: bytes,
        filename: str = "voice.ogg",
    ) -> Optional[str]:
        suffix = Path(filename).suffix or ".ogg"
        source_path = None
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_file.write(audio_bytes)
                source_path = temp_file.name

            if suffix.lower() == ".wav":
                return await self.transcribe_file(source_path)

            wav_path = source_path.rsplit(".", 1)[0] + ".dg.wav"
            await run_ffmpeg_checked(
                [
                    "ffmpeg",
                    "-i",
                    source_path,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    wav_path,
                    "-y",
                    "-loglevel",
                    "error",
                ],
                timeout_seconds=90.0,
            )

            return await self.transcribe_file(wav_path)
        finally:
            for path in (source_path, wav_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    async def transcribe_file(self, filepath: str) -> Optional[str]:
        if _voice_lane_off("effective_stt_provider"):
            return None
        return await self._get_stt_provider().transcribe_file(filepath)

    async def synthesize_speech(
        self,
        *,
        text: str,
        encoding: str = "linear16",
        sample_rate: int = 48000,
        container: str | None = None,
    ) -> Optional[bytes]:
        if _voice_lane_off("effective_tts_provider"):
            return None
        return await self._get_tts_provider().synthesize_speech(
            text=text,
            encoding=encoding,
            sample_rate=sample_rate,
            container=container,
        )

    async def synthesize_live_playback_file(self, *, text: str) -> Optional[str]:
        audio_data = await self.synthesize_speech(
            text=text,
            encoding="linear16",
            sample_rate=48000,
            container="wav",
        )
        if not audio_data:
            return None

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(audio_data)
            return temp_file.name

    async def synthesize_voice_attachment_file(self, *, text: str) -> Optional[str]:
        audio_data = await self.synthesize_speech(
            text=text,
            encoding="linear16",
            sample_rate=48000,
            container="wav",
        )
        if not audio_data:
            return None

        wav_path = None
        ogg_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_file.write(audio_data)
                wav_path = temp_file.name

            ogg_path = wav_path.replace(".wav", ".ogg")
            await run_ffmpeg_checked(
                [
                    "ffmpeg",
                    "-i",
                    wav_path,
                    "-c:a",
                    "libopus",
                    ogg_path,
                    "-y",
                    "-loglevel",
                    "error",
                ],
                timeout_seconds=90.0,
            )
            return ogg_path
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    async def close(self) -> None:
        await _close_provider_instances([*self._stt_providers.values(), *self._tts_providers.values()])
        # Step 31: lemonfox joined the ladder with the Cartesia cut — its
        # aiohttp session was never closed before it became the record provider.
        for closer in (deepgram_client.close, lemonfox_client.close):
            try:
                await closer()
            except Exception:
                pass


voice_speech_service = VoiceSpeechService()
