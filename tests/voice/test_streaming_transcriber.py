from __future__ import annotations

from types import SimpleNamespace

import aiohttp
import pytest

from voice.streaming_transcriber import DeepgramFluxTranscriber


class _DummySession:
    def __init__(self, *, fail_with: Exception | None = None):
        self.fail_with = fail_with
        self.closed = False
        self.calls: list[tuple[str, dict, float]] = []
        self.websocket = _DummyWebSocket()

    async def ws_connect(self, endpoint: str, *, params: dict, heartbeat: float):
        self.calls.append((endpoint, params, heartbeat))
        if self.fail_with is not None:
            raise self.fail_with
        return self.websocket

    async def close(self):
        self.closed = True


class _DummyWebSocket:
    def __init__(self):
        self.sent_bytes: list[bytes] = []
        self.sent_json: list[dict] = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def send_bytes(self, payload: bytes):
        self.sent_bytes.append(payload)

    async def send_json(self, payload: dict):
        self.sent_json.append(payload)

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_flux_model_uses_v2_listen_endpoint(monkeypatch):
    sessions: list[_DummySession] = []

    def _factory(*args, **kwargs):
        session = _DummySession()
        sessions.append(session)
        return session

    monkeypatch.setattr("voice.streaming_transcriber.config.DEEPGRAM_API_KEY", "dg-test", raising=False)
    monkeypatch.setattr("voice.streaming_transcriber.config.VOICE_DEEPGRAM_STREAM_MODEL", "flux-general-en", raising=False)
    monkeypatch.setattr("voice.streaming_transcriber.aiohttp.ClientSession", _factory)

    async def _on_event(event):
        return None

    transcriber = DeepgramFluxTranscriber(user_id=1, username="tester", on_event=_on_event)
    await transcriber.start()

    endpoint, params, heartbeat = sessions[0].calls[0]
    assert endpoint == "wss://api.deepgram.com/v2/listen"
    assert params["model"] == "flux-general-en"
    assert params["encoding"] == "linear16"
    assert params["channels"] == "2"
    assert "language" not in params
    assert heartbeat == 15.0


@pytest.mark.asyncio
async def test_handshake_failure_closes_client_session(monkeypatch):
    sessions: list[_DummySession] = []

    def _factory(*args, **kwargs):
        session = _DummySession(fail_with=RuntimeError("bad handshake"))
        sessions.append(session)
        return session

    monkeypatch.setattr("voice.streaming_transcriber.config.DEEPGRAM_API_KEY", "dg-test", raising=False)
    monkeypatch.setattr("voice.streaming_transcriber.aiohttp.ClientSession", _factory)

    async def _on_event(event):
        return None

    transcriber = DeepgramFluxTranscriber(user_id=1, username="tester", on_event=_on_event)

    with pytest.raises(RuntimeError, match="bad handshake"):
        await transcriber.start()

    assert sessions[0].closed is True


@pytest.mark.asyncio
async def test_linear16_audio_is_buffered_into_chunk_size(monkeypatch):
    sessions: list[_DummySession] = []

    def _factory(*args, **kwargs):
        session = _DummySession()
        sessions.append(session)
        return session

    monkeypatch.setattr("voice.streaming_transcriber.config.DEEPGRAM_API_KEY", "dg-test", raising=False)
    monkeypatch.setattr("voice.streaming_transcriber.aiohttp.ClientSession", _factory)

    async def _on_event(event):
        return None

    transcriber = DeepgramFluxTranscriber(
        user_id=1,
        username="tester",
        on_event=_on_event,
        encoding="linear16",
        sample_rate=48000,
        channels=2,
        chunk_ms=80,
    )
    await transcriber.start()

    # 20 ms of 48k stereo linear16 PCM = 3,840 bytes. Four frames should flush one 80 ms chunk.
    frame = b"a" * 3840
    for _ in range(3):
        await transcriber.push_audio(frame)
    assert sessions[0].websocket.sent_bytes == []

    await transcriber.push_audio(frame)
    assert len(sessions[0].websocket.sent_bytes) == 1
    assert len(sessions[0].websocket.sent_bytes[0]) == 15360
