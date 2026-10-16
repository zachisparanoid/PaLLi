from __future__ import annotations

import pytest

from voice.speech import Pyttsx3TTSProvider, SpeechProvider, VoiceSpeechService


@pytest.mark.asyncio
async def test_transcribe_linear16_utterance_normalizes_then_transcribes(monkeypatch):
    calls = {}

    async def _fake_normalize(**kwargs):
        calls["normalize"] = kwargs
        return "/tmp/test.wav"

    async def _fake_transcribe(self, filepath: str):
        calls["transcribe"] = filepath
        return "hello there"

    monkeypatch.setattr(VoiceSpeechService, "normalize_linear16_to_wav", staticmethod(_fake_normalize))
    monkeypatch.setattr(SpeechProvider, "transcribe_file", _fake_transcribe)
    monkeypatch.setattr("voice.speech.os.path.exists", lambda path: False)

    provider = SpeechProvider()
    out = await provider.transcribe_linear16_utterance(
        b"\x00\x01" * 200,
        sample_rate=44100,
        channels=1,
    )

    assert out == "hello there"
    assert calls["normalize"]["input_sample_rate"] == 44100
    assert calls["normalize"]["input_channels"] == 1
    assert calls["transcribe"] == "/tmp/test.wav"


# (test_cartesia_tts_provider_wraps_raw_pcm_into_valid_wav retired 2026-08-20,
# step 31: CartesiaTTSProvider deleted with the whole Cartesia integration —
# owner's call, lemonfox is her provider of record. The WAV-transcode
# behavior it guarded lives on in test_lemonfox_tts_provider_transcodes_to_
# requested_wav below.)


@pytest.mark.asyncio
async def test_voice_speech_service_lists_local_providers():
    service = VoiceSpeechService()

    assert "faster_whisper" in service.list_stt_provider_names()
    assert "pyttsx3" in service.list_tts_provider_names()


@pytest.mark.asyncio
async def test_replace_provider_registry_closes_removed_provider_instances_once():
    service = VoiceSpeechService()

    class _FakeProvider:
        def __init__(self, name: str):
            self.name = name
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    removed_stt = _FakeProvider("removed_stt")
    shared_removed = _FakeProvider("shared_removed")
    kept = _FakeProvider("kept")
    replacement = _FakeProvider("replacement")

    service.set_provider_registry(
        stt_providers={
            "removed": removed_stt,
            "kept": kept,
            "shared": shared_removed,
        },
        tts_providers={
            "shared": shared_removed,
            "kept": kept,
        },
    )

    await service.replace_provider_registry(
        stt_providers={
            "kept": kept,
            "replacement": replacement,
        },
        tts_providers={
            "kept": kept,
            "replacement": replacement,
        },
    )

    assert removed_stt.close_calls == 1
    assert shared_removed.close_calls == 1
    assert kept.close_calls == 0
    assert replacement.close_calls == 0


@pytest.mark.asyncio
async def test_voice_speech_service_close_closes_current_registry_providers_once(monkeypatch):
    service = VoiceSpeechService()

    class _FakeProvider:
        def __init__(self, name: str):
            self.name = name
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    shared_provider = _FakeProvider("shared")
    service.set_provider_registry(
        stt_providers={"shared": shared_provider},
        tts_providers={"shared": shared_provider},
    )

    closed_clients: list[str] = []

    async def _fake_deepgram_close():
        closed_clients.append("deepgram")

    async def _fake_lemonfox_close():
        closed_clients.append("lemonfox")

    monkeypatch.setattr("voice.speech.deepgram_client.close", _fake_deepgram_close)
    monkeypatch.setattr("voice.speech.lemonfox_client.close", _fake_lemonfox_close)

    await service.close()

    assert shared_provider.close_calls == 1
    # Step 31: the ladder closes the module clients that remain after the
    # Cartesia cut — deepgram (STT + silent fallback) and lemonfox (her
    # provider of record; its aiohttp session was never closed before).
    assert closed_clients == ["deepgram", "lemonfox"]


@pytest.mark.asyncio
async def test_pyttsx3_provider_lists_voices(monkeypatch):
    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("voice.speech.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr(
        Pyttsx3TTSProvider,
        "_list_voices_sync",
        lambda self: [
            {"id": "voice-1", "name": "Voice One", "language": "en", "provider": "pyttsx3"},
            {"id": "voice-2", "name": "Voice Two", "language": "en", "provider": "pyttsx3"},
        ],
    )

    provider = Pyttsx3TTSProvider()
    voices = await provider.list_voices()

    assert voices == [
        {"id": "voice-1", "name": "Voice One", "language": "en", "provider": "pyttsx3"},
        {"id": "voice-2", "name": "Voice Two", "language": "en", "provider": "pyttsx3"},
    ]


# ── LemonfoxTTSProvider (2026-07-14 — Cartesia credit-outage fallback) ────


@pytest.mark.asyncio
async def test_lemonfox_tts_provider_transcodes_to_requested_wav(monkeypatch, tmp_path):
    """Lemonfox serves mp3 at its native rate; the playback path wants WAV
    linear16 at the requested rate — the provider must transcode locally."""
    import subprocess

    from voice.speech import LemonfoxTTSProvider, _build_default_tts_provider_registry

    src = tmp_path / "tone.mp3"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
            "-ar", "24000", "-ac", "1", str(src),
        ],
        check=True,
    )
    mp3_bytes = src.read_bytes()

    async def _fake_synthesize_speech(**kwargs):
        return mp3_bytes

    monkeypatch.setattr(
        "voice.speech.lemonfox_client.synthesize_speech", _fake_synthesize_speech
    )
    provider = LemonfoxTTSProvider()
    out = await provider.synthesize_speech(
        text="hello", encoding="linear16", sample_rate=48000, container="wav"
    )
    assert out is not None and out[:4] == b"RIFF"
    import io as _io
    import wave as _wave

    with _wave.open(_io.BytesIO(out), "rb") as wav:
        assert wav.getframerate() == 48000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
    assert "lemonfox" in _build_default_tts_provider_registry()


@pytest.mark.asyncio
async def test_lemonfox_tts_provider_none_on_client_failure(monkeypatch):
    from voice.speech import LemonfoxTTSProvider

    async def _fake_none(**kwargs):
        return None

    monkeypatch.setattr("voice.speech.lemonfox_client.synthesize_speech", _fake_none)
    assert await LemonfoxTTSProvider().synthesize_speech(text="hello") is None
