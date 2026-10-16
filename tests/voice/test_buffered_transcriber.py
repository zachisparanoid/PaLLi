from __future__ import annotations

import asyncio
import struct
from pathlib import Path
import wave

import pytest

from voice.buffered_transcriber import BufferedPCMTranscriber
from voice.speech import VoiceSpeechService
from voice.streaming_transcriber import DeepgramFluxTranscriber


def _pcm_frame(sample: int, *, samples: int = 400, channels: int = 2) -> bytes:
    values = [sample] * (samples * channels)
    return struct.pack(f"<{len(values)}h", *values)


@pytest.mark.asyncio
async def test_buffered_transcriber_emits_turn_after_silence():
    events = []

    async def _on_event(event):
        events.append(event)

    async def _transcribe_pcm(audio_bytes: bytes):
        return "Palli can you hear me"

    transcriber = BufferedPCMTranscriber(
        user_id=1,
        username="robin_example",
        on_event=_on_event,
        transcribe_pcm=_transcribe_pcm,
        silence_threshold=0.01,
        max_buffer_time=10.0,
        voice_threshold=50,
        min_speech_duration=0.0,
    )

    await transcriber.push_audio(_pcm_frame(1000))
    await transcriber.push_audio(_pcm_frame(0))
    await asyncio.sleep(0.1)

    assert [event.event for event in events] == ["speech_started", "end_of_turn"]
    assert events[-1].transcript == "Palli can you hear me"


@pytest.mark.asyncio
async def test_buffered_transcriber_discards_short_audio_without_transcribing():
    events = []
    transcribe_calls = 0

    async def _on_event(event):
        events.append(event)

    async def _transcribe_pcm(audio_bytes: bytes):
        nonlocal transcribe_calls
        transcribe_calls += 1
        return "should not happen"

    transcriber = BufferedPCMTranscriber(
        user_id=1,
        username="robin_example",
        on_event=_on_event,
        transcribe_pcm=_transcribe_pcm,
        sample_rate=48000,
        channels=2,
        silence_threshold=0.01,
        max_buffer_time=10.0,
        voice_threshold=50,
        min_speech_duration=0.2,
    )

    await transcriber.push_audio(_pcm_frame(1000, samples=100, channels=2))
    await transcriber.push_audio(_pcm_frame(0, samples=100, channels=2))
    await asyncio.sleep(0.1)

    assert transcribe_calls == 0
    assert events[-1].event == "end_of_turn"
    assert events[-1].transcript == ""


@pytest.mark.asyncio
async def test_buffered_transcriber_flushes_at_max_buffer_time():
    events = []

    async def _on_event(event):
        events.append(event)

    async def _transcribe_pcm(audio_bytes: bytes):
        return "max buffer flush"

    transcriber = BufferedPCMTranscriber(
        user_id=1,
        username="robin_example",
        on_event=_on_event,
        transcribe_pcm=_transcribe_pcm,
        sample_rate=100,
        channels=1,
        silence_threshold=1.0,
        max_buffer_time=0.05,
        voice_threshold=50,
        min_speech_duration=0.0,
    )

    await transcriber.push_audio(_pcm_frame(1000, samples=10, channels=1))

    assert [event.event for event in events] == ["speech_started", "end_of_turn"]
    assert events[-1].metadata["reason"] == "max_buffer"


@pytest.mark.asyncio
async def test_transcribe_discord_pcm_utterance_normalizes_before_stt(deepgram_stt, monkeypatch):
    service = VoiceSpeechService()
    ffmpeg_called = {"value": False}

    async def _fake_transcribe_file(path: str):
        assert path.endswith(".wav")
        assert Path(path).exists()
        with wave.open(path, "rb") as wav_file:
            assert wav_file.getframerate() == 16000
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
        return "normalized transcript"

    async def _fake_ffmpeg(args, *, timeout_seconds):
        ffmpeg_called["value"] = True
        output_path = args[args.index("-ar") + 2]
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x00" * 1600)

    monkeypatch.setattr("voice.speech.deepgram_client.transcribe_file", _fake_transcribe_file)
    monkeypatch.setattr("voice.speech.run_ffmpeg_checked", _fake_ffmpeg)

    result = await service.transcribe_discord_pcm_utterance(_pcm_frame(1000, samples=800))

    assert result == "normalized transcript"
    assert ffmpeg_called["value"] is True


@pytest.mark.asyncio
async def test_create_live_transcriber_uses_runtime_mode(deepgram_stt, monkeypatch):
    service = VoiceSpeechService()

    async def _on_event(event):
        return None

    monkeypatch.setattr("voice.speech.config.VOICE_RUNTIME_MODE", "buffered", raising=False)
    buffered = await service.create_live_transcriber(user_id=1, username="robin_example", on_event=_on_event)
    assert isinstance(buffered, BufferedPCMTranscriber)

    monkeypatch.setattr("voice.speech.config.VOICE_RUNTIME_MODE", "streaming", raising=False)
    streaming = await service.create_live_transcriber(user_id=1, username="robin_example", on_event=_on_event)
    assert isinstance(streaming, DeepgramFluxTranscriber)


@pytest.mark.asyncio
async def test_create_live_transcriber_buffered_forwards_sample_rate_and_channels(monkeypatch):
    service = VoiceSpeechService()
    calls = []

    async def _on_event(event):
        return None

    async def _fake_transcribe_linear16_utterance(audio_bytes: bytes, *, sample_rate: int, channels: int):
        calls.append(
            {
                "audio_bytes": audio_bytes,
                "sample_rate": sample_rate,
                "channels": channels,
            }
        )
        return "forwarded"

    monkeypatch.setattr("voice.speech.config.VOICE_RUNTIME_MODE", "buffered", raising=False)
    monkeypatch.setattr("voice.speech.config.VOICE_DEEPGRAM_STREAM_SAMPLE_RATE", 16000, raising=False)
    monkeypatch.setattr("voice.speech.config.VOICE_DEEPGRAM_STREAM_CHANNELS", 1, raising=False)
    monkeypatch.setattr(
        "voice.speech.voice_speech_service.transcribe_linear16_utterance",
        _fake_transcribe_linear16_utterance,
    )

    buffered = await service.create_live_transcriber(user_id=1, username="robin_example", on_event=_on_event)

    assert isinstance(buffered, BufferedPCMTranscriber)
    assert await buffered.transcribe_pcm(b"pcm-audio") == "forwarded"
    assert calls == [
        {
            "audio_bytes": b"pcm-audio",
            "sample_rate": 16000,
            "channels": 1,
        }
    ]


@pytest.mark.asyncio
async def test_synthesize_live_playback_file_requests_wav_container(monkeypatch):
    service = VoiceSpeechService()
    calls = []
    # E.5 Wave 4 step 4 (2026-05-13): TTS_PROVIDER moved to palli.yaml
    # voice.tts_provider. The YAML default is deepgram already, but we
    # pin it on the live snapshot so the test is hermetic.
    from core.settings_boot import settings_service as _settings
    from core.settings_schema import TTSProvider
    monkeypatch.setattr(_settings.snapshot().voice, "tts_provider", TTSProvider.DEEPGRAM, raising=True)

    async def _fake_synthesize_speech(*, text: str, encoding: str, sample_rate: int, container: str):
        calls.append(
            {
                "text": text,
                "encoding": encoding,
                "sample_rate": sample_rate,
                "container": container,
            }
        )
        return b"RIFFfake-wav"

    monkeypatch.setattr("voice.speech.deepgram_client.synthesize_speech", _fake_synthesize_speech)

    path = await service.synthesize_live_playback_file(text="hello there")

    assert path is not None
    assert Path(path).read_bytes() == b"RIFFfake-wav"
    assert calls == [
        {
            "text": "hello there",
            "encoding": "linear16",
            "sample_rate": 48000,
            "container": "wav",
        }
    ]
    Path(path).unlink()
