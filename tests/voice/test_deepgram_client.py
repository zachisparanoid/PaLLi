from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from voice.deepgram_client import DeepgramClient
from voice.speech import VoiceSpeechService


class _DummyResponse:
    def __init__(self, payload: dict | None = None, *, status: int = 200):
        self.status = status
        self._payload = payload or {"results": {"channels": [{"alternatives": [{"transcript": "hello"}]}]}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _DummySession:
    def __init__(self):
        self.calls: list[dict] = []
        self.closed = False

    def post(self, url, *, params, data, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "data_size": len(data),
            }
        )
        return _DummyResponse()

    async def close(self):
        self.closed = True


class _FailingSession:
    def __init__(self, error: Exception):
        self.error = error
        self.calls: list[dict] = []
        self.closed = False

    def post(self, url, *, params, data, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "data_size": len(data),
            }
        )
        raise self.error

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_transcribe_audio_omits_raw_audio_params_for_wav(monkeypatch):
    client = DeepgramClient()
    client.api_key = "dg-test"
    session = _DummySession()
    client._get_session = lambda: asyncio.sleep(0, result=session)

    transcript = await client.transcribe_audio(b"wav-data", "audio/wav")

    assert transcript == "hello"
    params = session.calls[0]["params"]
    assert "encoding" not in params
    assert "sample_rate" not in params


@pytest.mark.asyncio
async def test_transcribe_audio_includes_raw_audio_params_when_needed(monkeypatch):
    client = DeepgramClient()
    client.api_key = "dg-test"
    session = _DummySession()
    client._get_session = lambda: asyncio.sleep(0, result=session)

    transcript = await client.transcribe_audio(
        b"pcm-data",
        "audio/l16",
        encoding="linear16",
        sample_rate=48000,
    )

    assert transcript == "hello"
    params = session.calls[0]["params"]
    assert params["encoding"] == "linear16"
    assert params["sample_rate"] == "48000"


@pytest.mark.asyncio
async def test_transcribe_audio_retries_once_on_retryable_error(monkeypatch):
    client = DeepgramClient()
    client.api_key = "dg-test"
    failing = _FailingSession(OSError(1236, "The network connection was aborted by the local system"))
    succeeding = _DummySession()
    sessions = iter([failing, succeeding])

    async def _fake_get_session():
        return next(sessions)

    client._get_session = _fake_get_session

    transcript = await client.transcribe_audio(b"wav-data", "audio/wav")

    assert transcript == "hello"
    assert len(failing.calls) == 1
    assert len(succeeding.calls) == 1


@pytest.mark.asyncio
async def test_transcribe_attachment_bytes_skips_ffmpeg_for_wav(deepgram_stt, monkeypatch, tmp_path: Path):
    service = VoiceSpeechService()
    called = {"ffmpeg": False, "path": None}

    async def _fake_transcribe_file(path: str):
        called["path"] = path
        return "do you read me"

    async def _fake_ffmpeg(*args, **kwargs):
        called["ffmpeg"] = True
        return None

    monkeypatch.setattr("voice.speech.deepgram_client.transcribe_file", _fake_transcribe_file)
    monkeypatch.setattr("voice.speech.run_ffmpeg_checked", _fake_ffmpeg)

    result = await service.transcribe_attachment_bytes(
        audio_bytes=b"RIFF....fake",
        filename="voice-test.wav",
    )

    assert result == "do you read me"
    assert called["ffmpeg"] is False
    assert called["path"] is not None
