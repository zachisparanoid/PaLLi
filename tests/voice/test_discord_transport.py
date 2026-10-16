from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from voice.discord_transport import DiscordVoiceSink, DiscordVoiceTransport


class _DummySession:
    def __init__(self):
        self.frames = []
        self.errors = []

    async def on_audio_frame(self, user_id: int, username: str, audio_bytes: bytes) -> None:
        self.frames.append((user_id, username, audio_bytes))

    async def on_transport_error(self, error: Exception) -> None:
        self.errors.append(error)


@pytest.mark.asyncio
async def test_discord_voice_sink_requests_pcm_and_forwards_data_pcm():
    session = _DummySession()
    sink = DiscordVoiceSink(session=session, loop=asyncio.get_running_loop())

    user = SimpleNamespace(id=1, bot=False, display_name="robin_example", name="robin_example")
    data = SimpleNamespace(
        pcm=b"pcm-audio",
        opus=b"opus-audio",
        packet=SimpleNamespace(decrypted_data=b"raw-opus"),
    )

    assert sink.wants_opus() is False

    sink.write(user, data)
    await asyncio.sleep(0.01)

    assert session.errors == []
    assert session.frames == [(1, "robin_example", b"pcm-audio")]


@pytest.mark.asyncio
async def test_discord_voice_transport_plays_wav_without_extra_ffmpeg_flags(monkeypatch):
    created_sources = []
    seen_requests = []

    class _FakePCMVolumeTransformer:
        def __init__(self, source, volume=1.0):
            self.source = source
            self.volume = volume

    class _FakeVoiceClient:
        def __init__(self):
            self._playing = False

        def is_playing(self):
            current = self._playing
            self._playing = False
            return current

        def play(self, source):
            self._playing = True
            self.played_source = source

    def _fake_ffmpeg_pcm_audio(source, **kwargs):
        created_sources.append((source, kwargs))
        return SimpleNamespace(source=source, kwargs=kwargs)

    async def _fake_synthesize_transport_audio(*, text: str, encoding: str, sample_rate: int, container: str):
        assert text == "hello there"
        seen_requests.append(
            {
                "encoding": encoding,
                "sample_rate": sample_rate,
                "container": container,
            }
        )
        return b"RIFFmockwav"

    monkeypatch.setattr("voice.discord_transport.discord.FFmpegPCMAudio", _fake_ffmpeg_pcm_audio)
    monkeypatch.setattr("voice.discord_transport.discord.PCMVolumeTransformer", _FakePCMVolumeTransformer)

    voice_client = _FakeVoiceClient()
    transport = DiscordVoiceTransport(voice_client=voice_client, text_channel_id=1)
    transport._session = SimpleNamespace(synthesize_transport_audio=_fake_synthesize_transport_audio)

    await transport.speak_text("hello there")

    assert seen_requests == [{"encoding": "linear16", "sample_rate": 48000, "container": "wav"}]
    assert len(created_sources) == 1
    source, kwargs = created_sources[0]
    assert kwargs == {}
    assert str(source).endswith(".wav")
